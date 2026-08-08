from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import lru_cache
from typing import AsyncIterator, Callable, Iterator, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from app.deployment_boundary import (
    DeploymentBoundary,
    canonicalize_hostname,
    validate_deployment_boundary,
)
from app.personal_config import AccessConfig, get_personal_config
from app.services.request_security import (
    TRUSTED_PROXY_NETWORKS,
    TRUST_PROXY_HEADERS,
    client_ip_from_scope,
    request_is_https,
)


OWNER_COOKIE_NAME = "optix_owner_session"
OWNER_SESSION_SECONDS = 12 * 60 * 60
_PBKDF2_ITERATIONS = 600_000
_PASSWORD_HASH_LENGTH = 32
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
_LOGIN_COOLDOWN_SECONDS = 30
_LOGIN_FAILURE_BUCKET_LIMIT = 256
_DEFAULT_ORIGIN_PORTS = {"http": 80, "https": 443}
_REQUEST_OWNER_ACCESS: ContextVar[bool | None] = ContextVar(
    "optix_request_owner_access",
    default=None,
)
_PUBLIC_STOCK_PULL_PATH = re.compile(
    r"^/api/stocks/(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})/pull$",
    re.IGNORECASE,
)
_PUBLIC_EARNINGS_REPORT_PATH = re.compile(
    r"^/api/ai/earnings-impact/[A-Z0-9][A-Z0-9.\-^]{0,11}"
    r"/reports/\d{4}-\d{2}-\d{2}$",
    re.IGNORECASE,
)


@contextmanager
def request_owner_access_context(owner_access: bool) -> Iterator[None]:
    """Bind the gateway's owner decision to the current request task."""

    token: Token[bool | None] = _REQUEST_OWNER_ACCESS.set(bool(owner_access))
    try:
        yield
    finally:
        _REQUEST_OWNER_ACCESS.reset(token)


def current_request_is_owner() -> bool:
    """Return the gateway decision, preserving direct-call test behavior."""

    owner_access = _REQUEST_OWNER_ACCESS.get()
    return True if owner_access is None else owner_access


def public_snapshot_unavailable(resource: str) -> HTTPException:
    """Build the stable response used when a visitor has no saved snapshot."""

    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "public_snapshot_unavailable",
            "message": f"Saved public snapshot is unavailable for {resource}",
        },
    )


def is_public_stock_pull_path(path: str) -> bool:
    """Match only the bounded, same-origin stock refresh action."""

    return _PUBLIC_STOCK_PULL_PATH.fullmatch(path) is not None


def is_public_earnings_impact_action_path(path: str) -> bool:
    return _PUBLIC_EARNINGS_REPORT_PATH.fullmatch(path) is not None


def is_public_earnings_ai_read_path(path: str) -> bool:
    return _PUBLIC_EARNINGS_REPORT_PATH.fullmatch(path) is not None


def _canonical_origin_host(
    value: str,
    *,
    require_ipv6: bool = False,
) -> str | None:
    hostname = canonicalize_hostname(value)
    if hostname is None:
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        if require_ipv6 and not isinstance(address, ipaddress.IPv6Address):
            return None
        return hostname
    if require_ipv6:
        return None
    labels = hostname.split(".")
    if len(hostname) > 253 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(not character.isalnum() and character != "-" for character in label)
        for label in labels
    ):
        return None
    return hostname


def _canonical_origin(value: str) -> tuple[str, str, int] | None:
    if (
        not value
        or value != value.strip()
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or any(character in value for character in "\\?#")
    ):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    bracketed = parsed.netloc.startswith("[")
    hostname = _canonical_origin_host(
        parsed.hostname or "",
        require_ipv6=bracketed,
    )
    if (
        scheme not in _DEFAULT_ORIGIN_PORTS
        or hostname is None
        or not parsed.netloc
        or (("[" in parsed.netloc or "]" in parsed.netloc) and not bracketed)
        or parsed.netloc.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port < 1)
    ):
        return None
    return (scheme, hostname, port or _DEFAULT_ORIGIN_PORTS[scheme])


def _canonical_request_origin(
    scheme: str,
    authority: str,
) -> tuple[str, str, int] | None:
    if (
        scheme not in _DEFAULT_ORIGIN_PORTS
        or not authority
        or authority != authority.strip()
        or authority.endswith(":")
        or any(
            ord(character) < 33
            or ord(character) == 127
            or character in "/\\?#@,%"
            for character in authority
        )
    ):
        return None
    try:
        parsed = urlsplit(f"//{authority}")
        port = parsed.port
    except ValueError:
        return None
    bracketed = parsed.netloc.startswith("[")
    hostname = _canonical_origin_host(
        parsed.hostname or "",
        require_ipv6=bracketed,
    )
    if (
        hostname is None
        or not parsed.netloc
        or (("[" in parsed.netloc or "]" in parsed.netloc) and not bracketed)
        or parsed.scheme
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and port < 1)
    ):
        return None
    return (scheme, hostname, port or _DEFAULT_ORIGIN_PORTS[scheme])


