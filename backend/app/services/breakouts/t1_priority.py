"""Production T1 evaluation and stable boost sort.

T1 never changes detector output, lifecycle, or event identity. It only
annotates daily-condition status and, when selected, reorders an already
qualified result set.
"""

from __future__ import annotations

from app.services.numeric import (
    finite_number as _finite,
)

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

import pandas as pd

from app.services.algorithm_modes import T1_ALGORITHM, T1_VERSION
from app.services.breakouts.daily_confirmation import (
    T1_SETTINGS,
    daily_bar_location,
    rvol_daily_20med,
    t1_checks,
    valid_daily_ohlc,
    valid_session_volume,
)
from app.services.breakouts.feature_engine import (
    completed_daily_session,
    compute_atr,
    trim_daily_bars,
)
from app.services.breakouts.anchors import resolve_event_anchor
from app.services.breakouts.models import MarketSession, TemporalCutoff
from app.services.market_calendar import ET, is_trading_day, prior_trading_sessions


T1_MET = "met"
T1_UNMET = "unmet"
T1_PENDING = "pending"
T1_UNAVAILABLE = "unavailable"
T1_NOT_APPLICABLE = "not_applicable"
T1_DAILY_SETUP = "DAILY_BASE_BREAKOUT"
T1_TERMINAL_STATUSES = {T1_MET, T1_UNMET}
T1_EXPIRED_LIFECYCLES = {"FAILED", "EXPIRED"}
T1_RETRYABLE_REASONS = {
    "daily_unavailable",
    "missing_event_bar",
    "missing_event_volume",
    "no_completed_daily_bars",
    "daily_fetch_failed",
    "price_adapter_unavailable",
}
T1_DATA_CONVENTION = "adj_preferred"
T1_SETTLED_STATUSES = {T1_MET, T1_UNMET}


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.astimezone(ET).date()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _bar_date(index_value: Any) -> date | None:
    if hasattr(index_value, "date"):
        try:
            return index_value.date()
        except Exception:
            return None
    return _as_date(index_value)


def _ohlc(row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    if isinstance(row, pd.Series):
        data = {str(key).lower(): row[key] for key in row.index}
    else:
        data = {str(key).lower(): value for key, value in dict(row).items()}
    return {
        "open": data.get("adj_open", data.get("open")),
        "high": data.get("adj_high", data.get("high")),
        "low": data.get("adj_low", data.get("low")),
        "close": data.get("adj_close", data.get("close")),
        "volume": data.get("adj_volume", data.get("volume")),
    }


def _market_session(value: MarketSession | str | None) -> MarketSession:
    try:
        return MarketSession(str(value or MarketSession.CLOSED.value))
    except ValueError:
        return MarketSession.CLOSED


def _event_setup_type(event: Mapping[str, Any] | None) -> str:
    """Accept both production model enums and persisted JSON strings."""

    payload = dict(event or {})
    setup = payload.get("setup_type") or payload.get("origin_setup_type") or ""
    return str(getattr(setup, "value", setup)).strip()


def t1_setup_applicable(event: Mapping[str, Any] | None) -> bool:
    return _event_setup_type(event) == T1_DAILY_SETUP


def t1_boost_eligible(event: Mapping[str, Any]) -> bool:
    lifecycle = str(event.get("lifecycle_state") or "").strip()
    if lifecycle in T1_EXPIRED_LIFECYCLES:
        return False
    return event_t1_status(event) == T1_MET


def t1_needs_close_eval(event: Mapping[str, Any]) -> bool:
    if not t1_setup_applicable(event):
        return False
    status = event_t1_status(event)
    if status in {T1_MET, T1_UNMET}:
        return False
    # Early enum annotations incorrectly marked daily-base events inapplicable.
    # The canonical setup above allows those stored attempts to recover at close;
    # genuinely inapplicable setups have already returned False.
    if status in {T1_PENDING, T1_NOT_APPLICABLE}:
        return True
    features = event.get("features") if isinstance(event.get("features"), Mapping) else {}
    payload = event.get("t1_priority")
    if not isinstance(payload, Mapping):
        payload = features.get("t1_priority") if isinstance(features, Mapping) else None
    reason = str((payload or {}).get("reason") or "") if isinstance(payload, Mapping) else ""
    return status == T1_UNAVAILABLE and reason in T1_RETRYABLE_REASONS


def _identity_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def t1_identity_complete(payload: Mapping[str, Any] | None) -> bool:
    """True only for a hashable *and* decision-complete settled conclusion.

    A hash that includes null resistance or volume is not proof that the
    inputs were complete. Unavailable / pending attempts are never complete.
    """

    body = dict(payload or {})
    if body.get("identity_complete") is False:
        return False
    status = str(body.get("status") or "").strip()
    if status not in T1_SETTLED_STATUSES:
        return False
    return bool(str(body.get("identity_hash") or "").strip())


def t1_input_identity(
    *,
    event_id: str | None,
    session_date: date,
    resistance_high: Any,
    session_bar: Mapping[str, Any],
    prior_bars: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any] | None = None,
) -> str:
    """Fingerprint every causal T-window input, including ATR history."""

    cfg = {**T1_SETTINGS, **dict(settings or {})}
    return _identity_hash(
        {
            "event_id": event_id or "",
            "session_date": session_date.isoformat(),
            "version": T1_VERSION,
            "variant": T1_ALGORITHM,
            "data_convention": T1_DATA_CONVENTION,
            "resistance": _finite(resistance_high),
            "session_bar": {
                "open": _finite(session_bar.get("open")),
                "high": _finite(session_bar.get("high")),
                "low": _finite(session_bar.get("low")),
                "close": _finite(session_bar.get("close")),
                "volume": _finite(session_bar.get("volume")),
            },
            "prior_bars": [
                {
                    "session_date": str(item.get("session_date") or ""),
                    "open": _finite(item.get("open")),
                    "high": _finite(item.get("high")),
                    "low": _finite(item.get("low")),
                    "close": _finite(item.get("close")),
                    "volume": _finite(item.get("volume")),
                }
                for item in prior_bars
            ],
            "settings": {
                "clv_min": cfg.get("clv_min"),
                "rvol_min": cfg.get("rvol_min"),
                "upper_shadow_max": cfg.get("upper_shadow_max"),
                "distance_atr_min": cfg.get("distance_atr_min"),
                "rvol_lookback": cfg.get("rvol_lookback"),
                "atr_period": cfg.get("atr_period"),
            },
        }
    )


