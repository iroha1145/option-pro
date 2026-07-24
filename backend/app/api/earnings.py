from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import math
import re
import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException

from app.access import (
    current_request_is_owner,
    public_snapshot_unavailable,
    require_same_origin_action,
)
from app.services.cache import cache
from app.public_home_snapshot import (
    public_home_resource_parameters,
    read_owner_public_home_entry_async,
    read_public_home_resource_async,
)
from app.personal_config import get_personal_config
from app.config import get_settings

router = APIRouter(prefix="/api/earnings", tags=["earnings"])

EARNINGS_TICKERS = [
    # Magnificent 7
    "NVDA", "TSLA", "AAPL", "AMZN", "META", "MSFT", "GOOGL",
    # Semiconductors
    "AMD", "AVGO", "TSM", "ASML", "MU", "INTC", "ARM", "QCOM", "MRVL", "AMAT", "LRCX", "KLAC",
    # Software / Cloud
    "CRM", "ORCL", "ADBE", "NOW", "SNOW", "PLTR", "NET", "PANW", "CRWD",
    # Consumer / Media
    "NFLX", "DIS", "BABA", "COST", "WMT", "TGT", "NKE", "SBUX", "MCD",
    # Finance
    "JPM", "GS", "MS", "V", "MA", "BAC", "C", "BLK",
    # Biotech / Pharma
    "LLY", "NVO", "ABBV", "AMGN", "GILD", "MRNA", "PFE", "JNJ", "UNH",
    # Energy
    "XOM", "CVX", "COP", "SLB",
    # Industrials / Others
    "BA", "CAT", "DE", "UPS", "FDX",
    # Chinese ADRs
    "PDD", "JD", "BIDU", "NIO", "LI", "XPEV",
]


from app.services.utils import sanitize as _sanitize

MARKET_TZ = ZoneInfo("America/New_York")
MAX_EARNINGS_LOOKAHEAD_DAYS = 180
RECENT_EARNINGS_LOOKBACK_DAYS = 3
FINNHUB_EARNINGS_LOOKAHEAD_DAYS = 30
FINNHUB_SEGMENT_DAYS = 7
FINNHUB_RESPONSE_HARD_LIMIT = 1_500
EXPECTED_MOVE_LOOKAHEAD_DAYS = 30
EXPECTED_MOVE_MAX_EXPIRY_GAP_DAYS = 14
MAX_FINNHUB_EARNINGS_ROWS = 5_000
MAX_EARNINGS_OUTPUT_ROWS = 5_000
EARNINGS_REFRESH_COOLDOWN_SECONDS = 60
_refresh_deadlines: dict[str, float] = {}
_EARNINGS_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")
_EARNINGS_OUTPUT_FIELDS = (
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
)


def _to_optional_float(value: Any) -> float | None:
    try:
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _first(value: Any) -> Any:
    """Return the first item from list-like calendar fields; pass scalars through."""
    if value is None:
        return None
    try:
        if hasattr(value, "iloc"):
            return value.iloc[0] if len(value) else None
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
    except Exception:
        return None
    return value


def _market_today() -> date:
    return datetime.now(MARKET_TZ).date()


async def _read_current_upcoming_earnings_snapshot(
    market_date: date | None = None,
) -> dict[str, Any] | None:
    """Read the shared current snapshot without starting another provider scan."""

    observed = market_date or _market_today()
    key = f"earnings:upcoming:{observed.isoformat()}"
    cached = cache.get(key)
    if isinstance(cached, dict):
        return cached
    config = get_personal_config()
    now = time.time()
    disk_entry = await read_owner_public_home_entry_async(
        "earnings",
        parameters=public_home_resource_parameters("earnings", now=now),
        fresh_for_seconds=float(config.public_home.earnings_seconds),
        now=now,
    )
    if (
        disk_entry is None
        or not bool(disk_entry.get("fresh"))
        or not isinstance(disk_entry.get("payload"), dict)
    ):
        return None
    payload = dict(disk_entry["payload"])
    remaining = max(
        1,
        int(
            float(disk_entry["saved_at"])
            + float(config.public_home.earnings_seconds)
            - now
        ),
    )
    return cache.set(key, payload, remaining)


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(MARKET_TZ)
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if value > 1_000_000:
                return datetime.fromtimestamp(value, MARKET_TZ).date()
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.lower() in {"nan", "nat", "none", "null", "-"}:
            return None
        if raw.isdigit():
            return _coerce_date(int(raw))
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            return None
    return None


