"""Fail-closed Personal Edition deployment-boundary validation."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Iterable, Literal


AccessMode = Literal["private_network", "password"]
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TRUSTED_PROXY_ENVELOPES = tuple(
    ipaddress.ip_network(value)
    for value in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
    )
)


@dataclass(frozen=True)
class DeploymentBoundary:
    access_mode: AccessMode
    host_bind: str
    allowed_hosts: tuple[str, ...]
    trusted_proxy_cidrs: tuple[str, ...]


def parse_boolean(value: str | bool, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no", ""}:
        return False
    raise RuntimeError(f"{name} must be a recognized boolean value")


def _parse_networks(values: str | Iterable[str], *, name: str) -> tuple[ipaddress._BaseNetwork, ...]:
    raw_items = values.split(",") if isinstance(values, str) else values
    networks: list[ipaddress._BaseNetwork] = []
    for raw in raw_items:
        text = str(raw).strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise RuntimeError(f"Invalid {name} entry: {text}") from exc
        if network not in networks:
            networks.append(network)
    return tuple(networks)


def _parse_address(value: str) -> ipaddress._BaseAddress | None:
    normalized = value.strip().strip("[]").split("%", 1)[0]
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return None
    return getattr(address, "ipv4_mapped", None) or address


def _address_allowed(
    address: ipaddress._BaseAddress,
    networks: Iterable[ipaddress._BaseNetwork],
) -> bool:
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def _normalize_hostname(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host or any(character.isspace() for character in host):
        raise RuntimeError("ALLOWED_HOSTS contains an empty or invalid host")
    if "*" in host or "/" in host or "://" in host or "@" in host:
        raise RuntimeError("ALLOWED_HOSTS must contain explicit host names")
    unwrapped = host.strip("[]")
    if _parse_address(unwrapped) is not None or unwrapped == "localhost":
        return unwrapped
    try:
        ascii_host = unwrapped.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RuntimeError("ALLOWED_HOSTS contains an invalid DNS host name") from exc
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or len(labels) < 2 or any(
        not _HOST_LABEL.fullmatch(label) for label in labels
    ):
        raise RuntimeError("ALLOWED_HOSTS contains an invalid DNS host name")
    return ascii_host


def normalize_allowed_hosts(host_bind: str, raw: str) -> tuple[str, ...]:
    hosts = {"localhost", "127.0.0.1", "::1"}
    normalized_bind = host_bind.strip().strip("[]").split("%", 1)[0].lower()
    if normalized_bind and normalized_bind not in {"0.0.0.0", "::"}:
        hosts.add(_normalize_hostname(normalized_bind))
    for item in raw.split(","):
        if item.strip():
            hosts.add(_normalize_hostname(item))
    return tuple(sorted(hosts))


def _explicit_dns_host_present(raw: str) -> bool:
    for item in raw.split(","):
        if not item.strip():
            continue
        host = _normalize_hostname(item)
        if host != "localhost" and _parse_address(host) is None:
            return True
    return False


def _trusted_proxy_network_allowed(network: ipaddress._BaseNetwork) -> bool:
    return any(
        network.version == envelope.version and network.subnet_of(envelope)
        for envelope in _TRUSTED_PROXY_ENVELOPES
    )


def validate_deployment_boundary(
    access_mode: str,
    host_bind: str,
    allowed_hosts: str,
    trust_proxy_headers: str | bool,
    trusted_proxy_cidrs: str | Iterable[str],
    *,
    allowed_private_cidrs: Iterable[str],
) -> DeploymentBoundary:
    """Validate the same access boundary for runtime, deploy and doctor."""

    if access_mode not in {"private_network", "password"}:
        raise RuntimeError("ACCESS_MODE must be private_network or password")
    mode: AccessMode = access_mode  # type: ignore[assignment]
    bind = host_bind.strip() or "127.0.0.1"
    proxy_enabled = parse_boolean(
        trust_proxy_headers,
        name="TRUST_PROXY_HEADERS",
    )
    private_networks = _parse_networks(
        allowed_private_cidrs,
        name="allowed_private_cidrs",
    )
    proxy_networks = _parse_networks(
        trusted_proxy_cidrs,
        name="TRUSTED_PROXY_CIDRS",
    )
    normalized_hosts = normalize_allowed_hosts(bind, allowed_hosts)

    if mode == "private_network":
        if proxy_enabled:
            raise RuntimeError(
                "private_network requires TRUST_PROXY_HEADERS=false; "
                "proxied deployments must use password mode"
            )
        normalized_bind = bind.strip().strip("[]").split("%", 1)[0]
        if normalized_bind.lower() != "localhost":
            address = _parse_address(normalized_bind)
            if (
                address is None
                or address.is_unspecified
                or not (
                    address.is_loopback
                    or _address_allowed(address, private_networks)
                )
            ):
                raise RuntimeError(
                    "private_network HOST_BIND must be localhost or an approved "
                    "private IP address"
                )
        for host in normalized_hosts:
            if host == "localhost":
                continue
            address = _parse_address(host)
            if address is None or not (
                address.is_loopback or _address_allowed(address, private_networks)
            ):
                raise RuntimeError(
                    "private_network ALLOWED_HOSTS accepts only localhost and "
                    "approved private IP literals"
                )
    else:
        explicit_hosts = [item for item in allowed_hosts.split(",") if item.strip()]
        if not explicit_hosts:
            raise RuntimeError("password mode requires explicit ALLOWED_HOSTS")
        if _explicit_dns_host_present(allowed_hosts) and not proxy_enabled:
            raise RuntimeError(
                "password mode with a DNS ALLOWED_HOSTS entry requires "
                "TRUST_PROXY_HEADERS=true and TRUSTED_PROXY_CIDRS"
            )
        if proxy_enabled:
            if not proxy_networks:
                raise RuntimeError(
                    "TRUST_PROXY_HEADERS=true requires TRUSTED_PROXY_CIDRS"
                )
            if any(not _trusted_proxy_network_allowed(item) for item in proxy_networks):
                raise RuntimeError(
                    "TRUSTED_PROXY_CIDRS must contain only the actual private "
                    "reverse-proxy networks"
                )

    return DeploymentBoundary(
        access_mode=mode,
        host_bind=bind,
        allowed_hosts=normalized_hosts,
        trusted_proxy_cidrs=tuple(str(item) for item in proxy_networks),
    )


__all__ = [
    "DeploymentBoundary",
    "normalize_allowed_hosts",
    "parse_boolean",
    "validate_deployment_boundary",
]
