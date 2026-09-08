"""Freshness policy for published Strength Radar snapshots.

Saved file time is not a market timestamp. A rewrite of yesterday's bars is
still yesterday's bars. Missing clocks stay unknown; they are never filled
with the current request time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.market_calendar import (
    ET,
    early_close_minutes,
    last_completed_trading_day,
    previous_trading_day,
    trading_days_between,
)

# Vendor bars for the just-closed session can lag the official close.
VENDOR_BACKFILL_BUFFER = timedelta(hours=6)
# Beyond this many completed sessions the snapshot is history, not current.
HISTORICAL_MAX_TRADING_DAYS = 7
_FUTURE_SLACK = timedelta(minutes=5)


@dataclass(frozen=True)
class StrengthFreshness:
    stale: bool
    source_status: str
    stale_reason: str | None
    score_data_through: str | None
    expected_session: str
    input_lag_sessions: int
    unknown_input_time: bool


def parse_aware_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp or a YYYY-MM-DD session date.

    Date-only values are the NYSE regular (or early) close of that day, not
    UTC midnight. That keeps Tokyo and New York on the same trading date.
    """

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            session = date.fromisoformat(text)
        except ValueError:
            return None
        close_minutes = early_close_minutes(session) or 16 * 60
        return datetime(
            session.year,
            session.month,
            session.day,
            close_minutes // 60,
            close_minutes % 60,
            tzinfo=ET,
        ).astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def session_date_of(value: datetime) -> date:
    return value.astimezone(ET).date()


def expected_complete_session(now: datetime) -> date:
    """Most recent session the scanner should have complete daily bars for."""

    last = last_completed_trading_day(now)
    local = now.astimezone(ET)
    close_minutes = early_close_minutes(last) or 16 * 60
    closed_at = datetime(
        last.year,
        last.month,
        last.day,
        close_minutes // 60,
        close_minutes % 60,
        tzinfo=ET,
    )
    if local < closed_at + VENDOR_BACKFILL_BUFFER:
        return previous_trading_day(last)
    return last