def _collect_dates(value: Any) -> list[date]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        dates: list[date] = []
        for item in value:
            dates.extend(_collect_dates(item))
        return dates
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        try:
            return _collect_dates(value.tolist())
        except Exception:
            pass
    parsed = _coerce_date(value)
    return [parsed] if parsed else []


def _calendar_get(calendar: Any, key: str) -> Any:
    if calendar is None:
        return None
    try:
        if hasattr(calendar, "get"):
            value = calendar.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        if hasattr(calendar, "loc"):
            return calendar.loc[key]
    except Exception:
        return None
    return None


def _earnings_table(ticker_obj: yf.Ticker) -> Any:
    table = None
    try:
        if hasattr(ticker_obj, "get_earnings_dates"):
            table = ticker_obj.get_earnings_dates(limit=12)
    except Exception:
        table = None
    if table is None:
        try:
            table = ticker_obj.earnings_dates
        except Exception:
            table = None
    return table


def _table_value(row: Any, *names: str) -> Any:
    for name in names:
        try:
            if hasattr(row, "get"):
                value = row.get(name)
                if value is not None:
                    return value
        except Exception:
            pass
        try:
            value = row[name]
            if value is not None:
                return value
        except Exception:
            continue
    return None


def _earnings_records_from_table(ticker_obj: yf.Ticker) -> list[dict[str, Any]]:
    """Return dated provider rows, including reported EPS when it is available."""

    table = _earnings_table(ticker_obj)
    if table is None:
        return []
    records: list[dict[str, Any]] = []
    try:
        indexes = list(table.index)
    except Exception:
        return []
    for position, raw_date in enumerate(indexes):
        parsed = _coerce_date(raw_date)
        if parsed is None:
            continue
        try:
            row = table.iloc[position]
        except Exception:
            row = {}
        records.append(
            {
                "date": parsed,
                "eps_estimate": _to_optional_float(
                    _table_value(row, "EPS Estimate", "epsEstimate", "eps_estimate")
                ),
                "eps_actual": _to_optional_float(
                    _table_value(row, "Reported EPS", "reportedEPS", "eps_actual")
                ),
            }
        )
    return records


def _earnings_dates_from_table(ticker_obj: yf.Ticker) -> list[date]:
    return [record["date"] for record in _earnings_records_from_table(ticker_obj)]


def _recent_earnings_record(
    records: list[dict[str, Any]],
    today: date,
) -> dict[str, Any] | None:
    recent = [
        record
        for record in records
        if isinstance(record.get("date"), date)
        and 0 <= (today - record["date"]).days <= RECENT_EARNINGS_LOOKBACK_DAYS
    ]
    if not recent:
        return None
    return max(
        recent,
        key=lambda record: (
            record["date"],
            record.get("eps_actual") is not None,
        ),
    )


def _next_future_date(candidates: list[date], today: date) -> date | None:
    unique = sorted(set(candidates))
    for candidate in unique:
        days = (candidate - today).days
        if 0 <= days <= MAX_EARNINGS_LOOKAHEAD_DAYS:
            return candidate
    return None


def _finnhub_fetch_result(
    *,
    rows: list[dict[str, Any]] | None = None,
    configured: bool,
    succeeded: bool,
    truncated: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "rows": list(rows or []),
        "configured": configured,
        "succeeded": succeeded,
        "truncated": truncated,
        "error": error,
    }


