"""Restart-safe, read-only snapshots for the anonymous public home page."""

from __future__ import annotations

import asyncio
import json
import math
import os
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
    "earnings",
    "unusual",
)
_MARKET_TZ = ZoneInfo("America/New_York")
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
    "eps_estimate",
    "eps_high",
    "eps_low",
    "revenue_estimate",
    "market_cap",
    "sector",
    "earnings_date_source",
    "estimate_source",
    "source_status",
    "observed_at",
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
    "focus_overview": PublicHomeResourceSpec("focus-overview-v1", 24 * 60 * 60),
    "focus_chart": PublicHomeResourceSpec("focus-chart-v1", 7 * 24 * 60 * 60),
    "focus_signals": PublicHomeResourceSpec("focus-signals-v1", 7 * 24 * 60 * 60),
    "earnings": PublicHomeResourceSpec("earnings-upcoming-v1", 30 * 60 * 60),
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
    if resource == "earnings":
        return {
            "market_date": datetime.fromtimestamp(now, _MARKET_TZ).date().isoformat()
        }
    if resource == "unusual":
        return {"type": "all", "min_vol_oi": 1.0}
    raise KeyError(resource)


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
        budget = [100_000]
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
    if resource in {"indices", "focus_chart", "earnings", "unusual"}:
        iso_values.append(payload.get("as_of"))
    if resource == "focus_chart":
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
        and _string_or_none(payload.get("website"))
        and _string_or_none(payload.get("logo_url"))
        and isinstance(logo_urls, list)
        and len(logo_urls) <= 8
        and all(isinstance(item, str) for item in logo_urls)
        and _finite_number(payload.get("price"), minimum=0.0000001)
        and all(_optional_finite(payload.get(field)) for field in numeric_fields)
    )


def _validate_chart(payload: Mapping[str, Any]) -> bool:
    bars = payload.get("bars")
    if (
        set(payload) != _CHART_FIELDS
        or payload.get("ticker") != PUBLIC_HOME_DEFAULT_TICKER
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


def _validate_earnings(payload: Mapping[str, Any]) -> bool:
    rows = payload.get("earnings")
    attempted = payload.get("attempted")
    succeeded = payload.get("succeeded")
    if (
        set(payload) != {
            "earnings",
            "attempted",
            "succeeded",
            "failed_symbols",
            "data_limited",
            "source_status",
            "as_of",
        }
        or not isinstance(rows, list)
        or len(rows) > 1_000
        or isinstance(attempted, bool)
        or not isinstance(attempted, int)
        or isinstance(succeeded, bool)
        or not isinstance(succeeded, int)
        or not 1 <= succeeded <= attempted <= 1_000
        or not _valid_iso_timestamp(payload.get("as_of"))
    ):
        return False
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != _EARNINGS_ROW_FIELDS
            or not isinstance(row.get("ticker"), str)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("days_until"), int)
            or isinstance(row.get("days_until"), bool)
            or not all(
                _optional_finite(row.get(field))
                for field in (
                    "eps_estimate",
                    "eps_high",
                    "eps_low",
                    "revenue_estimate",
                    "market_cap",
                )
            )
            or not isinstance(row.get("sector"), str)
            or row.get("earnings_date_source")
            not in {"calendar", "earnings_dates"}
            or row.get("estimate_source") not in {None, "calendar"}
            or row.get("source_status") != "active"
            or not _valid_iso_timestamp(row.get("observed_at"))
        ):
            return False
        try:
            date.fromisoformat(str(row.get("earnings_date")))
        except ValueError:
            return False
    failed_symbols = payload.get("failed_symbols")
    return bool(
        isinstance(failed_symbols, list)
        and len(failed_symbols) <= attempted
        and all(isinstance(item, str) for item in failed_symbols)
        and isinstance(payload.get("data_limited"), bool)
        and payload.get("source_status") in {"active", "degraded"}
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
    return parameters == public_home_resource_parameters(resource, now=1_700_000_000.0)


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
    "PUBLIC_HOME_RESOURCE_ORDER",
    "PUBLIC_HOME_RESOURCE_SPECS",
    "PUBLIC_HOME_SNAPSHOT_MAX_BYTES",
    "PUBLIC_HOME_SNAPSHOT_VERSION",
    "create_public_home_entry",
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
