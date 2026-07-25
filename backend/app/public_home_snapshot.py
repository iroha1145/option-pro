"""Restart-safe, read-only snapshots for the anonymous public home page."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.data_paths import get_data_paths


PUBLIC_HOME_SNAPSHOT_VERSION = 1
PUBLIC_HOME_SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024
PUBLIC_HOME_DEFAULT_TICKER = "NVDA"
PUBLIC_HOME_MAX_CLOCK_SKEW_SECONDS = 5 * 60
PUBLIC_HOME_INDEX_SYMBOLS = ("^GSPC", "^IXIC", "^DJI", "^N225", "000001.SS")
PUBLIC_HOME_RESOURCE_ORDER = (
    "indices",
    "focus_overview",
    "focus_chart",
    "focus_signals",
    "market_signals",
    "earnings",
    "unusual",
)
PUBLIC_HOME_OPTIONAL_RESOURCE_ORDER = ("breakout_lead_chart",)
_MARKET_TZ = ZoneInfo("America/New_York")
_BREAKOUT_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
_EARNINGS_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")
_MARKET_SIGNAL_FIELDS = {
    "sma20_distance",
    "sma50_distance",
    "sma200_distance",
    "rsi14",
    "return_20d",
    "rsp_spy_5d",
    "iwm_spy_5d",
    "qqq_spy_5d",
    "sectors_above_50dma",
    "vix",
    "vix_percentile",
    "vix_5d_change",
    "credit_risk",
    "yield_10y",
    "yield_10y_20d_change",
}
_MARKET_SCORE_FIELDS = {
    "top_score",
    "bottom_score",
    "top_status",
    "bottom_status",
    "data_quality",
    "signal_data_quality",
    "data_quality_available",
    "data_quality_expected",
    "coverage",
    "top_breakdown",
    "bottom_breakdown",
    "top_label",
    "bottom_label",
    "top_reasons",
    "bottom_reasons",
}
_MARKET_TOP_BREAKDOWN_FIELDS = {
    "price_overheated",
    "breadth_divergence",
    "options_sentiment",
    "volatility_turning",
    "rates_pressure",
    "credit_risk",
    "positioning",
}
_MARKET_BOTTOM_BREAKDOWN_FIELDS = {
    "panic_release",
    "technical_reclaim",
    "breadth_repair",
    "volatility_falling",
    "credit_stable",
    "rates_easing",
    "sentiment_pessimism",
}
_ENTRY_FIELDS = {"payload", "saved_at", "parameters", "schema", "max_age"}
_OVERVIEW_FIELDS = {
    "ticker",
    "name",
    "name_en",
    "website",
    "logo_url",
    "logo_urls",
    "price",
    "change",
    "change_percent",
    "volume",
    "market_cap",
    "prev_close",
    "high",
    "low",
    "open",
    "as_of",
    "price_provider",
    "description",
    "description_en",
    "sic_description",
    "pe_ratio",
    "dividend_yield",
    "year_high",
    "year_low",
}
_CHART_FIELDS = {
    "ticker",
    "range",
    "period",
    "interval",
    "exchange_timezone",
    "price_adjustment",
    "include_extended_hours",
    "moving_average_scope",
    "as_of",
    "last_bar_at",
    "source_status",
    "visible",
    "bars",
    "ema20",
    "sma50",
}
_EARNINGS_ROW_FIELDS = {
    "ticker",
    "name",
    "earnings_date",
    "days_until",
    "timing",
    "eps_estimate",
    "eps_actual",
    "eps_high",
    "eps_low",
    "revenue_estimate",
    "revenue_actual",
    "market_cap",
    "sector",
    "earnings_date_source",
    "estimate_source",
    "actual_source",
    "release_status",
    "quarter",
    "year",
    "source_status",
    "observed_at",
    "expected_move_pct",
    "expected_move_expiration",
    "expected_move_source",
    "expected_move_observed_at",
    "expected_move_source_status",
}
_UNUSUAL_ROW_FIELDS = {
    "ticker",
    "contract_ticker",
    "contract_type",
    "type",
    "strike",
    "expiration",
    "volume",
    "open_interest",
    "oi",
    "vol_oi_ratio",
    "vol_oi",
    "premium",
    "last_price",
    "implied_volatility",
    "underlying_price",
    "in_the_money",
    "moneyness",
    "direction",
    "direction_confidence",
    "direction_status",
    "signal",
    "inferred_direction",
    "direction_deprecated",
}


@dataclass(frozen=True)
class PublicHomeResourceSpec:
    schema: str
    max_age: int


PUBLIC_HOME_RESOURCE_SPECS: dict[str, PublicHomeResourceSpec] = {
    # Public responses are always marked stale after restart. The longer hard
    # limits keep Friday's last successful research view available through a
    # weekend without ever presenting it as live data.
    "indices": PublicHomeResourceSpec("market-indices-v1", 4 * 24 * 60 * 60),
    # 7 days, not 24 hours: the market-phase-aware refresh backs off to roughly six
    # hours once the market closes, so a 24-hour hard limit cannot survive a weekend.
    # focus_overview is a *required* resource, so every Saturday it expired, the
    # snapshot lost a required entry, the worker reported degraded and deploy.sh's
    # verify_worker gate failed -- a weekend deploy could not pass. Its siblings on
    # the same ticker already use 7 days, which is what the comment above intends.
    "focus_overview": PublicHomeResourceSpec("focus-overview-v2", 7 * 24 * 60 * 60),
    "focus_chart": PublicHomeResourceSpec("focus-chart-v1", 7 * 24 * 60 * 60),
    "focus_signals": PublicHomeResourceSpec("focus-signals-v1", 7 * 24 * 60 * 60),
    "market_signals": PublicHomeResourceSpec("market-signals-v1", 7 * 24 * 60 * 60),
    "breakout_lead_chart": PublicHomeResourceSpec(
        "breakout-lead-chart-v1",
        7 * 24 * 60 * 60,
    ),
    "earnings": PublicHomeResourceSpec("earnings-upcoming-v2", 30 * 60 * 60),
    "unusual": PublicHomeResourceSpec("options-unusual-v1", 4 * 24 * 60 * 60),
}


def public_home_resource_parameters(resource: str, *, now: float) -> dict[str, Any]:
    if resource == "indices":
        return {"symbols": list(PUBLIC_HOME_INDEX_SYMBOLS)}
    if resource == "focus_overview":
        return {"ticker": PUBLIC_HOME_DEFAULT_TICKER}
    if resource == "focus_chart":
        return {
            "ticker": PUBLIC_HOME_DEFAULT_TICKER,
            "range": "1d",
            "adjustment": "raw",
        }
    if resource == "focus_signals":
        return {"ticker": PUBLIC_HOME_DEFAULT_TICKER, "period": "100d"}
    if resource == "market_signals":
        return {"period": "1y"}
    if resource == "earnings":
        return {
            "market_date": datetime.fromtimestamp(now, _MARKET_TZ).date().isoformat()
        }
    if resource == "unusual":
        return {"type": "all", "min_vol_oi": 1.0}
    raise KeyError(resource)


def breakout_lead_chart_parameters(ticker: str) -> dict[str, Any]:
    symbol = str(ticker).strip().upper()
    if not _BREAKOUT_TICKER_PATTERN.fullmatch(symbol):
        raise ValueError("invalid breakout lead ticker")
    return {
        "ticker": symbol,
        "range": "1d",
        "adjustment": "raw",
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _finite_number(value: Any, *, minimum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return math.isfinite(number) and (minimum is None or number >= minimum)


def _optional_finite(value: Any, *, minimum: float | None = None) -> bool:
    return value is None or _finite_number(value, minimum=minimum)


def _string_or_none(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _valid_json_tree(
    value: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> bool:
    if budget is None:
        budget = [250_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 12:
        return False
    if value is None or isinstance(value, (bool, str)):
        return not isinstance(value, str) or len(value) <= 262_144
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value))
    if isinstance(value, list):
        return len(value) <= 10_000 and all(
            _valid_json_tree(item, depth=depth + 1, budget=budget) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 2_048 and all(
            isinstance(key, str)
            and len(key) <= 256
            and _valid_json_tree(item, depth=depth + 1, budget=budget)
            for key, item in value.items()
        )
    return False


def _valid_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _iso_timestamp_seconds(value: Any) -> float | None:
    if not _valid_iso_timestamp(value):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        result = parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _payload_timestamps_fit_entry(
    resource: str,
    payload: Mapping[str, Any],
    *,
    not_after: float,
) -> bool:
    """Reject payload clocks later than the caller's trusted time limit."""

    limit = not_after
    iso_values: list[Any] = []
    integer_values: list[Any] = []
    if resource in {
        "indices",
        "focus_overview",
        "focus_chart",
        "breakout_lead_chart",
        "market_signals",
        "earnings",
        "unusual",
    }:
        iso_values.append(payload.get("as_of"))
    if resource in {"focus_chart", "breakout_lead_chart"}:
        if payload.get("last_bar_at") is not None:
            iso_values.append(payload.get("last_bar_at"))
        integer_values.extend(
            bar.get("t")
            for bar in payload.get("bars", [])
            if isinstance(bar, Mapping)
        )
        for field in ("ema20", "sma50"):
            integer_values.extend(
                item.get("time")
                for item in payload.get(field, [])
                if isinstance(item, Mapping)
            )
    elif resource == "earnings":
        iso_values.extend(
            row.get("observed_at")
            for row in payload.get("earnings", [])
            if isinstance(row, Mapping)
        )
        iso_values.extend(
            row.get("expected_move_observed_at")
            for row in payload.get("earnings", [])
            if isinstance(row, Mapping)
            and row.get("expected_move_observed_at") is not None
        )
    return bool(
        all(
            (timestamp := _iso_timestamp_seconds(value)) is not None
            and timestamp <= limit
            for value in iso_values
        )
        and all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value <= limit
            for value in integer_values
        )
    )