def t1_view_token(
    scan_run_id: str,
    sort_algorithm: str,
    events: Sequence[Mapping[str, Any]],
) -> str:
    items = []
    for event in events:
        payload = event.get("t1_priority")
        if not isinstance(payload, Mapping):
            features = event.get("features") if isinstance(event.get("features"), Mapping) else {}
            payload = features.get("t1_priority") if isinstance(features, Mapping) else {}
        body = dict(payload or {})
        items.append(
            {
                "event_id": str(event.get("event_id") or ""),
                "status": str(body.get("status") or ""),
                "eval_version": int(body.get("eval_version") or 0),
                "identity_hash": str(body.get("identity_hash") or ""),
            }
        )
    items.sort(key=lambda item: item["event_id"])
    return _identity_hash(
        {
            "scan_run_id": scan_run_id,
            "sort_algorithm": sort_algorithm,
            "items": items,
        }
    )[:24]


def session_daily_complete(
    session_date: date,
    *,
    as_of: datetime,
    session: MarketSession | str | None = None,
) -> bool:
    """True only after that session's regular close, including early closes."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("T1 evaluation requires a timezone-aware as_of")
    cutoff = TemporalCutoff(event_at=as_of, session=_market_session(session))
    return completed_daily_session(cutoff) >= session_date


def evaluate_t1_from_daily(
    daily: pd.DataFrame | None,
    *,
    session_date: date,
    resistance_high: Any,
    as_of: datetime,
    session: MarketSession | str | None = None,
    previous: Mapping[str, Any] | None = None,
    settings: Mapping[str, Any] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate T1 from completed daily bars only.

    Inputs are always trimmed to the event session. A later trading day must
    not change a settled conclusion for the same event and input identity.
    ``known_at`` is the first time this identity became met/unmet; later
    revisions keep that first stamp and record the change.
    """

    cfg = {**T1_SETTINGS, **dict(settings or {})}
    computed_at = as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    prior = dict(previous or {})
    prior_known = prior.get("known_at") if prior.get("status") in T1_TERMINAL_STATUSES else None
    first_known = prior.get("first_known_at") or prior_known
    base = {
        "version": T1_VERSION,
        "variant": T1_ALGORITHM,
        "status": T1_UNAVAILABLE,
        "session_date": session_date.isoformat(),
        "data_through": None,
        "session_complete": False,
        "computed_at": computed_at,
        "known_at": None,
        "first_known_at": first_known,
        "eval_version": int(prior.get("eval_version") or 0) or 1,
        "identity_hash": None,
        "identity_complete": False,
        "revisions": list(prior.get("revisions") or []),
        "event_id": event_id or prior.get("event_id"),
        "checks": {
            "clv": False,
            "rvol": False,
            "upper_shadow": False,
            "distance_atr": False,
        },
        "clv": None,
        "rvol_daily_20med": None,
        "upper_shadow_ratio": None,
        "breakout_distance_atr": None,
        "reason": None,
    }
    if not is_trading_day(session_date):
        return {**base, "reason": "not_a_trading_day"}
    if not session_daily_complete(session_date, as_of=as_of, session=session):
        return {**base, "status": T1_PENDING, "reason": "session_incomplete"}
    if daily is None or not isinstance(daily, pd.DataFrame) or daily.empty:
        return {**base, "reason": "daily_unavailable"}

    cutoff = TemporalCutoff(
        event_at=as_of,
        session=MarketSession.CLOSED,
        completed_daily_session=session_date,
    )
    raw_session_rows = [
        (idx, daily.loc[idx])
        for idx in daily.index
        if _bar_date(idx) == session_date
    ]
    if raw_session_rows:
        raw_ohlc = _ohlc(raw_session_rows[-1][1])
        if valid_daily_ohlc(raw_ohlc["open"], raw_ohlc["high"], raw_ohlc["low"], raw_ohlc["close"]) is None:
            return {
                **base,
                "session_complete": True,
                "reason": "invalid_ohlc",
            }
    completed = trim_daily_bars(daily, cutoff)
    if completed.empty:
        return {**base, "reason": "no_completed_daily_bars"}

    lookback = int(cfg["rvol_lookback"])
    try:
        prior_sessions = prior_trading_sessions(session_date, lookback)
    except (RuntimeError, ValueError):
        return {**base, "session_complete": True, "reason": "trading_calendar_unavailable"}

    bars_by_date: dict[date, list[tuple[Any, Any]]] = {}
    for idx in completed.index:
        day = _bar_date(idx)
        if day is None:
            return {**base, "session_complete": True, "reason": "unordered_daily_bars"}
        bars_by_date.setdefault(day, []).append((idx, completed.loc[idx]))
    if any(len(rows) > 1 for rows in bars_by_date.values()):
        return {**base, "session_complete": True, "reason": "duplicate_session_bar"}

    session_rows = bars_by_date.get(session_date) or []
    if not session_rows:
        return {
            **base,
            "session_complete": True,
            "reason": "missing_event_bar",
        }
    bar_index, bar = session_rows[-1]
    ohlc = _ohlc(bar)
    if valid_daily_ohlc(ohlc["open"], ohlc["high"], ohlc["low"], ohlc["close"]) is None:
        return {
            **base,
            "session_complete": True,
            "reason": "invalid_ohlc",
        }
    location = daily_bar_location(ohlc["open"], ohlc["high"], ohlc["low"], ohlc["close"])
    prior_volumes: list[Any] = []
    prior_bars: list[dict[str, Any]] = []
    for day in prior_sessions:
        rows = bars_by_date.get(day)
        if not rows:
            return {
                **base,
                "session_complete": True,
                "reason": "missing_prior_session",
            }
        prior_ohlc = _ohlc(rows[-1][1])
        if valid_daily_ohlc(
            prior_ohlc["open"], prior_ohlc["high"], prior_ohlc["low"], prior_ohlc["close"]
        ) is None:
            return {
                **base,
                "session_complete": True,
                "reason": "invalid_prior_ohlc",
            }
        if valid_session_volume(prior_ohlc["volume"]) is None:
            return {
                **base,
                "session_complete": True,
                "reason": "missing_prior_volume",
            }
        prior_volumes.append(prior_ohlc["volume"])
        prior_bars.append(
            {
                "session_date": day.isoformat(),
                "open": _finite(prior_ohlc["open"]),
                "high": _finite(prior_ohlc["high"]),
                "low": _finite(prior_ohlc["low"]),
                "close": _finite(prior_ohlc["close"]),
                "volume": _finite(prior_ohlc["volume"]),
            }
        )
    identity = t1_input_identity(
        event_id=event_id or prior.get("event_id") or "",
        session_date=session_date,
        resistance_high=resistance_high,
        session_bar=ohlc,
        prior_bars=prior_bars,
        settings=cfg,
    )
    if (
        prior.get("identity_hash") == identity
        and prior.get("status") in T1_TERMINAL_STATUSES
        and prior.get("version") == T1_VERSION
    ):
        reused = dict(prior)
        reused.update(
            {
                "computed_at": computed_at,
                "session_complete": True,
                "identity_hash": identity,
                "identity_complete": True,
                "reused": True,
                "first_known_at": first_known or prior.get("known_at"),
                "event_id": event_id or prior.get("event_id"),
            }
        )
        reused.pop("latest_attempt", None)
        return reused
    rvol = rvol_daily_20med(
        ohlc["volume"],
        prior_volumes,
        lookback=lookback,
    )
    window_dates = set(prior_sessions) | {session_date}
    window_indexes = [idx for idx in completed.index if _bar_date(idx) in window_dates]
    window_frame = completed.loc[window_indexes]
    atr = compute_atr(window_frame, period=int(cfg["atr_period"]))
    close_v = _finite(ohlc["close"])
    resistance = _finite(resistance_high)
    distance = (
        (close_v - resistance) / atr
        if close_v is not None and resistance is not None and atr is not None and atr > 0
        else None
    )
    checks = t1_checks(
        clv=location["clv"],
        rvol=rvol.get("rvol_daily_20med"),
        upper_shadow=location["upper_shadow_ratio"],
        distance_atr=distance,
        settings=cfg,
    )
    data_through = _bar_date(bar_index)
    data_through_text = data_through.isoformat() if data_through else session_date.isoformat()
    detail = {
        "status": rvol.get("status"),
        "reason": rvol.get("reason"),
        "lookback_used": rvol.get("lookback_used"),
        "denominator": rvol.get("denominator"),
    }
    if not checks["available"]:
        reason = rvol.get("reason") or (
            "invalid_ohlc" if location.get("invalid_ohlc")
            else "zero_range" if location.get("zero_range")
            else "t1_inputs_unavailable"
        )
        return {
            **base,
            "status": T1_UNAVAILABLE,
            "session_complete": True,
            "data_through": data_through_text,
            "identity_hash": identity,
            "identity_complete": False,
            "reason": reason,
            "checks": checks["checks"],
            "clv": checks["clv"],
            "rvol_daily_20med": checks["rvol"],
            "upper_shadow_ratio": checks["upper_shadow_ratio"],
            "breakout_distance_atr": checks["breakout_distance_atr"],
            "rvol_detail": detail,
        }
    status = T1_MET if checks["satisfied"] else T1_UNMET
    reason = None if status == T1_MET else "conditions_not_met"
    known_at = computed_at
    revisions = list(prior.get("revisions") or [])
    eval_version = int(prior.get("eval_version") or 0) or 1
    if (
        prior.get("status") in T1_TERMINAL_STATUSES
        and prior.get("identity_hash")
        and prior.get("identity_hash") != identity
    ):
        revisions.append(
            {
                "eval_version": eval_version,
                "identity_hash": prior.get("identity_hash"),
                "status": prior.get("status"),
                "known_at": prior.get("known_at"),
                "computed_at": prior.get("computed_at"),
            }
        )
        eval_version += 1
        first_known = prior.get("first_known_at") or prior.get("known_at") or known_at
    return {
        **base,
        "status": status,
        "session_complete": True,
        "data_through": data_through_text,
        "known_at": known_at,
        "first_known_at": first_known or known_at,
        "eval_version": eval_version,
        "identity_hash": identity,
        "identity_complete": True,
        "revisions": revisions,
        "reused": False,
        "event_id": event_id or prior.get("event_id"),
        "checks": checks["checks"],
        "clv": checks["clv"],
        "rvol_daily_20med": checks["rvol"],
        "upper_shadow_ratio": checks["upper_shadow_ratio"],
        "breakout_distance_atr": checks["breakout_distance_atr"],
        "reason": reason,
        "rvol_detail": detail,
    }


