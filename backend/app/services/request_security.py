"""Trusted-proxy aware client metadata parsing shared by API gateways."""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Iterable


_TRUTHY = {"1", "true", "yes"}
TRUST_PROXY_HEADERS = (
    os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in _TRUTHY
)


def parse_trusted_proxy_cidrs(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    networks: list[ipaddress._BaseNetwork] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError as exc:
            raise RuntimeError(f"Invalid TRUSTED_PROXY_CIDRS entry: {text}") from exc
    return tuple(networks)


TRUSTED_PROXY_NETWORKS = parse_trusted_proxy_cidrs(
    os.environ.get("TRUSTED_PROXY_CIDRS", "")
)


def _peer_ip(scope: dict[str, Any]) -> str:
    client = scope.get("client")
    return str(client[0]).strip() if client else "unknown"


def _parsed_ip(value: str) -> ipaddress._BaseAddress | None:
    text = value.strip().strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _is_trusted(value: str, networks: Iterable[ipaddress._BaseNetwork]) -> bool:
    address = _parsed_ip(value)
    return bool(
        address is not None
        and any(address.version == network.version and address in network for network in networks)
    )


def _header(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            try:
                return value.decode("latin-1")
            except (AttributeError, UnicodeDecodeError):
                return ""
    return ""


def proxy_headers_trusted(
    scope: dict[str, Any],
    *,
    enabled: bool = TRUST_PROXY_HEADERS,
    networks: Iterable[ipaddress._BaseNetwork] = TRUSTED_PROXY_NETWORKS,
) -> bool:
    return bool(enabled and _is_trusted(_peer_ip(scope), networks))


def client_ip_from_scope(
    scope: dict[str, Any],
    *,
    enabled: bool = TRUST_PROXY_HEADERS,
    networks: Iterable[ipaddress._BaseNetwork] = TRUSTED_PROXY_NETWORKS,
) -> str:
    peer = _peer_ip(scope)
    networks_tuple = tuple(networks)
    if not proxy_headers_trusted(scope, enabled=enabled, networks=networks_tuple):
        return peer

    chain = [item.strip() for item in _header(scope, b"x-forwarded-for").split(",")]
    valid_chain = [item for item in chain if _parsed_ip(item) is not None]
    for candidate in reversed(valid_chain):
        if not _is_trusted(candidate, networks_tuple):
            return candidate
    return valid_chain[0] if valid_chain else peer


def request_client_ip(request: Any) -> str:
    return client_ip_from_scope(request.scope)


def request_is_https(
    scope: dict[str, Any],
    *,
    enabled: bool = TRUST_PROXY_HEADERS,
    networks: Iterable[ipaddress._BaseNetwork] = TRUSTED_PROXY_NETWORKS,
) -> bool:
    if scope.get("scheme") == "https":
        return True
    if not proxy_headers_trusted(scope, enabled=enabled, networks=networks):
        return False
    return _header(scope, b"x-forwarded-proto").split(",", 1)[0].strip().lower() == "https"


__all__ = [
    "TRUSTED_PROXY_NETWORKS",
    "TRUST_PROXY_HEADERS",
    "client_ip_from_scope",
    "parse_trusted_proxy_cidrs",
    "proxy_headers_trusted",
    "request_client_ip",
    "request_is_https",
]
