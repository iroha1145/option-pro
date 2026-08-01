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
from fastapi import APIRouter, Depends, HTTPException, Request

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


from app.services import earnings_enrichment
from app.services.http_read_cache import respond_with_snapshot, snapshot_version_key
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
    "market_cap_source",
    "market_cap_as_of",
    "market_cap_status",
    "sector",
    "earnings_date_source",
    "estimate_source",
    "actual_source",
    "release_status",
    "quarter",
    "year",
    "source_status",
    "observed_at",
    "public_featured",
    "featured_reasons",
    "calendar_sources",
    "calendar_date_status",
    "calendar_conflict",
    "expected_move_pct",
    "expected_move_expiration",
    "expected_move_source",
    "expected_move_observed_at",
    "expected_move_underlying_price",
    "expected_move_method",
    "expected_move_status",
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
    featured_reasons = [
        reason
        for reason in (value.get("featured_reasons") or [])
        if reason in {"market_cap", "earnings_pool"}
    ]
    calendar_sources = [
        source
        for source in (value.get("calendar_sources") or [])
        if source in {"yahoo", "finnhub_calendar", "fmp_calendar"}
    ]
    if not calendar_sources:
        # 单一来源的行也要有可追溯的日历来源标注。
        date_source = value.get("earnings_date_source")
        calendar_sources = [
            "fmp_calendar"
            if date_source == "fmp_calendar"
            else "finnhub_calendar"
            if date_source == "finnhub_calendar"
            else "yahoo"
        ]
    calendar_date_status = value.get("calendar_date_status")
    if calendar_date_status not in {"single_source", "confirmed", "conflict"}:
        calendar_date_status = (
            "confirmed" if len(calendar_sources) > 1 else "single_source"
        )
    calendar_conflict = (
        value.get("calendar_conflict")
        if calendar_date_status == "conflict"
        else None
    )
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
        "market_cap_source": (
            value.get("market_cap_source") if market_cap is not None else None
        ),
        "market_cap_as_of": (
            value.get("market_cap_as_of") if market_cap is not None else None
        ),
        # market_cap 缺失表示 unknown（不能当小公司证据），状态如实标注。
        "market_cap_status": (
            str(value.get("market_cap_status") or "active")
            if market_cap is not None
            else "unavailable"
        ),
        "sector": str(value.get("sector") or ""),
        "earnings_date_source": value.get("earnings_date_source"),
        "estimate_source": value.get("estimate_source"),
        "actual_source": actual_source,
        "release_status": release_status,
        "quarter": value.get("quarter"),
        "year": value.get("year"),
        "source_status": str(value.get("source_status") or "active"),
        "observed_at": value.get("observed_at"),
        "public_featured": bool(value.get("public_featured")),
        "featured_reasons": featured_reasons,
        "calendar_sources": calendar_sources,
        "calendar_date_status": calendar_date_status,
        "calendar_conflict": calendar_conflict,
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
        "expected_move_underlying_price": (
            value.get("expected_move_underlying_price")
            if expected_move is not None
            else None
        ),
        "expected_move_method": (
            value.get("expected_move_method")
            if expected_move is not None
            else None
        ),
        "expected_move_status": (
            "active"
            if expected_move is not None
            else value.get("expected_move_status")
        ),
    }
    return {field: normalized[field] for field in _EARNINGS_OUTPUT_FIELDS}


def _expected_move_from_chain_snapshot(snapshot: Any) -> float | None:
    """Calculate the at-the-money straddle move from one real option chain.

    委托共享实现（app.services.earnings_enrichment）：只认 bid/ask 派生的
    报价中值，宽价差按低质量报价拒绝，绝不用 last price 伪装成功。
    """

    if not isinstance(snapshot, dict):
        return None
    move = earnings_enrichment.compute_straddle_move(snapshot)
    return None if move is None else move["move_pct"]