def event_t1_status(event: Mapping[str, Any]) -> str:
    features = event.get("features") if isinstance(event.get("features"), Mapping) else {}
    payload = event.get("t1_priority")
    if not isinstance(payload, Mapping):
        payload = features.get("t1_priority") if isinstance(features, Mapping) else None
    if not isinstance(payload, Mapping):
        return T1_UNAVAILABLE
    status = str(payload.get("status") or "").strip()
    if status in {T1_MET, T1_UNMET, T1_PENDING, T1_UNAVAILABLE, T1_NOT_APPLICABLE}:
        return status
    return T1_UNAVAILABLE


def _event_at_text(event: Mapping[str, Any]) -> str:
    value = event.get("event_at") or event.get("triggered_at") or event.get("first_seen_at")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _alert_priority(event: Mapping[str, Any]) -> float | None:
    scores = event.get("scores") if isinstance(event.get("scores"), Mapping) else {}
    return _finite(
        event.get("alert_priority_score")
        if event.get("alert_priority_score") is not None
        else (scores or {}).get("alert_priority_score")
    )


def production_event_sort_key(event: Mapping[str, Any]) -> tuple[Any, ...]:
    """Match repository list order: event_at DESC, priority DESC, event_id DESC."""

    return (
        _event_at_text(event),
        _alert_priority(event) if _alert_priority(event) is not None else -1.0,
        str(event.get("event_id") or ""),
    )