async def _fetch_finnhub_earnings(today: date) -> dict[str, Any]:
    """Fetch a bounded US earnings calendar; Yahoo remains a supplement."""

    settings = get_settings()
    token = str(settings.finnhub_api_key or "").strip()
    if not token:
        return _finnhub_fetch_result(
            configured=False,
            succeeded=False,
            error="not_configured",
        )
    start = today - timedelta(days=RECENT_EARNINGS_LOOKBACK_DAYS)
    end = today + timedelta(days=FINNHUB_EARNINGS_LOOKAHEAD_DAYS)
    segments: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        segment_end = min(
            end,
            cursor + timedelta(days=FINNHUB_SEGMENT_DAYS - 1),
        )
        segments.append((cursor, segment_end))
        cursor = segment_end + timedelta(days=1)

    async def request_segment(
        client: httpx.AsyncClient,
        segment_start: date,
        segment_end: date,
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                f"{str(settings.finnhub_base_url).rstrip('/')}/calendar/earnings",
                params={
                    "from": segment_start.isoformat(),
                    "to": segment_end.isoformat(),
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            return {"rows": [], "succeeded": False, "error": "timeout"}
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error = (
                "unauthorized"
                if status_code in {401, 403}
                else "rate_limited"
                if status_code == 429
                else "http_error"
            )
            return {"rows": [], "succeeded": False, "error": error}
        except httpx.HTTPError:
            return {"rows": [], "succeeded": False, "error": "request_error"}
        except (TypeError, ValueError):
            return {"rows": [], "succeeded": False, "error": "protocol_error"}
        rows = (
            payload.get("earningsCalendar")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(rows, list):
            return {"rows": [], "succeeded": False, "error": "protocol_error"}
        return {"rows": rows, "succeeded": True, "error": None}

    raw_rows: list[Any] = []
    errors: list[str] = []
    truncated = False
    async with httpx.AsyncClient(
        timeout=20.0,
        headers={"X-Finnhub-Token": token},
    ) as client:
        segment_results = await asyncio.gather(
            *(
                request_segment(client, segment_start, segment_end)
                for segment_start, segment_end in segments
            )
        )
        saturated: list[tuple[date, date]] = []
        for segment, result in zip(segments, segment_results):
            if not result["succeeded"]:
                errors.append(str(result["error"] or "request_error"))
                continue
            rows = list(result["rows"])
            if len(rows) >= FINNHUB_RESPONSE_HARD_LIMIT:
                saturated.append(segment)
            else:
                raw_rows.extend(rows)

        daily_ranges = [
            (day, day)
            for segment_start, segment_end in saturated
            for offset in range((segment_end - segment_start).days + 1)
            for day in (segment_start + timedelta(days=offset),)
        ]
        if daily_ranges:
            daily_results = await asyncio.gather(
                *(
                    request_segment(client, day_start, day_end)
                    for day_start, day_end in daily_ranges
                )
            )
            for result in daily_results:
                if not result["succeeded"]:
                    errors.append(str(result["error"] or "request_error"))
                    continue
                rows = list(result["rows"])
                if len(rows) >= FINNHUB_RESPONSE_HARD_LIMIT:
                    truncated = True
                raw_rows.extend(rows)

    if not raw_rows:
        return _finnhub_fetch_result(
            configured=True,
            succeeded=False,
            truncated=truncated,
            error=errors[0] if errors else "empty_payload",
        )
    normalized: dict[str, dict[str, Any]] = {}
    for value in raw_rows:
        if not isinstance(value, dict):
            continue
        ticker = str(value.get("symbol") or "").strip().upper()
        report_date = _coerce_date(value.get("date"))
        if _EARNINGS_SYMBOL_RE.fullmatch(ticker) is None or report_date is None:
            continue
        days_until = (report_date - today).days
        if not (
            -RECENT_EARNINGS_LOOKBACK_DAYS
            <= days_until
            <= FINNHUB_EARNINGS_LOOKAHEAD_DAYS
        ):
            continue
        report_date_text = report_date.isoformat()
        row = {
            "ticker": ticker,
            "earnings_date": report_date_text,
            "days_until": days_until,
            "timing": (
                str(value.get("hour")).lower()
                if str(value.get("hour") or "").lower() in {"bmo", "amc"}
                else None
            ),
            "eps_estimate": _to_optional_float(value.get("epsEstimate")),
            "eps_actual": _to_optional_float(value.get("epsActual")),
            "revenue_estimate": _to_optional_float(value.get("revenueEstimate")),
            "revenue_actual": _to_optional_float(value.get("revenueActual")),
            "quarter": (
                int(value["quarter"])
                if isinstance(value.get("quarter"), int)
                else None
            ),
            "year": (
                int(value["year"])
                if isinstance(value.get("year"), int)
                else None
            ),
        }
        previous = normalized.get(ticker)
        if previous is not None:
            previous_date = str(previous.get("earnings_date") or "")
            if previous_date == report_date_text:
                # Finnhub occasionally emits the same company/date more than
                # once while actuals are being populated.
                normalized[ticker] = {
                    **previous,
                    **{
                        field: field_value
                        for field, field_value in row.items()
                        if field_value is not None
                    },
                }
                continue
            candidates = _select_finnhub_rows([previous, row], today)
            if ticker in candidates:
                normalized[ticker] = candidates[ticker]
            continue
        if len(normalized) >= MAX_FINNHUB_EARNINGS_ROWS:
            truncated = True
            continue
        if ticker in _select_finnhub_rows([row], today):
            normalized[ticker] = row
    if not normalized:
        return _finnhub_fetch_result(
            configured=True,
            succeeded=False,
            truncated=truncated,
            error=errors[0] if errors else "no_valid_rows",
        )
    return _finnhub_fetch_result(
        rows=list(normalized.values()),
        configured=True,
        succeeded=not errors,
        truncated=truncated,
        error=errors[0] if errors else None,
    )


def _select_finnhub_rows(
    rows: list[dict[str, Any]],
    today: date,
) -> dict[str, dict[str, Any]]:
    """Prefer a just-released report, otherwise the nearest future report."""

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for value in rows:
        if not isinstance(value, dict):
            continue
        ticker = str(value.get("ticker") or "").strip().upper()
        report_date = _coerce_date(value.get("earnings_date"))
        if _EARNINGS_SYMBOL_RE.fullmatch(ticker) is None or report_date is None:
            continue
        days_until = (report_date - today).days
        if not (
            -RECENT_EARNINGS_LOOKBACK_DAYS
            <= days_until
            <= MAX_EARNINGS_LOOKAHEAD_DAYS
        ):
            continue
        by_ticker.setdefault(ticker, []).append(
            {
                **value,
                "ticker": ticker,
                "earnings_date": report_date.isoformat(),
                "days_until": days_until,
            }
        )

    selected: dict[str, dict[str, Any]] = {}
    for ticker, matches in by_ticker.items():
        recent = [
            row
            for row in matches
            if -RECENT_EARNINGS_LOOKBACK_DAYS
            <= int(row.get("days_until") or 0)
            <= 0
        ]
        future = [
            row
            for row in matches
            if 0 <= int(row.get("days_until") or 0) <= MAX_EARNINGS_LOOKAHEAD_DAYS
        ]
        if recent:
            selected[ticker] = max(
                recent,
                key=lambda row: (
                    str(row.get("earnings_date") or ""),
                    (
                        row.get("eps_actual") is not None
                        or row.get("revenue_actual") is not None
                    ),
                ),
            )
        elif future:
            selected[ticker] = min(
                future,
                key=lambda row: str(row.get("earnings_date") or "9999"),
            )
    return selected


def _normalize_earnings_output_row(value: Mapping[str, Any]) -> dict[str, Any]:
    """Publish one stable row shape regardless of the contributing provider."""

    release_status = str(value.get("release_status") or "scheduled")
    eps_actual = value.get("eps_actual")
    revenue_actual = value.get("revenue_actual")
    market_cap = _to_optional_float(value.get("market_cap"))
    if market_cap is not None and market_cap <= 0:
        market_cap = None
    actual_source = value.get("actual_source")
    if (
        actual_source is None
        and release_status == "released"
        and (eps_actual is not None or revenue_actual is not None)
    ):
        actual_source = value.get("earnings_date_source")
    expected_move = value.get("expected_move_pct")
    normalized = {
        "ticker": str(value.get("ticker") or "").strip().upper(),
        "name": str(value.get("name") or value.get("ticker") or "").strip(),
        "earnings_date": str(value.get("earnings_date") or ""),
        "days_until": value.get("days_until"),
        "timing": value.get("timing"),
        "eps_estimate": value.get("eps_estimate"),
        "eps_actual": eps_actual,
        "eps_high": value.get("eps_high"),
        "eps_low": value.get("eps_low"),
        "revenue_estimate": value.get("revenue_estimate"),
        "revenue_actual": revenue_actual,
        "market_cap": market_cap,
        "sector": str(value.get("sector") or ""),
        "earnings_date_source": value.get("earnings_date_source"),
        "estimate_source": value.get("estimate_source"),
        "actual_source": actual_source,
        "release_status": release_status,
        "quarter": value.get("quarter"),
        "year": value.get("year"),
        "source_status": str(value.get("source_status") or "active"),
        "observed_at": value.get("observed_at"),
        "expected_move_pct": expected_move,
        "expected_move_expiration": (
            value.get("expected_move_expiration")
            if expected_move is not None
            else None
        ),
        "expected_move_source": (
            value.get("expected_move_source")
            if expected_move is not None
            else None
        ),
        "expected_move_observed_at": (
            value.get("expected_move_observed_at")
            if expected_move is not None
            else None
        ),
        "expected_move_source_status": (
            value.get("expected_move_source_status")
            if expected_move is not None
            else None
        ),
    }
    return {field: normalized[field] for field in _EARNINGS_OUTPUT_FIELDS}


def _option_mark(contract: Any) -> float | None:
    if not isinstance(contract, dict):
        return None
    for field in ("midpoint", "mid", "last_price"):
        value = _to_optional_float(contract.get(field))
        if value is not None and value > 0:
            return value
    return None


def _expected_move_from_chain_snapshot(snapshot: Any) -> float | None:
    """Calculate the at-the-money straddle move from one real option chain."""

    if not isinstance(snapshot, dict):
        return None
    underlying = _to_optional_float(snapshot.get("underlying_price"))
    if underlying is None or underlying <= 0:
        return None
    calls = {
        strike: row
        for row in snapshot.get("calls") or []
        if isinstance(row, dict)
        and (strike := _to_optional_float(row.get("strike"))) is not None
        and _option_mark(row) is not None
    }
    puts = {
        strike: row
        for row in snapshot.get("puts") or []
        if isinstance(row, dict)
        and (strike := _to_optional_float(row.get("strike"))) is not None
        and _option_mark(row) is not None
    }
    common_strikes = set(calls).intersection(puts)
    if not common_strikes:
        return None
    strike = min(common_strikes, key=lambda value: abs(value - underlying))
    call_mark = _option_mark(calls[strike])
    put_mark = _option_mark(puts[strike])
    if call_mark is None or put_mark is None:
        return None
    move = (call_mark + put_mark) / underlying * 100
    if not math.isfinite(move) or move <= 0 or move > 200:
        return None
    return round(move, 2)


def _expected_move_for_report(
    ticker: str,
    report_date: date,
    today: date,
    timing: str | None,
) -> dict[str, Any]:
    """Read one bounded real option chain for an upcoming earnings release."""

    days_until = (report_date - today).days
    if not 0 <= days_until <= EXPECTED_MOVE_LOOKAHEAD_DAYS:
        return {}
    try:
        from app.services import yahoo as yahoo_provider

        expirations = yahoo_provider.get_expirations_snapshot(ticker).get(
            "expirations",
            [],
        )
        candidates: list[tuple[date, str]] = []
        minimum_expiration = (
            report_date
            if timing == "bmo"
            else report_date + timedelta(days=1)
        )
        for raw in expirations:
            parsed = _coerce_date(raw)
            if (
                parsed is not None
                and minimum_expiration <= parsed
                <= report_date + timedelta(days=EXPECTED_MOVE_MAX_EXPIRY_GAP_DAYS)
            ):
                candidates.append((parsed, str(raw)))
        if not candidates:
            return {}
        _expiration_date, expiration = min(candidates, key=lambda item: item[0])
        chain = yahoo_provider.get_option_chain(ticker, expiration)
        if (
            bool(chain.get("_stale"))
            or chain.get("source_status") not in {None, "active"}
        ):
            return {}
        expected_move = _expected_move_from_chain_snapshot(chain)
        if expected_move is None:
            return {}
        observed_at = chain.get("as_of")
        if not isinstance(observed_at, str) or not observed_at:
            return {}
        return {
            "expected_move_pct": expected_move,
            "expected_move_expiration": expiration,
            "expected_move_source": "Yahoo/yfinance options",
            "expected_move_observed_at": observed_at,
            "expected_move_source_status": "active",
        }
    except Exception:
        return {}


@router.get("/upcoming")
async def upcoming_earnings():
    """Fetch real upcoming earnings dates from Yahoo Finance.

    Uses the locked cache helper so concurrent cold-cache requests share ONE
    fetch instead of each firing ~67 tickers worth of yfinance calls
    (thundering herd).
    """
    today = _market_today()
    key = f"earnings:upcoming:{today.isoformat()}"
    owner = current_request_is_owner()
    if not owner:
        cached = cache.get(key)
        if cached is None:
            now = time.time()
            cached = await read_public_home_resource_async(
                "earnings",
                parameters=public_home_resource_parameters("earnings", now=now),
                now=now,
            )
        if cached is None:
            raise public_snapshot_unavailable(key)
        return cached
    cached = cache.get(key)
    if cached is not None:
        return cached
    config = get_personal_config()
    if config.access.mode == "password":
        now = time.time()
        interval = float(config.public_home.earnings_seconds)
        disk_entry = await read_owner_public_home_entry_async(
            "earnings",
            parameters=public_home_resource_parameters("earnings", now=now),
            fresh_for_seconds=interval,
            now=now,
        )
        if disk_entry is not None:
            remaining = max(
                1,
                int(float(disk_entry["saved_at"]) + interval - now),
            )
            return cache.set(key, disk_entry["payload"], remaining)
    return await cache.get_or_set(
        key,
        3600,
        lambda: _build_upcoming_earnings(today),
    )


@router.post(
    "/upcoming/refresh",
    dependencies=[Depends(require_same_origin_action)],
)
async def refresh_upcoming_earnings():
    """Explicitly refresh the cached earnings snapshot.

    State-changing provider work uses POST; ordinary GET requests remain
    cache-only and never start a refresh.
    """
    today = _market_today()
    key = f"earnings:upcoming:{today.isoformat()}"

    now = time.monotonic()
    for expired_key in [
        item_key
        for item_key, deadline in _refresh_deadlines.items()
        if deadline <= now
    ]:
        _refresh_deadlines.pop(expired_key, None)
    cached = cache.get(key)
    deadline = _refresh_deadlines.get(key)
    if deadline is not None and deadline > now:
        retry_after = max(1, math.ceil(deadline - now))
        if isinstance(cached, dict):
            return _sanitize({
                **cached,
                "refresh_status": "cooldown",
                "refresh_retry_after_seconds": retry_after,
            })
        raise HTTPException(
            status_code=429,
            detail="Earnings refresh is cooling down",
            headers={"Retry-After": str(retry_after)},
        )

    # Reserve the cooldown before awaiting so simultaneous button presses
    # cannot start duplicate provider scans.
    _refresh_deadlines[key] = now + EARNINGS_REFRESH_COOLDOWN_SECONDS
    try:
        payload = await _build_upcoming_earnings(today)
    except Exception:
        if isinstance(cached, dict):
            return _sanitize({
                **cached,
                "_stale": True,
                "source_status": "stale",
                "refresh_status": "failed_stale",
                "refresh_error": "provider_refresh_failed",
                "refresh_retry_after_seconds": EARNINGS_REFRESH_COOLDOWN_SECONDS,
            })
        raise
    cached_is_complete = bool(
        isinstance(cached, dict)
        and cached.get("data_limited") is False
        and cached.get("source_status") == "active"
    )
    payload_is_limited = bool(
        not isinstance(payload, dict)
        or payload.get("data_limited") is not False
        or payload.get("source_status") != "active"
    )
    if cached_is_complete and payload_is_limited:
        # A transient provider outage must not replace a same-day complete
        # generation with a smaller hot-list fallback.
        return _sanitize({
            **cached,
            "_stale": True,
            "source_status": "stale",
            "refresh_status": "failed_stale",
            "refresh_error": "provider_refresh_incomplete",
            "refresh_retry_after_seconds": EARNINGS_REFRESH_COOLDOWN_SECONDS,
        })
    cache.set(key, payload, 3600)
    return _sanitize({
        **payload,
        "refresh_status": "refreshed",
        "refresh_retry_after_seconds": EARNINGS_REFRESH_COOLDOWN_SECONDS,
    })


async def _build_upcoming_earnings(today: date):
    sem = asyncio.Semaphore(8)
    finnhub_task = asyncio.create_task(_fetch_finnhub_earnings(today))

    async def fetch_one(ticker: str):
        def _work():
            try:
                tk = yf.Ticker(ticker)
                try:
                    cal = tk.calendar
                except Exception:
                    cal = None
                calendar_dates = _collect_dates(_calendar_get(cal, "Earnings Date"))
                next_date = _next_future_date(calendar_dates, today)
                earnings_date_source = "calendar" if next_date is not None else None
                table_records: list[dict[str, Any]] = []
                if (
                    next_date is None
                    or next_date == today
                    or (next_date - today).days > 45
                ):
                    # The dates table is a slower fallback. Avoid it when the
                    # lightweight calendar already has a near-term date. For a
                    # same-day or far-future date, also inspect recent rows:
                    # the table carries reported EPS, while lightweight
                    # calendars often roll forward immediately after release.
                    table_records = _earnings_records_from_table(tk)
                    table_dates = [record["date"] for record in table_records]
                    recent_record = _recent_earnings_record(table_records, today)
                    if recent_record is not None:
                        next_date = recent_record["date"]
                        earnings_date_source = "earnings_dates"
                    elif next_date is None:
                        next_date = _next_future_date(table_dates, today)
                        if next_date is not None:
                            earnings_date_source = "earnings_dates"

                source_observed = bool(calendar_dates or table_records)
                if next_date is None:
                    return {
                        "ticker": ticker,
                        "ok": source_observed,
                        "source_observed": source_observed,
                        "data": None,
                    }
                earnings_date = next_date.isoformat()
                observed_at = datetime.now(MARKET_TZ).isoformat()
                estimates_match_selected_date = next_date in set(calendar_dates)
                selected_table_record = next(
                    (
                        record
                        for record in table_records
                        if record.get("date") == next_date
                    ),
                    None,
                )

                # Full quote-summary info is expensive. Fetch it only after a
                # ticker has been confirmed as an upcoming earnings match.
                try:
                    info_value = tk.info
                    info = info_value if isinstance(info_value, dict) else {}
                except Exception:
                    info = {}
                name = info.get("shortName", ticker)

                eps_estimate = (
                    _to_optional_float(_first(_calendar_get(cal, "Earnings Average")))
                    if estimates_match_selected_date
                    else (
                        selected_table_record.get("eps_estimate")
                        if selected_table_record is not None
                        else None
                    )
                )
                eps_high = (
                    _to_optional_float(_first(_calendar_get(cal, "Earnings High")))
                    if estimates_match_selected_date
                    else None
                )
                eps_low = (
                    _to_optional_float(_first(_calendar_get(cal, "Earnings Low")))
                    if estimates_match_selected_date
                    else None
                )
                revenue_estimate = (
                    _to_optional_float(_first(_calendar_get(cal, "Revenue Average")))
                    if estimates_match_selected_date
                    else None
                )
                release_status = (
                    "released"
                    if selected_table_record is not None
                    and selected_table_record.get("eps_actual") is not None
                    else "reported_pending_actual"
                    if next_date < today
                    else "scheduled"
                )

                return {"ticker": ticker, "ok": True, "source_observed": True, "data": {
                    "ticker": ticker,
                    "name": name,
                    "earnings_date": earnings_date,
                    "days_until": (next_date - today).days,
                    "eps_estimate": eps_estimate,
                    "eps_actual": (
                        selected_table_record.get("eps_actual")
                        if selected_table_record is not None
                        else None
                    ),
                    "eps_high": eps_high,
                    "eps_low": eps_low,
                    "revenue_estimate": revenue_estimate,
                    "market_cap": _to_optional_float(info.get("marketCap")),
                    "sector": info.get("sector", ""),
                    "earnings_date_source": earnings_date_source,
                    "estimate_source": (
                        "calendar"
                        if estimates_match_selected_date
                        else "earnings_dates"
                        if selected_table_record is not None
                        and selected_table_record.get("eps_estimate") is not None
                        else None
                    ),
                    "release_status": release_status,
                    "source_status": "active",
                    "observed_at": observed_at,
                }}
            except Exception:
                return {"ticker": ticker, "ok": False, "source_observed": False, "data": None}

        async with sem:
            return await asyncio.to_thread(_work)

    results = await asyncio.gather(*[fetch_one(t) for t in EARNINGS_TICKERS], return_exceptions=True)
    finnhub_result = await finnhub_task
    finnhub_rows = (
        list(finnhub_result.get("rows") or [])
        if isinstance(finnhub_result, Mapping)
        else []
    )
    finnhub_selected = _select_finnhub_rows(finnhub_rows, today)
    completed = [r for r in results if isinstance(r, dict)]
    succeeded = [r for r in completed if r.get("ok")]
    failed_symbols = [
        ticker
        for ticker, result in zip(EARNINGS_TICKERS, results)
        if (
            (not isinstance(result, dict) or not result.get("ok"))
            and ticker not in finnhub_selected
        )
    ]
    if not succeeded and not finnhub_selected:
        raise HTTPException(status_code=503, detail="Earnings data is currently unavailable")
    earnings_by_ticker = {
        str(result["data"].get("ticker")): dict(result["data"])
        for result in succeeded
        if isinstance(result.get("data"), dict)
    }
    observed_at = datetime.now(MARKET_TZ).isoformat()
    for ticker, finnhub in finnhub_selected.items():
        existing = earnings_by_ticker.get(ticker, {})
        release_status = (
            "released"
            if finnhub.get("eps_actual") is not None
            or finnhub.get("revenue_actual") is not None
            else "reported_pending_actual"
            if int(finnhub.get("days_until") or 0) < 0
            else "scheduled"
        )
        earnings_by_ticker[ticker] = {
            **existing,
            **finnhub,
            "ticker": ticker,
            "name": existing.get("name") or ticker,
            "market_cap": existing.get("market_cap"),
            "sector": existing.get("sector") or "",
            "earnings_date_source": "finnhub_calendar",
            "estimate_source": "finnhub_calendar",
            "actual_source": (
                "finnhub_calendar" if release_status == "released" else None
            ),
            "release_status": release_status,
            "source_status": "active",
            "observed_at": observed_at,
            **(
                {
                    "expected_move_pct": None,
                    "expected_move_expiration": None,
                    "expected_move_source": None,
                    "expected_move_observed_at": None,
                }
                if release_status == "released"
                else {}
            ),
        }
    earnings = list(earnings_by_ticker.values())

    expected_move_sem = asyncio.Semaphore(4)
    expected_move_tickers = frozenset(EARNINGS_TICKERS)

    async def attach_expected_move(item: dict[str, Any]) -> None:
        for field in (
            "expected_move_pct",
            "expected_move_expiration",
            "expected_move_source",
            "expected_move_observed_at",
            "expected_move_source_status",
        ):
            item.pop(field, None)
        if item.get("release_status") != "scheduled":
            return
        # Full-market Finnhub coverage can contain thousands of reports.
        # Yahoo option lookups remain bounded to the curated liquid universe;
        # calendar coverage itself is never reduced by this enrichment limit.
        if str(item.get("ticker") or "") not in expected_move_tickers:
            return
        report_date = _coerce_date(item.get("earnings_date"))
        if report_date is None:
            return
        timing = str(item.get("timing") or "").lower()
        if timing not in {"bmo", "amc"}:
            timing = None
        async with expected_move_sem:
            expected_move = await asyncio.to_thread(
                _expected_move_for_report,
                str(item.get("ticker") or ""),
                report_date,
                today,
                timing,
            )
        item.update(expected_move)

    await asyncio.gather(
        *(attach_expected_move(item) for item in earnings),
        return_exceptions=False,
    )
    earnings = [_normalize_earnings_output_row(item) for item in earnings]
    earnings.sort(
        key=lambda item: (
            str(item.get("earnings_date") or "9999"),
            str(item.get("ticker") or ""),
        )
    )
    earnings = earnings[:MAX_EARNINGS_OUTPUT_ROWS]
    attempted_symbols = set(EARNINGS_TICKERS).union(finnhub_selected)
    finnhub_contributed = any(
        item.get("earnings_date_source") == "finnhub_calendar"
        for item in earnings
    )
    yahoo_contributed = any(
        item.get("earnings_date_source") in {"calendar", "earnings_dates"}
        for item in earnings
    )
    providers = [
        name
        for name, contributed in (
            ("Finnhub", finnhub_contributed),
            ("Yahoo Finance", yahoo_contributed),
        )
        if contributed
    ]
    finnhub_limited = bool(
        not isinstance(finnhub_result, Mapping)
        or not finnhub_result.get("configured")
        or not finnhub_result.get("succeeded")
        or finnhub_result.get("truncated")
    )
    # Finnhub is the full-market source for the visible -3..+30 day window.
    # Yahoo is a curated 180-day enrichment only, so a failed Yahoo profile
    # must not downgrade an otherwise complete full-market calendar.
    data_limited = finnhub_limited
    return _sanitize({
        "earnings": earnings,
        "attempted": len(attempted_symbols),
        "succeeded": len(earnings),
        "failed_symbols": failed_symbols,
        "data_limited": data_limited,
        "source_status": "degraded" if data_limited else "active",
        "providers": providers,
        "as_of": datetime.now(MARKET_TZ).isoformat(),
    })