def _validate_indices(payload: Mapping[str, Any]) -> bool:
    if set(payload) != {
        "indices",
        "attempted",
        "succeeded",
        "data_limited",
        "source_status",
        "as_of",
    }:
        return False
    rows = payload.get("indices")
    if not isinstance(rows, list) or len(rows) != len(PUBLIC_HOME_INDEX_SYMBOLS):
        return False
    if [row.get("symbol") for row in rows if isinstance(row, dict)] != list(
        PUBLIC_HOME_INDEX_SYMBOLS
    ):
        return False
    succeeded = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "symbol",
            "price",
            "change_percent",
        }:
            return False
        price = row.get("price")
        change = row.get("change_percent")
        if price is None:
            if change is not None:
                return False
            continue
        if not _finite_number(price, minimum=0.0000001) or not _finite_number(change):
            return False
        succeeded += 1
    return bool(
        succeeded > 0
        and payload.get("attempted") == len(PUBLIC_HOME_INDEX_SYMBOLS)
        and payload.get("succeeded") == succeeded
        and isinstance(payload.get("data_limited"), bool)
        and payload.get("source_status") in {"active", "degraded"}
        and _valid_iso_timestamp(payload.get("as_of"))
    )


def _validate_overview(payload: Mapping[str, Any]) -> bool:
    numeric_fields = (
        "change",
        "change_percent",
        "volume",
        "market_cap",
        "prev_close",
        "high",
        "low",
        "open",
        "pe_ratio",
        "dividend_yield",
        "year_high",
        "year_low",
    )
    logo_urls = payload.get("logo_urls")
    return bool(
        set(payload) == _OVERVIEW_FIELDS
        and payload.get("ticker") == PUBLIC_HOME_DEFAULT_TICKER
        and isinstance(payload.get("name"), str)
        and isinstance(payload.get("name_en"), str)
        and all(
            isinstance(payload.get(field), str)
            for field in ("description", "description_en", "sic_description")
        )
        and payload.get("price_provider") in {"Massive", "Yahoo/yfinance"}
        and _valid_iso_timestamp(payload.get("as_of"))
        and _string_or_none(payload.get("website"))
        and _string_or_none(payload.get("logo_url"))
        and isinstance(logo_urls, list)
        and len(logo_urls) <= 8
        and all(isinstance(item, str) for item in logo_urls)
        and _finite_number(payload.get("price"), minimum=0.0000001)
        and all(_optional_finite(payload.get(field)) for field in numeric_fields)
    )