def event_group_key(event: Mapping[str, Any]) -> str:
    trading = _as_date(event.get("trading_date"))
    if trading is not None:
        return trading.isoformat()
    raw = event.get("event_at") or event.get("triggered_at")
    parsed = _as_date(raw)
    return parsed.isoformat() if parsed is not None else ""


def apply_t1_stable_boost(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Boost T1-met events inside each trading-date group.

    Group order and intra-group production order are preserved. Pending and
    unavailable events stay with unmet events; they are not extra-penalized.
    """

    ordered = [dict(item) for item in events]
    ordered.sort(key=production_event_sort_key, reverse=True)
    groups: dict[str, list[dict[str, Any]]] = {}
    group_order: list[str] = []
    for item in ordered:
        key = event_group_key(item)
        if key not in groups:
            group_order.append(key)
            groups[key] = []
        groups[key].append(item)
    boosted: list[dict[str, Any]] = []
    for key in group_order:
        bucket = groups[key]
        met = [item for item in bucket if t1_boost_eligible(item)]
        rest = [item for item in bucket if not t1_boost_eligible(item)]
        boosted.extend(met)
        boosted.extend(rest)
    return boosted


def t1_resistance_high(event: Mapping[str, Any]) -> Any:
    """Same resistance the detector uses: ORB anchor first, else daily-base zone."""

    anchor = resolve_event_anchor(event)
    if anchor is not None and anchor.pivot_price is not None:
        return anchor.pivot_price
    structure = event.get("structure") if isinstance(event.get("structure"), Mapping) else None
    zone = structure.get("resistance_zone") if isinstance(structure, Mapping) else None
    if isinstance(zone, Mapping):
        return zone.get("high")
    return None


def attach_t1_features(
    event: Mapping[str, Any],
    daily: pd.DataFrame | None,
    *,
    as_of: datetime,
    session: MarketSession | str | None = None,
) -> dict[str, Any]:
    payload = dict(event)
    features = dict(payload.get("features") or {})
    resistance = t1_resistance_high(payload)
    session_date = _as_date(payload.get("trading_date"))
    computed_at = as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if not t1_setup_applicable(payload):
        evaluation = {
            "version": T1_VERSION,
            "variant": T1_ALGORITHM,
            "status": T1_NOT_APPLICABLE,
            "reason": "setup_not_in_t1_universe",
            "setup_type": _event_setup_type(payload),
            "session_date": session_date.isoformat() if session_date else None,
            "computed_at": computed_at,
            "known_at": None,
        }
    elif session_date is None:
        evaluation = {
            "version": T1_VERSION,
            "variant": T1_ALGORITHM,
            "status": T1_UNAVAILABLE,
            "reason": "missing_trading_date",
            "computed_at": computed_at,
        }
    else:
        evaluation = evaluate_t1_from_daily(
            daily,
            session_date=session_date,
            resistance_high=resistance,
            as_of=as_of,
            session=session or payload.get("session"),
            previous=(
                features.get("t1_priority")
                if isinstance(features.get("t1_priority"), Mapping)
                else payload.get("t1_priority")
                if isinstance(payload.get("t1_priority"), Mapping)
                else None
            ),
            event_id=str(payload.get("event_id") or "") or None,
        )
    features["t1_priority"] = evaluation
    payload["features"] = features
    payload["t1_priority"] = evaluation
    return payload