def extract_score_data_through(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    direct = parse_aware_datetime(payload.get("score_data_through"))
    throughs: list[datetime] = [direct] if direct is not None else []
    rows = payload.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            parsed = parse_aware_datetime(row.get("daily_data_through"))
            if parsed is not None:
                throughs.append(parsed)
    if throughs:
        # A newer symbol (or an older aggregate computed with max()) cannot
        # certify that every displayed score used equally recent daily bars.
        return min(throughs)
    return None


def evaluate_strength_snapshot_freshness(
    *,
    saved_at: float,
    payload: Any,
    now: float,
    ttl_seconds: float,
    scoring_version: str | None = None,
    expected_scoring_version: str | None = None,
) -> StrengthFreshness:
    observed = datetime.fromtimestamp(now, tz=timezone.utc)
    expected = expected_complete_session(observed)
    ttl_expired = saved_at + float(ttl_seconds) <= now
    through = extract_score_data_through(payload)
    unknown = through is None
    # A clock in the future is not evidence; treat it as unverified.
    if through is not None and through > observed + _FUTURE_SLACK:
        through = None
        unknown = True
    input_lag = 0
    input_stale = False
    historical = False
    if through is not None:
        through_day = session_date_of(through)
        input_lag = trading_days_between(through_day, expected)
        input_stale = through_day < expected
        historical = input_lag > HISTORICAL_MAX_TRADING_DAYS
    version_mismatch = bool(
        expected_scoring_version
        and scoring_version
        and scoring_version != expected_scoring_version
    )
    unknown_version = bool(expected_scoring_version and not scoring_version)
    stale = bool(ttl_expired or input_stale or version_mismatch)
    if historical:
        status = "historical"
        reason = "score_data_too_old"
    elif version_mismatch:
        status = "stale"
        reason = "scoring_version_mismatch"
    elif input_stale:
        status = "stale"
        reason = "score_data_behind_session"
    elif ttl_expired:
        status = "stale"
        reason = "worker_snapshot_expired"
    elif unknown:
        status = "unknown"
        reason = "missing_score_data_through"
    elif unknown_version:
        status = "unknown"
        reason = "missing_scoring_version"
    else:
        status = "active"
        reason = None
    return StrengthFreshness(
        stale=stale,
        source_status=status,
        stale_reason=reason,
        score_data_through=through.isoformat() if through is not None else None,
        expected_session=expected.isoformat(),
        input_lag_sessions=input_lag,
        unknown_input_time=unknown,
    )


def strength_payload_is_publishable(payload: Any) -> tuple[bool, str | None]:
    """Reject total provider failure. Tight filters may still publish zero rows."""

    if not isinstance(payload, dict):
        return False, "invalid_payload"
    rows = payload.get("rows")
    results = payload.get("results")
    if not isinstance(rows, list) or not isinstance(results, list):
        return False, "invalid_payload"
    sources = payload.get("data_sources")
    prices: dict[str, Any] = {}
    if isinstance(sources, dict) and isinstance(sources.get("prices"), dict):
        prices = sources["prices"]
    status = str(prices.get("status") or "")
    if status in {"unavailable", "error", "failed"}:
        return False, "price_source_failed"
    skipped = payload.get("skipped") if isinstance(payload.get("skipped"), dict) else {}
    universe_count = payload.get("universe_count")
    data_errors = skipped.get("data_error") if isinstance(skipped, dict) else 0
    insufficient = skipped.get("insufficient_history") if isinstance(skipped, dict) else 0
    try:
        error_count = int(data_errors or 0) + int(insufficient or 0)
        pool = int(universe_count or 0)
    except (TypeError, ValueError):
        error_count = 0
        pool = 0
    if pool > 0 and len(rows) == 0 and error_count >= pool:
        return False, "empty_after_total_failure"
    return True, None


@dataclass(frozen=True)
class SnapshotReplaceDecision:
    """Whether a candidate may replace the published snapshot, and why not."""

    replace: bool
    reason: str | None
    keep_result: str | None


EXPLICIT_SNAPSHOT_DOWNGRADE_REASONS = frozenset(
    {
        "scoring_version_migration",
        "corporate_action_revision",
        "explicit_downgrade",
    }
)


def extract_scoring_version(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("score_version") or payload.get("scoring_version")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def extract_usable_input_coverage(payload: Any) -> int | None:
    """Count scored rows that still carry a known daily input clock."""

    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None
    dated = 0
    for row in rows:
        if isinstance(row, dict) and parse_aware_datetime(row.get("daily_data_through")):
            dated += 1
    return dated


def decide_published_snapshot_replacement(
    *,
    existing_saved_at: float | None,
    incoming_saved_at: float,
    existing_payload: Any = None,
    incoming_payload: Any = None,
    expected_scoring_version: str | None = None,
    allow_reason: str | None = None,
) -> SnapshotReplaceDecision:
    """Compare publish clocks, conservative input time, version, and coverage.

    A later file time is not enough to overwrite an earlier market session.
    Same-session recomputes with equal or better coverage may replace. Version
    migration or an explicit downgrade must pass ``allow_reason`` or land on
    the current expected scoring version.
    """

    def _keep(reason: str, keep_result: str) -> SnapshotReplaceDecision:
        if allow_reason in EXPLICIT_SNAPSHOT_DOWNGRADE_REASONS:
            return SnapshotReplaceDecision(
                replace=True,
                reason=allow_reason,
                keep_result=None,
            )
        return SnapshotReplaceDecision(
            replace=False,
            reason=reason,
            keep_result=keep_result,
        )

    if existing_saved_at is None:
        return SnapshotReplaceDecision(True, None, None)
    if float(incoming_saved_at) < float(existing_saved_at):
        return SnapshotReplaceDecision(False, "newer_publish", "kept_newer_publish")
    if existing_payload is None:
        return SnapshotReplaceDecision(True, None, None)

    existing_through = extract_score_data_through(existing_payload)
    incoming_through = extract_score_data_through(incoming_payload)
    if existing_through is not None and incoming_through is None:
        return _keep("unknown_score_data", "kept_previous_snapshot")
    if (
        existing_through is not None
        and incoming_through is not None
        and incoming_through < existing_through
    ):
        return _keep("older_score_data", "kept_previous_snapshot")

    existing_version = extract_scoring_version(existing_payload)
    incoming_version = extract_scoring_version(incoming_payload)
    if existing_version and not incoming_version:
        return _keep("missing_scoring_version", "kept_previous_snapshot")
    if (
        existing_version
        and incoming_version
        and incoming_version != existing_version
        and incoming_version != expected_scoring_version
    ):
        return _keep("scoring_version_mismatch", "kept_previous_snapshot")

    existing_coverage = extract_usable_input_coverage(existing_payload)
    incoming_coverage = extract_usable_input_coverage(incoming_payload)
    incoming_newer_input = (
        existing_through is None and incoming_through is not None
    ) or (
        existing_through is not None
        and incoming_through is not None
        and incoming_through > existing_through
    )
    if (
        existing_coverage is not None
        and incoming_coverage is not None
        and incoming_coverage < existing_coverage
        and not incoming_newer_input
    ):
        return _keep("coverage_regressed", "kept_previous_snapshot")
    return SnapshotReplaceDecision(True, None, None)


def should_replace_published_snapshot(
    *,
    existing_saved_at: float | None,
    incoming_saved_at: float,
    existing_payload: Any = None,
    incoming_payload: Any = None,
    expected_scoring_version: str | None = None,
    allow_reason: str | None = None,
) -> bool:
    return decide_published_snapshot_replacement(
        existing_saved_at=existing_saved_at,
        incoming_saved_at=incoming_saved_at,
        existing_payload=existing_payload,
        incoming_payload=incoming_payload,
        expected_scoring_version=expected_scoring_version,
        allow_reason=allow_reason,
    ).replace
