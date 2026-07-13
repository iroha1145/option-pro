from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from datetime import datetime, timedelta, timezone

from fastapi import Request

from app.services.request_security import client_ip_from_scope, request_is_https

from .errors import CatalystError
from .focus_config import FocusContextSettings
from .repository import CatalystRepository
from .signing import EMPTY_BODY_SHA256, canonical_string


FOCUS_CONTEXT_PATH = "/api/integrations/macrolens/v1/focus-context"
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _in_networks(value: str, networks: tuple[ipaddress._BaseNetwork, ...]) -> bool:
    try:
        address = ipaddress.ip_address(value.strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    return any(address.version == network.version and address in network for network in networks)


def authenticate_focus_request(
    request: Request,
    *,
    settings: FocusContextSettings,
    repository: CatalystRepository,
    now: datetime | None = None,
) -> None:
    """Authenticate the dedicated server-to-server read credential."""

    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not settings.configured:
        raise CatalystError(
            "focus_capability_disabled",
            "MacroLens focus integration is not configured",
            retryable=False,
            counts_for_circuit=False,
        )
    trusted_proxies = settings.trusted_proxy_networks
    if not request_is_https(
        request.scope,
        enabled=bool(trusted_proxies),
        networks=trusted_proxies,
    ):
        raise CatalystError(
            "focus_https_required",
            "MacroLens focus integration requires HTTPS",
            retryable=False,
            counts_for_circuit=False,
        )
    client_ip = client_ip_from_scope(
        request.scope,
        enabled=bool(trusted_proxies),
        networks=trusted_proxies,
    )
    if not _in_networks(client_ip, settings.allowed_networks):
        raise CatalystError(
            "focus_source_forbidden",
            "MacroLens focus request source is not allowed",
            retryable=False,
            counts_for_circuit=False,
        )
    if request.scope.get("query_string"):
        raise CatalystError(
            "focus_query_rejected",
            "MacroLens focus endpoint does not accept query parameters",
            retryable=False,
            counts_for_circuit=False,
        )

    key_id = request.headers.get("X-Optix-Key-Id", "").strip()
    timestamp_text = request.headers.get("X-Optix-Timestamp", "").strip()
    nonce = request.headers.get("X-Optix-Nonce", "").strip()
    body_sha = request.headers.get("X-Optix-Content-SHA256", "").strip().lower()
    signature = request.headers.get("X-Optix-Signature", "").strip().lower()
    if (
        not hmac.compare_digest(key_id, settings.key_id)
        or not timestamp_text.isdigit()
        or not _NONCE_PATTERN.fullmatch(nonce)
        or not hmac.compare_digest(body_sha, EMPTY_BODY_SHA256)
        or not _HEX_64.fullmatch(signature)
    ):
        raise CatalystError(
            "focus_invalid_signature",
            "MacroLens focus request signature is invalid",
            retryable=False,
            counts_for_circuit=False,
        )
    try:
        timestamp = datetime.fromtimestamp(int(timestamp_text), timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise CatalystError(
            "focus_invalid_signature",
            "MacroLens focus request timestamp is invalid",
            retryable=False,
            counts_for_circuit=False,
        ) from exc
    if abs((observed - timestamp).total_seconds()) > settings.clock_skew_seconds:
        raise CatalystError(
            "focus_timestamp_expired",
            "MacroLens focus request timestamp is outside the allowed clock skew",
            retryable=False,
            counts_for_circuit=False,
        )

    message = canonical_string(
        method="GET",
        path=FOCUS_CONTEXT_PATH,
        query="",
        timestamp=timestamp_text,
        nonce=nonce,
        body_sha256=EMPTY_BODY_SHA256,
    ).encode("utf-8")
    secrets = [settings.secret.get_secret_value()]
    previous = settings.previous_secret.get_secret_value()
    if previous:
        secrets.append(previous)
    valid = any(
        hmac.compare_digest(
            signature,
            hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest(),
        )
        for secret in secrets
    )
    if not valid:
        raise CatalystError(
            "focus_invalid_signature",
            "MacroLens focus request signature is invalid",
            retryable=False,
            counts_for_circuit=False,
        )
    repository.consume_focus_nonce(
        key_id=key_id,
        nonce=nonce,
        expires_at=observed + timedelta(seconds=settings.nonce_ttl_seconds),
        now=observed,
    )

