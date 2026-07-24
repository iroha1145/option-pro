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


@dataclass(frozen=True)
class BreakoutReadState:
    """Freshness of the immutable snapshot exposed by the read-only API."""

    status: str
    reason: str
    details: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
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


def _worker_runtime(
    settings: object,
    status: Mapping[str, Any],
    *,
    current: datetime,
    stale_after_seconds: float | None = None,
    session_hint: str | None = None,
) -> dict[str, Any]:
    worker = status.get("worker")
    if not isinstance(worker, Mapping) or not worker.get("heartbeat_at"):
        return {
            "issue": "worker_status_missing",
            "worker_status": "unknown",
            "heartbeat_age_seconds": None,
        }
    worker_state = str(worker.get("status") or "unknown")
    stored_details = (
        dict(worker.get("details") or {})
        if isinstance(worker.get("details"), Mapping)
        else {}
    )
    try:
        heartbeat = _parse(str(worker["heartbeat_at"]))
    except (TypeError, ValueError):
        return {
            "issue": "worker_heartbeat_invalid",
            "worker_status": worker_state,
            "heartbeat_age_seconds": None,
        }
    if stale_after_seconds is not None:
        stale_after = float(stale_after_seconds)
    else:
        # The worker row heartbeats only when a scheduled scan runs, and the
        # scan cadence is session-dependent (5/10/30 min). Between runs the
        # silence is by design, so liveness must allow one full session
        # interval plus the configured grace before declaring the heartbeat
        # stale — otherwise the read surface reports a "degraded worker" for
        # most of every healthy cycle. A genuinely dead worker is still
        # caught: the completed-snapshot check uses the same session-aware
        # bound and flags independently.
        session = str(
            stored_details.get("market_session") or session_hint or "closed"
        )
        stale_after = max(
            float(getattr(settings, "worker_health_stale_seconds", 120.0)),
            _snapshot_stale_after(settings, session),
        )
    age = max(0.0, (current - heartbeat).total_seconds())
    if age > stale_after:
        issue = "worker_heartbeat_stale"
    elif worker_state in {"database_error", "lease_lost", "unhealthy"}:
        issue = f"worker_{worker_state}"
    else:
        issue = None
    return {
        "issue": issue,
        "worker_status": worker_state,
        "heartbeat_age_seconds": age,
        "heartbeat_stale_after_seconds": stale_after,
        "runtime_reason": stored_details.get("runtime_reason"),
        "market_session": stored_details.get("market_session"),
        "next_session_at": stored_details.get("next_session_at"),
        "failure_domain": stored_details.get("failure_domain"),
        "provider_health_unchanged": stored_details.get(
            "provider_health_unchanged"
        ),
    }


def _provider_states(status: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("status"))
        for item in (status.get("provider_health") or [])
        if isinstance(item, Mapping)
    }


def _degraded_reason(
    worker: Mapping[str, Any],
    providers: set[str],
) -> str:
    worker_state = str(worker.get("worker_status") or "unknown")
    failure_domain = str(worker.get("failure_domain") or "").strip()
    if (
        worker_state == "provider_error"
        or failure_domain == "provider"
        or providers.intersection({"degraded", "stale", "unavailable"})
    ):
        return "provider_degraded"
    if worker_state == "degraded":
        return f"{failure_domain}_degraded" if failure_domain else "worker_degraded"
    return "runtime_degraded"


def _snapshot_stale_after(settings: object, session: str) -> float:
    normalized = str(session or "closed").lower()
    if normalized == "premarket":
        interval = float(getattr(settings, "scan_interval_premarket_seconds", 600.0))
    elif normalized == "regular":
        interval = float(getattr(settings, "scan_interval_regular_seconds", 300.0))
    else:
        interval = float(getattr(settings, "scan_interval_closed_seconds", 1800.0))
    grace = float(getattr(settings, "worker_health_stale_seconds", 120.0))
    return max(1.0, interval + grace)