def _validate_chart_for_ticker(
    payload: Mapping[str, Any],
    *,
    expected_ticker: str,
) -> bool:
    bars = payload.get("bars")
    if (
        set(payload) != _CHART_FIELDS
        or payload.get("ticker") != expected_ticker
        or payload.get("range") != "1d"
        or payload.get("period") != "2y"
        or payload.get("interval") != "1d"
        or payload.get("exchange_timezone") != "America/New_York"
        or payload.get("price_adjustment") != "raw"
        or payload.get("include_extended_hours") is not False
        or payload.get("moving_average_scope") != "regular_session_only"
        or payload.get("source_status") != "active"
        or not isinstance(payload.get("visible"), int)
        or isinstance(payload.get("visible"), bool)
        or not _valid_iso_timestamp(payload.get("as_of"))
        or not _string_or_none(payload.get("last_bar_at"))
        or (
            payload.get("last_bar_at") is not None
            and not _valid_iso_timestamp(payload.get("last_bar_at"))
        )
        or not isinstance(bars, list)
        or not 1 <= len(bars) <= 5_000
    ):
        return False
    previous_time = -1
    for bar in bars:
        if not isinstance(bar, dict) or set(bar) != {
            "t",
            "o",
            "h",
            "l",
            "c",
            "v",
            "ext",
            "quote_only",
            "session",
        }:
            return False
        timestamp = bar.get("t")
        prices = [bar.get(field) for field in ("o", "h", "l", "c")]
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp <= previous_time
            or not all(_finite_number(value, minimum=0.0000001) for value in prices)
            or not _finite_number(bar.get("v", 0), minimum=0)
            or not isinstance(bar.get("ext"), bool)
            or not isinstance(bar.get("quote_only"), bool)
            or bar.get("session") != "regular"
        ):
            return False
        open_price, high, low, close = (float(value) for value in prices)
        if low > min(open_price, close) or high < max(open_price, close) or low > high:
            return False
        previous_time = timestamp
    for field in ("ema20", "sma50"):
        values = payload.get(field)
        if not isinstance(values, list) or len(values) > len(bars):
            return False
        for item in values:
            if (
                not isinstance(item, dict)
                or set(item) != {"time", "value"}
                or isinstance(item.get("time"), bool)
                or not isinstance(item.get("time"), int)
                or not _finite_number(item.get("value"), minimum=0.0000001)
            ):
                return False
    return True