def _expected_move_for_report(
    ticker: str,
    report_date: date,
    today: date,
    timing: str | None,
) -> dict[str, Any]:
    """Resolve one report's expected move through the provider priority chain.

    Massive Options（有权限时）→ MarketData（已配置时）→ Yahoo/yfinance 兜底；
    第一个成功值胜出，不做多来源平均。返回值直接就是行字段增量。
    """

    days_until = (report_date - today).days
    if not 0 <= days_until <= EXPECTED_MOVE_LOOKAHEAD_DAYS:
        return {}
    try:
        return earnings_enrichment.expected_move_for_report(
            ticker,
            report_date,
            today,
            timing,
        )
    except Exception:
        return {"expected_move_status": "unavailable:provider_error"}


@router.get("/upcoming")
async def upcoming_earnings(request: Request):
    """Serve the published earnings snapshot with conditional-GET support.

    The worker owns publication; ordinary GETs (owner included) never start a
    provider scan in password mode — POST /upcoming/refresh is the explicit
    rebuild. The 4MB body is served from the serialized-bytes cache with a
    strong ETag, so a repeat visit costs a 304 instead of a re-encode.
    """
    today = _market_today()
    key = f"earnings:upcoming:{today.isoformat()}"
    owner = current_request_is_owner()
    cache_control = "private, max-age=60, stale-while-revalidate=600"
    if not owner:
        # Snapshot-only, and deliberately off the owner's process-cache key:
        # a visitor must never observe the owner's live rebuild before the
        # worker publishes it (identity is part of every cache scope).
        now = time.time()
        payload = await read_public_home_resource_async(
            "earnings",
            parameters=public_home_resource_parameters("earnings", now=now),
            now=now,
        )
        if payload is None:
            raise public_snapshot_unavailable(key)
        return respond_with_snapshot(
            request,
            payload,
            version_key=snapshot_version_key(
                "earnings", "public", payload.get("snapshot_saved_at")
            ),
            cache_control=cache_control,
        )
    cached = cache.get(key)
    if cached is not None:
        # 进程缓存里存的是基础 payload——refresh_status 等请求域字段只加在
        # POST 返回的副本上，从不落进 cache.set。as_of 每次构建唯一，
        # (key, as_of) 即可稳定决定字节；此前 version_key=None 让 Owner 的
        # 每次命中都重付 json.dumps+sha256+gzip（审计 P2-01）。
        stamp = cached.get("as_of") if isinstance(cached, dict) else None
        return respond_with_snapshot(
            request,
            cached,
            version_key=(
                snapshot_version_key("earnings", "owner-live", key, stamp)
                if stamp
                else None
            ),
            cache_control=cache_control,
        )
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
            payload = disk_entry["payload"]
            if disk_entry["fresh"]:
                remaining = max(
                    1,
                    int(float(disk_entry["saved_at"]) + interval - now),
                )
                cache.set(key, payload, remaining)
            return respond_with_snapshot(
                request,
                payload,
                version_key=snapshot_version_key(
                    "earnings",
                    "owner",
                    disk_entry["saved_at"],
                    bool(disk_entry["fresh"]),
                ),
                cache_control=cache_control,
            )
        # No published snapshot at all: stay honest and unavailable instead
        # of letting an ordinary page read trigger a ~67-ticker provider scan.
        raise public_snapshot_unavailable(key)
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
    # FMP 是可选的第二日历来源：未配置时立即以 not_configured 短路，
    # 不产生任何请求，也不会影响 Finnhub 主路径。
    fmp_task = asyncio.create_task(
        earnings_enrichment.fetch_fmp_calendar(
            today,
            lookback_days=RECENT_EARNINGS_LOOKBACK_DAYS,
            lookahead_days=FINNHUB_EARNINGS_LOOKAHEAD_DAYS,
        )
    )

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
    fmp_result = await fmp_task
    finnhub_rows = (
        list(finnhub_result.get("rows") or [])
        if isinstance(finnhub_result, Mapping)
        else []
    )
    finnhub_selected = _select_finnhub_rows(finnhub_rows, today)
    fmp_rows = (
        list(fmp_result.get("rows") or [])
        if isinstance(fmp_result, Mapping)
        else []
    )
    # FMP 行与 Finnhub 行同形状，复用同一套「近发布优先/最近未来」选择规则。
    fmp_selected = _select_finnhub_rows(fmp_rows, today)
    completed = [r for r in results if isinstance(r, dict)]
    succeeded = [r for r in completed if r.get("ok")]
    failed_symbols = [
        ticker
        for ticker, result in zip(EARNINGS_TICKERS, results)
        if (
            (not isinstance(result, dict) or not result.get("ok"))
            and ticker not in finnhub_selected
            and ticker not in fmp_selected
        )
    ]
    if not succeeded and not finnhub_selected and not fmp_selected:
        raise HTTPException(status_code=503, detail="Earnings data is currently unavailable")
    earnings_by_ticker = {
        str(result["data"].get("ticker")): dict(result["data"])
        for result in succeeded
        if isinstance(result.get("data"), dict)
    }
    for row in earnings_by_ticker.values():
        row["calendar_sources"] = ["yahoo"]
        row["calendar_date_status"] = "single_source"
        row["calendar_conflict"] = None
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
            "calendar_sources": ["finnhub_calendar"],
            "calendar_date_status": "single_source",
            "calendar_conflict": None,
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

    # ── FMP 合并：交叉验证既有行的日期，补充 Finnhub 没覆盖的公司 ──
    for ticker, fmp_row in fmp_selected.items():
        existing = earnings_by_ticker.get(ticker)
        fmp_date = str(fmp_row.get("earnings_date") or "")
        if existing is not None:
            sources = list(existing.get("calendar_sources") or [])
            if "fmp_calendar" not in sources:
                sources.append("fmp_calendar")
            existing["calendar_sources"] = sources
            existing_date = str(existing.get("earnings_date") or "")
            if existing_date == fmp_date:
                existing["calendar_date_status"] = "confirmed"
                existing["calendar_conflict"] = None
            else:
                # 日期冲突必须可识别：主源（Finnhub/Yahoo）日期保留，
                # 次源日期原样记录，绝不静默合并成一条无法追踪的记录。
                existing["calendar_date_status"] = "conflict"
                existing["calendar_conflict"] = {"fmp_calendar": fmp_date}
            # 主源缺失的预期值可由次源补上（来源标注跟着换）。
            for field in ("eps_estimate", "revenue_estimate"):
                if existing.get(field) is None and fmp_row.get(field) is not None:
                    existing[field] = fmp_row[field]
                    existing["estimate_source"] = (
                        existing.get("estimate_source") or "fmp_calendar"
                    )
            continue
        release_status = (
            "released"
            if fmp_row.get("eps_actual") is not None
            or fmp_row.get("revenue_actual") is not None
            else "reported_pending_actual"
            if int(fmp_row.get("days_until") or 0) < 0
            else "scheduled"
        )
        earnings_by_ticker[ticker] = {
            **fmp_row,
            "ticker": ticker,
            "name": ticker,
            "market_cap": None,
            "sector": "",
            "earnings_date_source": "fmp_calendar",
            "estimate_source": (
                "fmp_calendar"
                if fmp_row.get("eps_estimate") is not None
                or fmp_row.get("revenue_estimate") is not None
                else None
            ),
            "actual_source": (
                "fmp_calendar" if release_status == "released" else None
            ),
            "release_status": release_status,
            "source_status": "active",
            "observed_at": observed_at,
            "calendar_sources": ["fmp_calendar"],
            "calendar_date_status": "single_source",
            "calendar_conflict": None,
        }
    earnings = list(earnings_by_ticker.values())

    # ── 市值：批量 + 持久缓存（Worker 低频刷新；绝不逐家请求资料） ──
    config = get_personal_config()
    market_caps = await earnings_enrichment.resolve_market_caps(
        earnings,
        cache_days=int(config.earnings.market_cap_cache_days),
    )
    for item in earnings:
        ticker = str(item.get("ticker") or "").upper()
        resolution = market_caps.get(ticker) or {
            "market_cap": None,
            "source": None,
            "as_of": None,
            "status": "unavailable",
        }
        item["market_cap"] = resolution["market_cap"]
        item["market_cap_source"] = resolution["source"]
        item["market_cap_as_of"] = resolution["as_of"]
        item["market_cap_status"] = resolution["status"]
        if not item.get("name") or item.get("name") == ticker:
            profile_name = resolution.get("name")
            if isinstance(profile_name, str) and profile_name:
                item["name"] = profile_name

    # ── 重点公司（公共部分）：市值门槛 或 公共关注池。
    #    账号自选属于账号上下文，由前端合并，绝不进入共享快照。 ──
    threshold = float(config.earnings.featured_market_cap_usd)
    public_pool = frozenset(EARNINGS_TICKERS)
    for item in earnings:
        featured, reasons = earnings_enrichment.featured_flags(
            str(item.get("ticker") or "").upper(),
            _to_optional_float(item.get("market_cap")),
            threshold=threshold,
            public_pool=public_pool,
        )
        item["public_featured"] = featured
        item["featured_reasons"] = reasons

    # ── 预期波动：只增强重点公司，且单次刷新有硬上限。
    #    日历覆盖本身绝不因增强预算而缩水。 ──
    expected_move_sem = asyncio.Semaphore(4)
    enrich_limit = int(config.earnings.expected_move_enrich_limit)
    enrich_candidates = [
        item
        for item in earnings
        if item.get("release_status") == "scheduled"
        and bool(item.get("public_featured"))
        and (report := _coerce_date(item.get("earnings_date"))) is not None
        and 0 <= (report - today).days <= EXPECTED_MOVE_LOOKAHEAD_DAYS
    ]
    enrich_candidates.sort(
        key=lambda item: (
            int(item.get("days_until") or 999),
            -(_to_optional_float(item.get("market_cap")) or 0.0),
            str(item.get("ticker") or ""),
        )
    )
    enrich_targets = {
        str(item.get("ticker") or "")
        for item in enrich_candidates[: max(0, enrich_limit)]
    }

    async def attach_expected_move(item: dict[str, Any]) -> None:
        for field in (
            "expected_move_pct",
            "expected_move_expiration",
            "expected_move_source",
            "expected_move_observed_at",
            "expected_move_underlying_price",
            "expected_move_method",
            "expected_move_status",
        ):
            item.pop(field, None)
        # 已发布/无日期的行同样落「not_enriched」：每行必有状态，
        # None 会被公开快照校验拒绝（不能靠校验器留活口掩盖丢状态）。
        if item.get("release_status") != "scheduled":
            item["expected_move_status"] = "not_enriched"
            return
        if str(item.get("ticker") or "") not in enrich_targets:
            item["expected_move_status"] = "not_enriched"
            return
        report_date = _coerce_date(item.get("earnings_date"))
        if report_date is None:
            item["expected_move_status"] = "not_enriched"
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
    attempted_symbols = (
        set(EARNINGS_TICKERS).union(finnhub_selected).union(fmp_selected)
    )
    finnhub_contributed = any(
        item.get("earnings_date_source") == "finnhub_calendar"
        for item in earnings
    )
    yahoo_contributed = any(
        item.get("earnings_date_source") in {"calendar", "earnings_dates"}
        for item in earnings
    )
    fmp_contributed = any(
        item.get("earnings_date_source") == "fmp_calendar"
        for item in earnings
    )
    providers = [
        name
        for name, contributed in (
            ("Finnhub", finnhub_contributed),
            ("Yahoo Finance", yahoo_contributed),
            ("FMP", fmp_contributed),
        )
        if contributed
    ]
    finnhub_limited = bool(
        not isinstance(finnhub_result, Mapping)
        or not finnhub_result.get("configured")
        or not finnhub_result.get("succeeded")
        or finnhub_result.get("truncated")
    )
    fmp_complete = bool(
        isinstance(fmp_result, Mapping)
        and fmp_result.get("configured")
        and fmp_result.get("succeeded")
    )
    # Finnhub is the primary full-market source for the visible -3..+30 day
    # window; FMP is an optional second full-market calendar. Coverage is
    # limited only when neither full-market source completed. Yahoo is a
    # curated 180-day enrichment and never downgrades the calendar.
    data_limited = finnhub_limited and not fmp_complete
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