def assess_breakout_read_state(
    settings: object,
    status: Mapping[str, Any],
    *,
    now: datetime | None = None,
    completed_snapshot: Mapping[str, Any] | None = None,
) -> BreakoutReadState:
    """Classify API data without hiding the last immutable completed snapshot.

    A dead/stalled worker or an overdue completed scan makes retained data
    ``stale``.  A fresh snapshot with a non-fatal worker/Provider problem is
    ``degraded``.  The worker healthcheck remains stricter and can still fail
    the worker container while this read surface continues serving old data.
    """

    current = _now_utc(now)
    database = status.get("database")
    if isinstance(database, Mapping) and database.get("status") not in {None, "active"}:
        return BreakoutReadState(
            status="unavailable",
            reason="database_unavailable",
            details={"database": str(database.get("status") or "unavailable")},
        )

    snapshot = completed_snapshot
    if snapshot is None:
        value = status.get("latest_completed_scan")
        snapshot = value if isinstance(value, Mapping) else None

    session_hint = (
        str(snapshot.get("session"))
        if isinstance(snapshot, Mapping) and snapshot.get("session")
        else None
    )
    worker = _worker_runtime(
        settings, status, current=current, session_hint=session_hint
    )
    providers = _provider_states(status)
    details: dict[str, Any] = {
        **worker,
        "provider_states": sorted(providers),
    }

    snapshot_issue: str | None = None
    if snapshot is None:
        snapshot_issue = "completed_snapshot_missing"
        details["snapshot_age_seconds"] = None
        details["snapshot_stale_after_seconds"] = None
    else:
        snapshot_time = (
            snapshot.get("published_at")
            or snapshot.get("completed_at")
            or snapshot.get("scheduled_at")
        )
        stale_after = _snapshot_stale_after(settings, str(snapshot.get("session") or "closed"))
        details["snapshot_stale_after_seconds"] = stale_after
        try:
            parsed = _parse(str(snapshot_time))
            snapshot_age = max(0.0, (current - parsed).total_seconds())
            details["snapshot_age_seconds"] = snapshot_age
            if snapshot_age > stale_after:
                snapshot_issue = "completed_snapshot_stale"
        except (TypeError, ValueError):
            details["snapshot_age_seconds"] = None
            snapshot_issue = "completed_snapshot_time_invalid"

    worker_issue = worker.get("issue")
    if (
        worker.get("worker_status") == "paused"
        and worker.get("runtime_reason") == "market_closed"
        and worker_issue is None
    ):
        return BreakoutReadState("paused", "market_closed", details)
    if snapshot_issue in {"completed_snapshot_stale", "completed_snapshot_time_invalid"}:
        return BreakoutReadState("stale", snapshot_issue, details)
    if worker_issue == "worker_heartbeat_stale":
        return BreakoutReadState("stale", worker_issue, details)
    if "stale" in providers:
        return BreakoutReadState("stale", "provider_stale", details)
    if snapshot_issue == "completed_snapshot_missing":
        return BreakoutReadState("degraded", snapshot_issue, details)
    if worker_issue:
        return BreakoutReadState("degraded", str(worker_issue), details)
    worker_state = str(worker.get("worker_status") or "unknown")
    if worker_state in {"degraded", "provider_error"} or providers.intersection(
        {"degraded", "unavailable"}
    ):
        return BreakoutReadState(
            "degraded",
            _degraded_reason(worker, providers),
            details,
        )
    return BreakoutReadState("active", "fresh", details)


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

    current = _now_utc(now)
    # The container healthcheck stays intentionally stricter than the read
    # surface: it keeps the flat configured threshold instead of the
    # session-aware allowance used by assess_breakout_read_state.
    worker = _worker_runtime(
        settings,
        status,
        current=current,
        stale_after_seconds=(
            stale_after_seconds
            if stale_after_seconds is not None
            else float(getattr(settings, "worker_health_stale_seconds", 120.0))
        ),
    )
    worker_issue = worker.get("issue")
    if worker_issue == "worker_status_missing":
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="worker_status_missing",
            details={"database": "active"},
        )
    if worker_issue == "worker_heartbeat_invalid":
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="worker_heartbeat_invalid",
            details={"database": "active"},
        )
    age = float(worker.get("heartbeat_age_seconds") or 0.0)
    worker_state = str(worker.get("worker_status") or "unknown")
    if (
        worker_state == "paused"
        and worker.get("runtime_reason") == "market_closed"
        and worker_issue is None
    ):
        return BreakoutHealth(
            status="paused",
            healthy=True,
            enabled=True,
            reason="market_closed",
            details={
                "database": "active",
                "worker_status": worker_state,
                "heartbeat_age_seconds": age,
                "market_session": worker.get("market_session"),
                "next_session_at": worker.get("next_session_at"),
                "provider_states": sorted(_provider_states(status)),
            },
        )
    if worker_issue == "worker_heartbeat_stale":
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason="worker_heartbeat_stale",
            details={"database": "active", "heartbeat_age_seconds": age},
        )
    if worker_issue:
        return BreakoutHealth(
            status="unhealthy",
            healthy=False,
            enabled=True,
            reason=str(worker_issue),
            details={"database": "active", "heartbeat_age_seconds": age},
        )

    provider_states = _provider_states(status)
    degraded = worker_state in {"degraded", "provider_error"} or bool(
        provider_states.intersection({"degraded", "stale", "unavailable"})
    )
    reason = _degraded_reason(worker, provider_states) if degraded else "healthy"
    return BreakoutHealth(
        status="degraded" if degraded else "active",
        healthy=True,
        enabled=True,
        reason=reason,
        details={
            "database": "active",
            "worker_status": worker_state,
            "heartbeat_age_seconds": age,
            "provider_states": sorted(provider_states),
            "failure_domain": worker.get("failure_domain"),
            "provider_health_unchanged": worker.get(
                "provider_health_unchanged"
            ),
        },
    )


check_health = check_breakout_health


__all__ = [
    "BreakoutHealth",
    "BreakoutReadState",
    "assess_breakout_read_state",
    "check_breakout_health",
    "check_health",
]