def _validate_chart(payload: Mapping[str, Any]) -> bool:
    return _validate_chart_for_ticker(
        payload,
        expected_ticker=PUBLIC_HOME_DEFAULT_TICKER,
    )


def _validate_breakout_lead_chart(payload: Mapping[str, Any]) -> bool:
    ticker = payload.get("ticker")
    return bool(
        isinstance(ticker, str)
        and _BREAKOUT_TICKER_PATTERN.fullmatch(ticker)
        and _validate_chart_for_ticker(payload, expected_ticker=ticker)
    )


def _validate_signals(payload: Mapping[str, Any]) -> bool:
    signals = payload.get("signals")
    if not isinstance(signals, dict) or set(signals) != {
        "rsi",
        "macd",
        "ema20",
        "sma50",
        "volume",
    }:
        return False
    for item in signals.values():
        if (
            not isinstance(item, dict)
            or set(item) != {"value", "signal", "label"}
            or not _finite_number(item.get("value"))
            or not isinstance(item.get("signal"), str)
            or not isinstance(item.get("label"), str)
        ):
            return False
    return bool(
        set(payload) == {"ticker", "price", "score", "overall", "signals", "tags"}
        and payload.get("ticker") == PUBLIC_HOME_DEFAULT_TICKER
        and _finite_number(payload.get("price"), minimum=0.0000001)
        and _finite_number(payload.get("score"), minimum=0)
        and float(payload.get("score")) <= 100
        and payload.get("overall") in {"bullish", "bearish", "neutral"}
        and isinstance(payload.get("tags"), list)
        and len(payload.get("tags")) <= 8
        and all(isinstance(item, str) for item in payload.get("tags"))
    )


def _validate_market_signals(payload: Mapping[str, Any]) -> bool:
    signals = payload.get("signals")
    scores = payload.get("scores")
    if (
        set(payload) != {"signals", "scores", "as_of"}
        or not isinstance(signals, dict)
        or not 1 <= len(signals) <= 64
        or not isinstance(scores, dict)
        or set(scores) != _MARKET_SCORE_FIELDS
        or not _valid_iso_timestamp(payload.get("as_of"))
    ):
        return False
    source = signals.get("_source_status")
    breadth = signals.get("_breadth_coverage")
    if (
        not isinstance(source, dict)
        or set(source) != {"value", "label"}
        or source.get("value") not in {"active", "degraded"}
        or not isinstance(source.get("label"), str)
        or not isinstance(breadth, dict)
        or set(breadth) != {"available", "expected", "ratio"}
        or isinstance(breadth.get("available"), bool)
        or not isinstance(breadth.get("available"), int)
        or isinstance(breadth.get("expected"), bool)
        or not isinstance(breadth.get("expected"), int)
        or not 0 <= breadth["available"] <= breadth["expected"] <= 100
        or not _finite_number(breadth.get("ratio"), minimum=0)
        or float(breadth["ratio"]) > 1
    ):
        return False
    metric_count = 0
    for key, item in signals.items():
        if key.startswith("_"):
            continue
        metric_count += 1
        if (
            not isinstance(key, str)
            or key not in _MARKET_SIGNAL_FIELDS
            or not isinstance(item, dict)
            or set(item) != {"value", "label", "top_score", "bottom_score"}
            or not _optional_finite(item.get("value"))
            or not isinstance(item.get("label"), str)
            or not _optional_finite(item.get("top_score"), minimum=0)
            or not _optional_finite(item.get("bottom_score"), minimum=0)
        ):
            return False
    coverage = scores.get("coverage")
    top_breakdown = scores.get("top_breakdown")
    bottom_breakdown = scores.get("bottom_breakdown")
    if (
        not isinstance(coverage, dict)
        or set(coverage)
        != {
            "top_active_weight",
            "bottom_active_weight",
            "top_ratio",
            "bottom_ratio",
            "top_missing_components",
            "bottom_missing_components",
        }
        or not all(
            _finite_number(coverage.get(field), minimum=0)
            and float(coverage[field]) <= 1
            for field in (
                "top_active_weight",
                "bottom_active_weight",
                "top_ratio",
                "bottom_ratio",
            )
        )
        or not all(
            isinstance(coverage.get(field), list)
            and all(isinstance(item, str) for item in coverage[field])
            for field in ("top_missing_components", "bottom_missing_components")
        )
        or not isinstance(top_breakdown, dict)
        or set(top_breakdown) != _MARKET_TOP_BREAKDOWN_FIELDS
        or not isinstance(bottom_breakdown, dict)
        or set(bottom_breakdown) != _MARKET_BOTTOM_BREAKDOWN_FIELDS
        or not all(_optional_finite(value) for value in top_breakdown.values())
        or not all(_optional_finite(value) for value in bottom_breakdown.values())
    ):
        return False
    for field in ("top_reasons", "bottom_reasons"):
        reasons = scores.get(field)
        if (
            not isinstance(reasons, dict)
            or set(reasons) != {"raising", "suppressing"}
            or not all(
                isinstance(reasons.get(kind), list)
                and len(reasons[kind]) <= 3
                and all(isinstance(item, str) for item in reasons[kind])
                for kind in ("raising", "suppressing")
            )
        ):
            return False
    return bool(
        metric_count > 0
        and _optional_finite(scores.get("top_score"), minimum=0)
        and _optional_finite(scores.get("bottom_score"), minimum=0)
        and scores.get("top_status") in {"active", "insufficient_data"}
        and scores.get("bottom_status") in {"active", "insufficient_data"}
        and _finite_number(scores.get("data_quality"), minimum=0)
        and float(scores["data_quality"]) <= 100
        and _finite_number(scores.get("signal_data_quality"), minimum=0)
        and float(scores["signal_data_quality"]) <= 100
        and isinstance(scores.get("data_quality_available"), int)
        and not isinstance(scores.get("data_quality_available"), bool)
        and isinstance(scores.get("data_quality_expected"), int)
        and not isinstance(scores.get("data_quality_expected"), bool)
        and 0
        <= scores["data_quality_available"]
        <= scores["data_quality_expected"]
        <= 100
        and isinstance(scores.get("top_label"), str)
        and isinstance(scores.get("bottom_label"), str)
    )


