from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .focus_config import FocusContextSettings
from .focus_models import FOCUS_SCHEMA_SHA256, FocusContextDraft, FocusSymbol
from .models import TICKER_PATTERN


_ACTIVE_BREAKOUT_STATES = {
    "TRIGGERED",
    "CONFIRMED",
    "HOLDING",
    "RETESTING",
    "RETEST_HELD",
    "REACCELERATING",
    "EXTENDED",
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ticker(row: Mapping[str, Any]) -> str | None:
    value = str(row.get("ticker") or "").strip().upper()
    return value if TICKER_PATTERN.fullmatch(value) else None


def _features(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("features")
    return value if isinstance(value, Mapping) else {}


def _value(row: Mapping[str, Any], *names: str) -> Any:
    features = _features(row)
    for name in names:
        if row.get(name) is not None:
            return row[name]
        if features.get(name) is not None:
            return features[name]
    return None


def _dollar_volume(row: Mapping[str, Any], market_session: str) -> float | None:
    if market_session == "regular":
        intraday = _finite(
            _value(row, "cumulative_dollar_volume", "current_dollar_volume")
        )
        if intraday is not None:
            return intraday
        # A daily-only canonical scan cannot honestly manufacture an intraday
        # cumulative value. Fall back to the last complete session/ADV20 so the
        # pool remains useful while preserving a deterministic dollar-volume
        # basis instead of ranking by share volume.
        return _finite(
            _value(
                row,
                "previous_session_dollar_volume",
                "last_complete_session_dollar_volume",
                "avg_dollar_volume_20d",
                "average_dollar_volume",
            )
        )
    return _finite(
        _value(
            row,
            "previous_session_dollar_volume",
            "last_complete_session_dollar_volume",
            "avg_dollar_volume_20d",
            "average_dollar_volume",
        )
    )


def _normalized_quality(value: Any) -> float | None:
    quality = _finite(value)
    if quality is None:
        return None
    if quality > 1:
        quality /= 100.0
    if quality < 0 or quality > 1:
        return None
    return round(quality, 4)


def _as_utc(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    else:
        return fallback
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return fallback
    return parsed.astimezone(timezone.utc)


def build_focus_context(
    *,
    settings: FocusContextSettings,
    strength_rows: Sequence[Mapping[str, Any]],
    breakout_rows: Sequence[Mapping[str, Any]] = (),
    canonical_symbols: Sequence[str] = (),
    previous_symbols: Sequence[str] = (),
    previous_context: Sequence[FocusSymbol | Mapping[str, Any]] = (),
    as_of: datetime,
    data_through: datetime | None,
    market_session: str,
    universe_version: str,
) -> FocusContextDraft:
    """Build a display-only focus universe from already-produced local data.

    The function never fetches prices or starts a scanner. Strength order is
    consumed as an existing local ranking, while only safe market-reaction
    fields are emitted.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    if market_session not in {"premarket", "regular", "after_hours", "closed", "unknown"}:
        market_session = "unknown"

    rows: dict[str, dict[str, Any]] = {}
    previous_records: dict[str, FocusSymbol] = {}
    for raw in previous_context:
        try:
            symbol = raw if isinstance(raw, FocusSymbol) else FocusSymbol.model_validate(raw)
        except Exception:
            continue
        previous_records[symbol.ticker] = symbol
    strength_order: list[str] = []
    for raw in strength_rows:
        ticker = _ticker(raw)
        if ticker is None:
            continue
        rows.setdefault(ticker, {}).update(dict(raw))
        if ticker not in strength_order:
            strength_order.append(ticker)

    active_breakouts: set[str] = set()
    for raw in breakout_rows:
        ticker = _ticker(raw)
        if ticker is None:
            continue
        merged = rows.setdefault(ticker, {})
        for key, value in dict(raw).items():
            if value is not None and merged.get(key) is None:
                merged[key] = value
        state = str(
            _value(raw, "breakout_state", "lifecycle_state", "state") or ""
        ).upper()
        if state in _ACTIVE_BREAKOUT_STATES:
            active_breakouts.add(ticker)
            merged["breakout_state"] = state

    for ticker in [
        *settings.priority_symbols,
        *settings.index_constituent_symbols,
        *active_breakouts,
    ]:
        rows.setdefault(ticker, {"ticker": ticker})
    for ticker in [*previous_symbols, *previous_records]:
        normalized = str(ticker).strip().upper()
        if not TICKER_PATTERN.fullmatch(normalized) or normalized in rows:
            continue
        prior = previous_records.get(normalized)
        rows[normalized] = {
            "ticker": normalized,
            "_previous_missing": True,
            "as_of": prior.as_of if prior else as_of,
            "sector_id": prior.sector_id if prior else None,
            "validation_status": prior.validation_status if prior else "unverified",
        }

    canonical = {
        str(value).strip().upper()
        for value in canonical_symbols
        if TICKER_PATTERN.fullmatch(str(value).strip().upper())
    }
    canonical.update(
        ticker
        for ticker, row in rows.items()
        if bool(row.get("universe_member"))
    )

    ranked = sorted(
        (
            (ticker, value)
            for ticker, row in rows.items()
            if (value := _dollar_volume(row, market_session)) is not None
        ),
        key=lambda item: (-item[1], item[0]),
    )
    dollar_ranks = {ticker: index for index, (ticker, _) in enumerate(ranked, 1)}
    reasons: dict[str, set[str]] = {ticker: set() for ticker in rows}
    for ticker, rank in dollar_ranks.items():
        if rank <= settings.dollar_volume_count:
            reasons[ticker].add(f"dollar_volume_top{settings.dollar_volume_count}")
    for ticker in strength_order[: settings.strength_count]:
        reasons[ticker].add(f"strength_top{settings.strength_count}")
    for ticker in active_breakouts:
        reasons[ticker].add("active_breakout")
    for ticker in settings.priority_symbols:
        reasons[ticker].add("priority_watchlist")
    for ticker in settings.index_constituent_symbols:
        reasons[ticker].add("major_index_constituent")

    previous = [
        str(value).strip().upper()
        for value in [*previous_symbols, *previous_records]
        if str(value).strip().upper() in rows
    ]
    previous = list(dict.fromkeys(previous))
    for ticker in previous:
        if rows[ticker].get("_previous_missing"):
            reasons[ticker].add("stale_retained")
    previous_set = set(previous)
    eligible: set[str] = set()
    for ticker, ticker_reasons in reasons.items():
        rank = dollar_ranks.get(ticker)
        non_dollar_reason = any(
            not reason.startswith("dollar_volume_") for reason in ticker_reasons
        )
        if non_dollar_reason or (
            rank is not None
            and (
                rank <= settings.enter_dollar_volume_rank
                or (ticker in previous_set and rank <= settings.retain_dollar_volume_rank)
            )
        ):
            eligible.add(ticker)
        if (
            ticker in previous_set
            and rank is not None
            and settings.dollar_volume_count < rank <= settings.retain_dollar_volume_rank
        ):
            ticker_reasons.add("dollar_volume_retained")

    forced = {
        ticker
        for ticker in eligible
        if "active_breakout" in reasons[ticker]
        or "priority_watchlist" in reasons[ticker]
    }
    strength_rank = {ticker: index for index, ticker in enumerate(strength_order, 1)}

    def priority(ticker: str) -> tuple[Any, ...]:
        ticker_reasons = reasons[ticker]
        return (
            0 if "priority_watchlist" in ticker_reasons else 1,
            0 if "active_breakout" in ticker_reasons else 1,
            dollar_ranks.get(ticker, 10**9),
            strength_rank.get(ticker, 10**9),
            0 if "major_index_constituent" in ticker_reasons else 1,
            ticker,
        )

    desired = sorted(eligible, key=priority)
    warnings: list[str] = []
    if not previous:
        selected = desired[: settings.max_symbols]
    else:
        selected_set = set(forced)
        selected_set.update(ticker for ticker in previous if ticker in eligible)
        new_ordinary = [
            ticker
            for ticker in desired
            if ticker not in previous_set and ticker not in forced
        ][: settings.max_replacements_per_cycle]
        selected_set.update(new_ordinary)
        selected = [ticker for ticker in desired if ticker in selected_set]
        if len(selected) > settings.max_symbols:
            mandatory = [ticker for ticker in selected if ticker in forced]
            ordinary = [ticker for ticker in selected if ticker not in forced]
            if len(mandatory) > settings.max_symbols:
                warnings.append("mandatory_focus_symbols_truncated")
            selected = (mandatory + ordinary)[: settings.max_symbols]

    if len(desired) > len(selected):
        warnings.append("focus_universe_bounded")
    if not dollar_ranks:
        warnings.append("dollar_volume_unavailable")

    reason_order = {
        "priority_watchlist": 0,
        "active_breakout": 1,
        f"dollar_volume_top{settings.dollar_volume_count}": 2,
        "dollar_volume_retained": 3,
        f"strength_top{settings.strength_count}": 4,
        "major_index_constituent": 5,
        "stale_retained": 98,
    }
    symbols: list[FocusSymbol] = []
    for ticker in selected:
        row = rows[ticker]
        stale = bool(row.get("_previous_missing"))
        prior = previous_records.get(ticker)
        explicit_validation = str(row.get("validation_status") or "").lower()
        if ticker in canonical:
            validation_status = "canonical"
        elif explicit_validation == "valid_external":
            validation_status = "valid_external"
        elif prior is not None and prior.validation_status in {"canonical", "valid_external"}:
            validation_status = prior.validation_status
        else:
            validation_status = "unverified"
        symbols.append(
            FocusSymbol(
                ticker=ticker,
                validation_status=validation_status,
                data_status="stale" if stale else "active",
                universe_reasons=sorted(
                    reasons[ticker], key=lambda value: (reason_order.get(value, 99), value)
                ),
                dollar_volume_rank=dollar_ranks.get(ticker),
                session_change_pct=(
                    None if stale else _finite(_value(row, "session_change_pct", "change_pct"))
                ),
                rvol_time_of_day=(
                    None if stale else _finite(_value(row, "rvol_time_of_day"))
                ),
                breakout_state=(
                    str(_value(row, "breakout_state", "lifecycle_state", "state"))[:60]
                    if _value(row, "breakout_state", "lifecycle_state", "state")
                    else None
                ),
                sector_id=(
                    str(_value(row, "sector_id", "primary_sector_id"))[:120]
                    if _value(row, "sector_id", "primary_sector_id")
                    else None
                ),
                as_of=_as_utc(_value(row, "as_of", "universe_as_of"), as_of),
                data_quality=None if stale else _normalized_quality(_value(row, "data_quality")),
            )
        )

    return FocusContextDraft(
        schema_sha256=FOCUS_SCHEMA_SHA256,
        as_of=as_of,
        data_through=data_through,
        market_session=market_session,
        universe_version=universe_version,
        symbols=symbols,
        major_market_symbols=settings.market_symbols,
        warnings=list(dict.fromkeys(warnings)),
    )
