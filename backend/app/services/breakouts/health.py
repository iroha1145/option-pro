"""Health evaluation for the standalone Breakout Radar worker."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.breakouts.repository import (
    BreakoutRepository,
    BreakoutRepositoryError,
    SchemaVersionError,
)


@dataclass(frozen=True)
class BreakoutHealth:
    status: str
    healthy: bool
    enabled: bool
    reason: str
    details: Mapping[str, Any]

    @property
    def exit_code(self) -> int:
        return 0 if self.healthy else 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "healthy": self.healthy,
            "enabled": self.enabled,
            "reason": self.reason,
            "details": dict(self.details),
        }


def _now_utc(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("healthcheck time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("stored heartbeat is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def check_breakout_health(
    settings: object,
    repository: BreakoutRepository | None = None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float | None = None,
) -> BreakoutHealth:
    """Evaluate only database/lease liveness; Provider degradation is non-fatal."""
    enabled = bool(getattr(settings, "enabled", False))
    if not enabled:
        return BreakoutHealth(
            status="disabled",
            healthy=True,
            enabled=False,
            reason="breakout_radar_disabled",
            details={},
        )

    if repository is None:
        repository = BreakoutRepository(
            Path(getattr(settings, "db_path")),
            read_only=True,
        )
    try:
        status = repository.status()
    except FileNotFoundError:
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="database_missing",
            details={"database": "unavailable"},
        )
    except (sqlite3.Error, SchemaVersionError, BreakoutRepositoryError, OSError, ValueError) as exc:
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="database_unavailable",
            details={"database": "unavailable", "error_type": type(exc).__name__},
        )

    worker = status.get("worker")
    if not isinstance(worker, Mapping) or not worker.get("heartbeat_at"):
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="worker_status_missing",
            details={"database": "active"},
        )
    current = _now_utc(now)
    try:
        heartbeat = _parse(str(worker["heartbeat_at"]))
    except (TypeError, ValueError):
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="worker_heartbeat_invalid",
            details={"database": "active"},
        )
    stale_after = float(
        stale_after_seconds
        if stale_after_seconds is not None
        else getattr(settings, "worker_health_stale_seconds", 120.0)
    )
    age = max(0.0, (current - heartbeat).total_seconds())
    worker_state = str(worker.get("status") or "unknown")
    fatal_state = worker_state in {"database_error", "lease_lost", "unhealthy"}
    if age > stale_after:
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="worker_heartbeat_stale",
            details={"database": "active", "heartbeat_age_seconds": age},
        )
    if fatal_state:
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason=f"worker_{worker_state}",
            details={"database": "active", "heartbeat_age_seconds": age},
        )

    provider_states = {
        str(item.get("status"))
        for item in status.get("provider_health", [])
        if isinstance(item, Mapping)
    }
    degraded = worker_state in {"degraded", "provider_error"} or bool(
        provider_states.intersection({"degraded", "stale", "unavailable"})
    )
    return BreakoutHealth(
        status="degraded" if degraded else "active",
        healthy=True,
        enabled=True,
        reason="provider_degraded" if degraded else "healthy",
        details={
            "database": "active",
            "worker_status": worker_state,
            "heartbeat_age_seconds": age,
            "provider_states": sorted(provider_states),
        },
    )


check_health = check_breakout_health


__all__ = ["BreakoutHealth", "check_breakout_health", "check_health"]