def _validate_earnings(payload: Mapping[str, Any]) -> bool:
    rows = payload.get("earnings")
    attempted = payload.get("attempted")
    succeeded = payload.get("succeeded")
    providers = payload.get("providers")
    if (
        set(payload) != {
            "earnings",
            "attempted",
            "succeeded",
            "failed_symbols",
            "data_limited",
            "source_status",
            "providers",
            "as_of",
        }
        or not isinstance(rows, list)
        or len(rows) > 5_000
        or isinstance(attempted, bool)
        or not isinstance(attempted, int)
        or isinstance(succeeded, bool)
        or not isinstance(succeeded, int)
        or not 1 <= succeeded == len(rows) <= attempted <= 10_000
        or not isinstance(providers, list)
        or not 1 <= len(providers) <= 2
        or not all(isinstance(item, str) for item in providers)
        or len(set(providers)) != len(providers)
        or not set(providers).issubset({"Finnhub", "Yahoo Finance"})
        or not _valid_iso_timestamp(payload.get("as_of"))
    ):
        return False
    contributing_providers: set[str] = set()
    for row in rows:
        move = row.get("expected_move_pct") if isinstance(row, dict) else None
        move_metadata = (
            row.get("expected_move_expiration"),
            row.get("expected_move_source"),
            row.get("expected_move_observed_at"),
            row.get("expected_move_source_status"),
        ) if isinstance(row, dict) else ()
        actual_present = bool(
            isinstance(row, dict)
            and (
                row.get("eps_actual") is not None
                or row.get("revenue_actual") is not None
            )
        )
        if (
            not isinstance(row, dict)
            or set(row) != _EARNINGS_ROW_FIELDS
            or not isinstance(row.get("ticker"), str)
            or _EARNINGS_TICKER_PATTERN.fullmatch(row["ticker"]) is None
            or not isinstance(row.get("name"), str)
            or not row["name"]
            or len(row["name"]) > 256
            or not isinstance(row.get("days_until"), int)
            or isinstance(row.get("days_until"), bool)
            or not -3 <= row["days_until"] <= 180
            or row.get("timing") not in {None, "bmo", "amc"}
            or not all(
                _optional_finite(row.get(field))
                for field in (
                    "eps_estimate",
                    "eps_actual",
                    "eps_high",
                    "eps_low",
                    "revenue_estimate",
                    "revenue_actual",
                    "market_cap",
                )
            )
            or not isinstance(row.get("sector"), str)
            or row.get("earnings_date_source")
            not in {"calendar", "earnings_dates", "finnhub_calendar"}
            or row.get("estimate_source")
            not in {None, "calendar", "earnings_dates", "finnhub_calendar"}
            or row.get("actual_source")
            not in {None, "earnings_dates", "finnhub_calendar"}
            or row.get("release_status")
            not in {"scheduled", "released", "reported_pending_actual"}
            or actual_present != (row.get("release_status") == "released")
            or (actual_present and row.get("actual_source") is None)
            or (not actual_present and row.get("actual_source") is not None)
            or (
                row.get("quarter") is not None
                and (
                    isinstance(row.get("quarter"), bool)
                    or not isinstance(row.get("quarter"), int)
                    or not 1 <= row["quarter"] <= 4
                )
            )
            or (
                row.get("year") is not None
                and (
                    isinstance(row.get("year"), bool)
                    or not isinstance(row.get("year"), int)
                    or not 1900 <= row["year"] <= 2200
                )
            )
            or row.get("source_status") != "active"
            or not _valid_iso_timestamp(row.get("observed_at"))
            or (
                move is not None
                and (
                    not _finite_number(move, minimum=0.0000001)
                    or float(move) > 200
                    or not isinstance(row.get("expected_move_expiration"), str)
                    or not isinstance(row.get("expected_move_source"), str)
                    or row.get("expected_move_source")
                    != "Yahoo/yfinance options"
                    or not _valid_iso_timestamp(
                        row.get("expected_move_observed_at")
                    )
                    or row.get("expected_move_source_status") != "active"
                )
            )
            or (move is None and any(value is not None for value in move_metadata))
        ):
            return False
        try:
            date.fromisoformat(str(row.get("earnings_date")))
            if row.get("expected_move_expiration") is not None:
                date.fromisoformat(str(row["expected_move_expiration"]))
        except ValueError:
            return False
        if row.get("earnings_date_source") == "finnhub_calendar":
            contributing_providers.add("Finnhub")
        else:
            contributing_providers.add("Yahoo Finance")
    failed_symbols = payload.get("failed_symbols")
    return bool(
        set(providers) == contributing_providers
        and isinstance(failed_symbols, list)
        and len(failed_symbols) <= attempted
        and all(
            isinstance(item, str)
            and _EARNINGS_TICKER_PATTERN.fullmatch(item) is not None
            for item in failed_symbols
        )
        and isinstance(payload.get("data_limited"), bool)
        and payload.get("source_status") in {"active", "degraded"}
        and (
            (payload.get("source_status") == "degraded")
            == payload.get("data_limited")
        )
    )