def canonical_request_host(authority: str) -> str | None:
    origin = _canonical_request_origin("http", authority)
    return origin[1] if origin is not None else None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_owner_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Owner password must contain at least 12 characters")
    if "\x00" in password or "\r" in password or "\n" in password:
        raise ValueError("Owner password contains unsupported control characters")
    resolved_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt,
        _PBKDF2_ITERATIONS,
        dklen=_PASSWORD_HASH_LENGTH,
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(_PBKDF2_ITERATIONS),
            _b64encode(resolved_salt),
            _b64encode(digest),
        )
    )


def verify_owner_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        if iterations != _PBKDF2_ITERATIONS:
            return False
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
        if len(salt) < 16 or len(expected) != _PASSWORD_HASH_LENGTH:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected),
        )
    except (binascii.Error, TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def owner_password_hash_is_valid(encoded_hash: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded_hash.split("$")
        return bool(
            algorithm == "pbkdf2_sha256"
            and int(raw_iterations) == _PBKDF2_ITERATIONS
            and len(_b64decode(raw_salt)) >= 16
            and len(_b64decode(raw_digest)) == _PASSWORD_HASH_LENGTH
        )
    except (binascii.Error, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    expires_at: float


class LoginRejected(RuntimeError):
    def __init__(self, code: str, *, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class OwnerAccessRuntime:
    def __init__(
        self,
        config: AccessConfig,
        *,
        password_hash: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.mode: Literal["private_network", "password"] = config.mode
        self.visitor_live_pulls = bool(config.visitor_live_pulls)
        self.visitor_ai_actions = bool(config.visitor_ai_actions)
        self._networks = tuple(
            ipaddress.ip_network(value, strict=False)
            for value in config.allowed_private_cidrs
        )
        self._password_hash = password_hash.strip()
        self._clock = clock
        self._lock = threading.Lock()
        self._session_digest: bytes | None = None
        self._session_expires_at = 0.0
        self._login_failures: dict[str, tuple[int, float, float]] = {}
        self._login_in_flight: set[str] = set()

    @property
    def password_configured(self) -> bool:
        return bool(self._password_hash)

    def validate_startup(
        self,
        host_bind: str,
        *,
        allowed_hosts: str = "",
        trust_proxy_headers: str | bool = False,
        trusted_proxy_cidrs: str = "",
    ) -> DeploymentBoundary:
        if self.mode == "password":
            if not self._password_hash or not self._valid_password_hash_shape():
                raise RuntimeError(
                    "ACCESS_MODE=password requires a valid APP_PASSWORD_HASH"
                )
        return validate_deployment_boundary(
            self.mode,
            host_bind,
            allowed_hosts,
            trust_proxy_headers,
            trusted_proxy_cidrs,
            allowed_private_cidrs=(str(network) for network in self._networks),
        )

    def _valid_password_hash_shape(self) -> bool:
        return owner_password_hash_is_valid(self._password_hash)

    def _address_allowed(
        self, address: ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> bool:
        return any(address.version == network.version and address in network for network in self._networks)

    def request_address(self, request: Request) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        raw = client_ip_from_scope(
            request.scope,
            enabled=TRUST_PROXY_HEADERS,
            networks=TRUSTED_PROXY_NETWORKS,
        )
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            return None
        mapped = getattr(address, "ipv4_mapped", None)
        return mapped or address

    def request_is_owner(self, request: Request) -> bool:
        if self.mode == "private_network":
            address = self.request_address(request)
            return address is not None and self._address_allowed(address)
        return self.session_valid(request.cookies.get(OWNER_COOKIE_NAME, ""))

    def session_valid(self, token: str) -> bool:
        if not token:
            return False
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        now = self._clock()
        with self._lock:
            if self._session_digest is None or self._session_expires_at <= now:
                self._session_digest = None
                self._session_expires_at = 0.0
                return False
            return hmac.compare_digest(digest, self._session_digest)

    def _prune_login_failures(self, now: float) -> None:
        stale_before = now - _LOGIN_FAILURE_WINDOW_SECONDS
        for key in [
            key
            for key, (_count, started_at, blocked_until) in self._login_failures.items()
            if started_at < stale_before and blocked_until <= now
        ]:
            self._login_failures.pop(key, None)
        if len(self._login_failures) >= _LOGIN_FAILURE_BUCKET_LIMIT:
            oldest = min(
                self._login_failures,
                key=lambda key: self._login_failures[key][1],
            )
            self._login_failures.pop(oldest, None)

    def login(self, password: str, *, client_key: str) -> LoginResult:
        if self.mode != "password":
            raise LoginRejected("login_not_required")
        now = self._clock()
        with self._lock:
            self._prune_login_failures(now)
            count, started_at, blocked_until = self._login_failures.get(
                client_key, (0, now, 0.0)
            )
            if blocked_until > now:
                raise LoginRejected(
                    "login_cooldown",
                    retry_after=max(1, int(blocked_until - now + 0.999)),
                )
            if client_key in self._login_in_flight:
                raise LoginRejected("login_cooldown", retry_after=1)
            self._login_in_flight.add(client_key)
        try:
            valid = verify_owner_password(password, self._password_hash)
        except BaseException:
            with self._lock:
                self._login_in_flight.discard(client_key)
            raise
        with self._lock:
            self._login_in_flight.discard(client_key)
            now = self._clock()
            if not valid:
                count, started_at, _blocked_until = self._login_failures.get(
                    client_key, (0, now, 0.0)
                )
                if started_at < now - _LOGIN_FAILURE_WINDOW_SECONDS:
                    count, started_at = 0, now
                count += 1
                blocked_until = (
                    now + _LOGIN_COOLDOWN_SECONDS
                    if count >= _LOGIN_FAILURE_LIMIT
                    else 0.0
                )
                self._login_failures[client_key] = (
                    count,
                    started_at,
                    blocked_until,
                )
                raise LoginRejected(
                    "invalid_owner_password",
                    retry_after=(
                        _LOGIN_COOLDOWN_SECONDS if blocked_until else None
                    ),
                )
            self._login_failures.pop(client_key, None)
            token = secrets.token_urlsafe(32)
            expires_at = now + OWNER_SESSION_SECONDS
            self._session_digest = hashlib.sha256(token.encode("utf-8")).digest()
            self._session_expires_at = expires_at
            return LoginResult(session_token=token, expires_at=expires_at)

    def logout(self, token: str) -> None:
        if not token:
            return
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        with self._lock:
            if self._session_digest is not None and hmac.compare_digest(
                digest, self._session_digest
            ):
                self._session_digest = None
                self._session_expires_at = 0.0


@lru_cache(maxsize=1)
def get_access_runtime() -> OwnerAccessRuntime:
    config = get_personal_config().access
    return OwnerAccessRuntime(
        config,
        password_hash=os.environ.get("APP_PASSWORD_HASH", ""),
    )


def _runtime(request: Request) -> OwnerAccessRuntime:
    runtime = getattr(request.app.state, "access_runtime", None)
    return runtime if isinstance(runtime, OwnerAccessRuntime) else get_access_runtime()


def request_allows_visitor_live_pulls(request: Request | None) -> bool:
    """Whether password-mode visitors may start bounded live provider work.

    Reads the app-injected runtime when the request carries one (HTTP path and
    tests that install ``app.state.access_runtime``), and falls back to the
    deployment config for bare, directly constructed requests.
    """

    runtime: OwnerAccessRuntime | None = None
    if request is not None:
        try:
            candidate = getattr(request.app.state, "access_runtime", None)
        except (AttributeError, KeyError):
            candidate = None
        if isinstance(candidate, OwnerAccessRuntime):
            runtime = candidate
    if runtime is None:
        return bool(get_personal_config().access.visitor_live_pulls)
    return runtime.visitor_live_pulls


def request_is_owner_session(request: Request) -> bool:
    """Boolean form of :func:`require_owner_access`.

    For routes that treat an owner session as one acceptable principal among
    several rather than a hard gate, so they can fall through to another check
    instead of raising. Shares the ``request.state`` memo with the gate below so
    both agree within a request and neither re-resolves the runtime.
    """

    if getattr(request.state, "owner_access", False) is True:
        return True
    if _runtime(request).request_is_owner(request):
        request.state.owner_access = True
        return True
    return False


def require_owner_access(request: Request) -> None:
    if request_is_owner_session(request):
        return
    runtime = _runtime(request)
    code = (
        "owner_login_required"
        if runtime.mode == "password"
        else "private_network_required"
    )
    raise HTTPException(
        status_code=(
            status.HTTP_401_UNAUTHORIZED
            if runtime.mode == "password"
            else status.HTTP_403_FORBIDDEN
        ),
        detail={"code": code, "message": "Owner access is required"},
    )


def request_account_session(request: Request):
    """登录客户（账号 cookie）的会话账号，解析失败一律按未登录（None）。

    手动拉取的准入与预算键控都从这里取账号：注册账号可拉、按 acct:{uid}
    限额；匿名只读快照。宽容 except 不只是防御——旧测试用 SimpleNamespace
    冒充 Request 直调路由函数（没有 .cookies），必须把它们保持在「匿名」
    语义而不是炸 AttributeError。
    账号面在 app.api.accounts（它反向依赖本模块），这里延迟导入避免环。
    """

    try:
        from app.api.accounts import current_account

        return current_account(request)
    except Exception:
        return None


def request_has_account_session(request: Request) -> bool:
    """登录客户会话是否有效（request_account_session 的布尔视图）。"""

    return request_account_session(request) is not None


async def require_public_read_or_owner_access(
    request: Request,
) -> AsyncIterator[None]:
    """Allow password-mode reads while keeping every other request owner-only."""

    runtime = _runtime(request)
    method = request.method.upper()
    public_batch_query = (
        method == "POST"
        and request.url.path == "/api/catalysts/tickers/batch"
    )
    is_stock_pull = (
        method == "POST" and is_public_stock_pull_path(request.url.path)
    )
    # 手动拉取：owner 与登录客户都可发起；visitor_live_pulls 开关额外放开匿名。
    public_stock_pull = is_stock_pull and (
        runtime.visitor_live_pulls or request_has_account_session(request)
    )
    if runtime.mode == "password" and (
        method in {"GET", "HEAD"} or public_batch_query or public_stock_pull
    ):
        owner_access = runtime.request_is_owner(request)
        request.state.owner_access = owner_access
        with request_owner_access_context(owner_access):
            yield
        return
    if (
        runtime.mode == "password"
        and is_stock_pull
        and not runtime.request_is_owner(request)
    ):
        # 到这里必是匿名（owner 与账号会话已在上面放行）：如实要求登录，
        # 不再抛统一的 owner 门——拉取本来就不是 owner 专属动作。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "account_login_required", "message": "请先登录"},
        )
    require_owner_access(request)
    with request_owner_access_context(True):
        yield


def request_uses_https(request: Request) -> bool:
    return request_is_https(
        request.scope,
        enabled=TRUST_PROXY_HEADERS,
        networks=TRUSTED_PROXY_NETWORKS,
    )


#: Content types a cross-origin HTML form can produce without a preflight.
_FORM_CONTENT_TYPES = frozenset(
    {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "text/plain",
    }
)


def require_same_origin_request(request: Request) -> None:
    """Same-origin proof without requiring a JSON body or owner access.

    Bodyless mutations (DELETE, sign-out) need the CSRF guard but have no
    content type to assert, and customer routes must not demand owner access
    the way :func:`require_same_origin_action` does.

    A body is still refused when it carries one of the simple form content
    types: those are precisely what a cross-origin ``<form>`` can send, so
    rejecting them keeps the same defence-in-depth as the JSON guard even
    though no body is expected here.
    """

    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type in _FORM_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "json_required", "message": "JSON request required"},
        )
    origins = request.headers.getlist("origin")
    hosts = request.headers.getlist("host")
    origin = origins[0] if len(origins) == 1 else ""
    host = hosts[0] if len(hosts) == 1 else ""
    expected_scheme = "https" if request_uses_https(request) else "http"
    origin_key = _canonical_origin(origin)
    request_key = _canonical_request_origin(expected_scheme, host)
    valid = bool(origin_key is not None and origin_key == request_key)
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        valid = False
    if request.headers.get("x-optix-action", "").strip() != "1":
        valid = False
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "same_origin_required", "message": "Same-origin request required"},
        )


def require_same_origin_json(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "json_required", "message": "JSON request required"},
        )
    require_same_origin_request(request)


def require_same_origin_action(request: Request) -> None:
    method = request.method.upper()
    path = request.url.path
    owner_access = bool(getattr(request.state, "owner_access", False))
    if (
        not owner_access
        and method in {"GET", "HEAD"}
        and is_public_earnings_ai_read_path(path)
    ):
        return
    if (
        not owner_access
        and method == "POST"
        and _runtime(request).visitor_ai_actions
        and is_public_earnings_impact_action_path(path)
    ):
        require_same_origin_json(request)
        return
    if not owner_access:
        require_owner_access(request)
    if method in {"GET", "HEAD", "OPTIONS"}:
        return
    require_same_origin_json(request)
