from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from app.deployment_boundary import DeploymentBoundary, validate_deployment_boundary
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


def require_owner_access(request: Request) -> None:
    runtime = _runtime(request)
    if runtime.request_is_owner(request):
        request.state.owner_access = True
        return
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


def request_uses_https(request: Request) -> bool:
    return request_is_https(
        request.scope,
        enabled=TRUST_PROXY_HEADERS,
        networks=TRUSTED_PROXY_NETWORKS,
    )


def require_same_origin_json(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "json_required", "message": "JSON request required"},
        )
    origin = request.headers.get("origin", "").strip()
    host = request.headers.get("host", "").strip().lower()
    if not origin or not host or "," in host or any(character.isspace() for character in host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "same_origin_required", "message": "Same-origin request required"},
        )
    try:
        parsed = urlsplit(origin)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "same_origin_required", "message": "Same-origin request required"},
        ) from exc
    expected_scheme = "https" if request_uses_https(request) else "http"
    valid = bool(
        parsed.scheme == expected_scheme
        and parsed.netloc.lower() == host
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )
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


def require_same_origin_action(request: Request) -> None:
    if not getattr(request.state, "owner_access", False):
        require_owner_access(request)
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    require_same_origin_json(request)