def _validate_unusual(payload: Mapping[str, Any]) -> bool:
    rows = payload.get("results")
    attempted = payload.get("attempted")
    succeeded = payload.get("succeeded")
    if not (
        set(payload) == {
            "results",
            "data_limited",
            "source_status",
            "attempted",
            "succeeded",
            "failed_symbols",
            "partial_symbols",
            "as_of",
        }
        and isinstance(rows, list)
        and len(rows) <= 1_000
        and isinstance(attempted, int)
        and not isinstance(attempted, bool)
        and isinstance(succeeded, int)
        and not isinstance(succeeded, bool)
        and 1 <= succeeded <= attempted <= 1_000
        and _valid_iso_timestamp(payload.get("as_of"))
        and isinstance(payload.get("data_limited"), bool)
        and payload.get("source_status") in {"active", "degraded"}
        and all(
            isinstance(payload.get(field), list)
            and len(payload.get(field)) <= attempted
            and all(isinstance(item, str) for item in payload.get(field))
            for field in ("failed_symbols", "partial_symbols")
        )
    ):
        return False
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != _UNUSUAL_ROW_FIELDS
            or not isinstance(row.get("ticker"), str)
            or not isinstance(row.get("contract_ticker"), str)
            or row.get("contract_type") not in {"call", "put"}
            or row.get("type") not in {"call", "put"}
            or not _finite_number(row.get("strike"), minimum=0.0000001)
            or not isinstance(row.get("expiration"), str)
            or not _finite_number(row.get("volume"), minimum=0)
            or not _finite_number(row.get("open_interest"), minimum=0)
            or not _finite_number(row.get("oi"), minimum=0)
            or not _finite_number(row.get("vol_oi_ratio"), minimum=0)
            or not _finite_number(row.get("vol_oi"), minimum=0)
            or not all(
                _optional_finite(row.get(field), minimum=0)
                for field in (
                    "premium",
                    "last_price",
                    "implied_volatility",
                    "underlying_price",
                )
            )
            or not (
                row.get("in_the_money") is None
                or isinstance(row.get("in_the_money"), bool)
            )
            or not isinstance(row.get("moneyness"), str)
            or row.get("direction") is not None
            or not _finite_number(row.get("direction_confidence"), minimum=0)
            or not isinstance(row.get("direction_status"), str)
            or not isinstance(row.get("signal"), str)
            or not isinstance(row.get("inferred_direction"), str)
            or not isinstance(row.get("direction_deprecated"), bool)
        ):
            return False
    return True


_PAYLOAD_VALIDATORS = {
    "indices": _validate_indices,
    "focus_overview": _validate_overview,
    "focus_chart": _validate_chart,
    "focus_signals": _validate_signals,
    "market_signals": _validate_market_signals,
    "breakout_lead_chart": _validate_breakout_lead_chart,
    "earnings": _validate_earnings,
    "unusual": _validate_unusual,
}


def validate_public_home_payload(resource: str, payload: Any) -> dict[str, Any]:
    validator = _PAYLOAD_VALIDATORS.get(resource)
    if validator is None:
        raise ValueError("unknown public home resource")
    if not isinstance(payload, dict) or not _valid_json_tree(payload) or not validator(payload):
        raise ValueError(f"invalid public home payload: {resource}")
    return dict(payload)


def _valid_parameters(resource: str, parameters: Any) -> bool:
    if not isinstance(parameters, dict) or not _valid_json_tree(parameters):
        return False
    if resource == "earnings":
        if set(parameters) != {"market_date"}:
            return False
        try:
            date.fromisoformat(str(parameters["market_date"]))
        except ValueError:
            return False
        return True
    if resource == "breakout_lead_chart":
        try:
            expected = breakout_lead_chart_parameters(str(parameters.get("ticker")))
        except (AttributeError, ValueError):
            return False
        return parameters == expected
    return parameters == public_home_resource_parameters(resource, now=1_700_000_000.0)


def _payload_matches_parameters(
    resource: str,
    payload: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> bool:
    if resource == "breakout_lead_chart":
        return payload.get("ticker") == parameters.get("ticker")
    return True


def create_public_home_entry(
    resource: str,
    payload: Any,
    *,
    saved_at: float,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    spec = PUBLIC_HOME_RESOURCE_SPECS.get(resource)
    if spec is None:
        raise ValueError("unknown public home resource")
    if not _finite_number(saved_at, minimum=0.0000001):
        raise ValueError("public home saved_at is invalid")
    parameter_copy = dict(parameters)
    if not _valid_parameters(resource, parameter_copy):
        raise ValueError("public home parameters are invalid")
    payload_copy = validate_public_home_payload(resource, payload)
    if not _payload_matches_parameters(resource, payload_copy, parameter_copy):
        raise ValueError("public home payload does not match its parameters")
    if not _payload_timestamps_fit_entry(
        resource,
        payload_copy,
        not_after=float(saved_at) + PUBLIC_HOME_MAX_CLOCK_SKEW_SECONDS,
    ):
        raise ValueError("public home payload timestamp is later than saved_at")
    return {
        "payload": payload_copy,
        "saved_at": float(saved_at),
        "parameters": parameter_copy,
        "schema": spec.schema,
        "max_age": spec.max_age,
    }


def _validate_entry(resource: str, value: Any, *, now: float) -> dict[str, Any] | None:
    spec = PUBLIC_HOME_RESOURCE_SPECS.get(resource)
    if spec is None or not isinstance(value, dict) or set(value) != _ENTRY_FIELDS:
        return None
    saved_at = value.get("saved_at")
    if (
        not _finite_number(saved_at, minimum=0.0000001)
        or float(saved_at) > now
        or value.get("schema") != spec.schema
        or value.get("max_age") != spec.max_age
        or not _valid_parameters(resource, value.get("parameters"))
    ):
        return None
    try:
        payload = validate_public_home_payload(resource, value.get("payload"))
    except ValueError:
        return None
    if not _payload_matches_parameters(resource, payload, value["parameters"]):
        return None
    if not _payload_timestamps_fit_entry(
        resource,
        payload,
        not_after=now + PUBLIC_HOME_MAX_CLOCK_SKEW_SECONDS,
    ):
        return None
    return {
        "payload": payload,
        "saved_at": float(saved_at),
        "parameters": dict(value["parameters"]),
        "schema": spec.schema,
        "max_age": spec.max_age,
    }


def read_public_home_entries(
    path: Path | None = None,
    *,
    now: float | None = None,
) -> dict[str, dict[str, Any]]:
    target = path or get_data_paths().public_home_snapshot
    current = time.time() if now is None else float(now)
    try:
        if (
            not target.is_absolute()
            or _path_has_symlink_boundary(target)
            or not target.is_file()
        ):
            return {}
        with target.open("rb") as handle:
            raw = handle.read(PUBLIC_HOME_SNAPSHOT_MAX_BYTES + 1)
        if not raw or len(raw) > PUBLIC_HOME_SNAPSHOT_MAX_BYTES:
            return {}
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json,
        )
        if (
            not isinstance(document, dict)
            or set(document) != {"version", "resources"}
            or document.get("version") != PUBLIC_HOME_SNAPSHOT_VERSION
            or isinstance(document.get("version"), bool)
            or not isinstance(document.get("resources"), dict)
            or any(name not in PUBLIC_HOME_RESOURCE_SPECS for name in document["resources"])
        ):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for resource, value in document["resources"].items():
            entry = _validate_entry(resource, value, now=current)
            if entry is not None:
                result[resource] = entry
        return result
    except (
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return {}


def public_home_entry_is_servable(
    resource: str,
    entry: Mapping[str, Any] | None,
    *,
    parameters: Mapping[str, Any],
    now: float,
) -> bool:
    spec = PUBLIC_HOME_RESOURCE_SPECS.get(resource)
    if spec is None or not isinstance(entry, Mapping):
        return False
    saved_at = entry.get("saved_at")
    return bool(
        _finite_number(saved_at, minimum=0.0000001)
        and float(saved_at) <= now
        and now - float(saved_at) <= spec.max_age
        and entry.get("schema") == spec.schema
        and entry.get("max_age") == spec.max_age
        and entry.get("parameters") == dict(parameters)
    )


def read_public_home_resource(
    resource: str,
    *,
    parameters: Mapping[str, Any],
    path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    current = time.time() if now is None else float(now)
    entry = read_public_home_entries(path, now=current).get(resource)
    if not public_home_entry_is_servable(
        resource,
        entry,
        parameters=parameters,
        now=current,
    ):
        return None
    payload = dict(entry["payload"])
    age = max(0.0, current - float(entry["saved_at"]))
    payload["_stale"] = True
    payload["source_status"] = "degraded"
    payload["stale_reason"] = "public_snapshot_only"
    payload["stale_age_seconds"] = round(age, 1)
    payload["snapshot_saved_at"] = datetime.fromtimestamp(
        float(entry["saved_at"]), timezone.utc
    ).isoformat()
    return payload


async def read_public_home_resource_async(
    resource: str,
    *,
    parameters: Mapping[str, Any],
    path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        read_public_home_resource,
        resource,
        parameters=parameters,
        path=path,
        now=now,
    )


def read_owner_public_home_entry(
    resource: str,
    *,
    parameters: Mapping[str, Any],
    fresh_for_seconds: float,
    path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Return an exact hard-valid disk entry, marking old data as stale."""

    current = time.time() if now is None else float(now)
    if (
        isinstance(fresh_for_seconds, bool)
        or not isinstance(fresh_for_seconds, (int, float))
        or not math.isfinite(float(fresh_for_seconds))
        or float(fresh_for_seconds) <= 0
    ):
        raise ValueError("public home freshness window is invalid")
    entry = read_public_home_entries(path, now=current).get(resource)
    if not public_home_entry_is_servable(
        resource,
        entry,
        parameters=parameters,
        now=current,
    ):
        return None
    saved_at = float(entry["saved_at"])
    age = max(0.0, current - saved_at)
    fresh = age < float(fresh_for_seconds)
    payload = dict(entry["payload"])
    if not fresh:
        payload["_stale"] = True
        payload["source_status"] = "degraded"
        payload["stale_reason"] = "worker_snapshot_awaiting_refresh"
        payload["stale_age_seconds"] = round(age, 1)
        payload["snapshot_saved_at"] = datetime.fromtimestamp(
            saved_at,
            timezone.utc,
        ).isoformat()
    return {"payload": payload, "saved_at": saved_at, "fresh": fresh}


async def read_owner_public_home_entry_async(
    resource: str,
    *,
    parameters: Mapping[str, Any],
    fresh_for_seconds: float,
    path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    return await asyncio.to_thread(
        read_owner_public_home_entry,
        resource,
        parameters=parameters,
        fresh_for_seconds=fresh_for_seconds,
        path=path,
        now=now,
    )


def _path_has_symlink_boundary(path: Path) -> bool:
    """Reject an attacker-controlled target or immediate data directory."""

    try:
        return path.is_symlink() or path.parent.is_symlink()
    except OSError:
        return True


def write_public_home_snapshot(
    path: Path,
    entries: Mapping[str, Mapping[str, Any]],
    *,
    now: float | None = None,
) -> None:
    if not path.is_absolute():
        raise ValueError("public home snapshot path must be absolute")
    if _path_has_symlink_boundary(path):
        raise ValueError("public home snapshot path must not cross a symlink")
    current = time.time() if now is None else float(now)
    cleaned: dict[str, dict[str, Any]] = {}
    for resource, value in entries.items():
        if resource not in PUBLIC_HOME_RESOURCE_SPECS:
            raise ValueError("unknown public home resource")
        entry = _validate_entry(resource, value, now=current)
        if entry is None:
            raise ValueError(f"invalid public home entry: {resource}")
        cleaned[resource] = entry
    if not cleaned:
        raise ValueError("public home snapshot must contain at least one resource")
    encoded = json.dumps(
        {"version": PUBLIC_HOME_SNAPSHOT_VERSION, "resources": cleaned},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > PUBLIC_HOME_SNAPSHOT_MAX_BYTES:
        raise ValueError("public home snapshot exceeds the size limit")

    path.parent.mkdir(parents=True, exist_ok=True)
    if _path_has_symlink_boundary(path):
        raise ValueError("public home snapshot path must not cross a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if _path_has_symlink_boundary(path):
            raise ValueError("public home snapshot path changed during write")
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


__all__ = [
    "PUBLIC_HOME_DEFAULT_TICKER",
    "PUBLIC_HOME_INDEX_SYMBOLS",
    "PUBLIC_HOME_MAX_CLOCK_SKEW_SECONDS",
    "PUBLIC_HOME_OPTIONAL_RESOURCE_ORDER",
    "PUBLIC_HOME_RESOURCE_ORDER",
    "PUBLIC_HOME_RESOURCE_SPECS",
    "PUBLIC_HOME_SNAPSHOT_MAX_BYTES",
    "PUBLIC_HOME_SNAPSHOT_VERSION",
    "create_public_home_entry",
    "breakout_lead_chart_parameters",
    "public_home_entry_is_servable",
    "public_home_resource_parameters",
    "read_owner_public_home_entry",
    "read_owner_public_home_entry_async",
    "read_public_home_entries",
    "read_public_home_resource",
    "read_public_home_resource_async",
    "validate_public_home_payload",
    "write_public_home_snapshot",
]
