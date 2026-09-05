"""财报页增强：批量市值、第二日历来源（FMP）与预期波动 provider 链。

设计边界（与 docs/personal-edition 的访客只读模型一致）：
- 一切外呼只发生在 Worker/Owner 的日历构建里；普通页面读取只消费快照。
- FMP 未配置时所有路径干净短路，不影响启动与刷新（Finnhub 仍是主源）。
- 市值走批量接口 + 持久缓存（慢变量，低频刷新）；禁止逐家公司请求资料。
- 预期波动按 provider 优先级取第一个成功值（Massive Options → MarketData →
  Yahoo/yfinance），不把多个来源盲目平均；每个值保留 provider、expiration、
  observed_at、underlying、method 与失败原因。
- 宽价差、过期报价、缺少共同有效行权价 → 返回失败原因，绝不用 last price
  或 0 伪装成功。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from app.config import get_settings
from app.data_paths import get_data_paths

# ── 常量 ─────────────────────────────────────────────────────

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")
_FMP_CALENDAR_PATH = "/api/v3/earning_calendar"
_FMP_PROFILE_PATH = "/api/v3/profile"
_FMP_PROFILE_BATCH = 50
_FMP_PROFILE_MAX_BATCHES = 30
_FMP_TIMEOUT_SECONDS = 20.0

MARKET_CAP_CACHE_FILENAME = "earnings-market-caps-v1.json"
_MARKET_CAP_SOURCES = ("yahoo_info", "fmp_profile", "massive_reference")
_MASSIVE_DETAIL_BUDGET = 40

EXPECTED_MOVE_METHOD = "atm_straddle_mid"
_EXPECTED_MOVE_MAX_EXPIRY_GAP_DAYS = 14
_MAX_QUOTE_AGE_DAYS = 5
_MAX_SPREAD_RATIO = 0.5


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── FMP：第二财报日历来源 ────────────────────────────────────


def fmp_configured() -> bool:
    return bool(str(get_settings().fmp_api_key or "").strip())


def _fmp_result(
    *,
    rows: list[dict[str, Any]] | None = None,
    configured: bool,
    succeeded: bool,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "rows": list(rows or []),
        "configured": configured,
        "succeeded": succeeded,
        "error": error,
    }


def _fmp_error_code(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "unauthorized"
        if status == 429:
            return "rate_limited"
        return "http_error"
    if isinstance(exc, httpx.HTTPError):
        return "request_error"
    return "protocol_error"


async def fetch_fmp_calendar(
    today: date,
    *,
    lookback_days: int,
    lookahead_days: int,
) -> dict[str, Any]:
    """拉取 FMP 财报日历窗口；返回归一化行（与 Finnhub 行同形状）。"""

    settings = get_settings()
    token = str(settings.fmp_api_key or "").strip()
    if not token:
        return _fmp_result(configured=False, succeeded=False, error="not_configured")
    start = today - timedelta(days=lookback_days)
    end = today + timedelta(days=lookahead_days)
    try:
        async with httpx.AsyncClient(timeout=_FMP_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{str(settings.fmp_base_url).rstrip('/')}{_FMP_CALENDAR_PATH}",
                params={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "apikey": token,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - mapped to explicit status below
        return _fmp_result(
            configured=True,
            succeeded=False,
            error=_fmp_error_code(exc),
        )
    if not isinstance(payload, list):
        return _fmp_result(configured=True, succeeded=False, error="protocol_error")

    rows: list[dict[str, Any]] = []
    for value in payload:
        if not isinstance(value, dict):
            continue
        ticker = str(value.get("symbol") or "").strip().upper()
        report_date = _coerce_date(value.get("date"))
        if _SYMBOL_RE.fullmatch(ticker) is None or report_date is None:
            continue
        days_until = (report_date - today).days
        if not -lookback_days <= days_until <= lookahead_days:
            continue
        timing_raw = str(value.get("time") or "").strip().lower()
        rows.append(
            {
                "ticker": ticker,
                "earnings_date": report_date.isoformat(),
                "days_until": days_until,
                "timing": timing_raw if timing_raw in {"bmo", "amc"} else None,
                "eps_estimate": _finite(value.get("epsEstimated")),
                "eps_actual": _finite(value.get("eps") or value.get("epsActual")),
                "revenue_estimate": _finite(value.get("revenueEstimated")),
                "revenue_actual": _finite(
                    value.get("revenue") or value.get("revenueActual")
                ),
                "quarter": None,
                "year": None,
            }
        )
    if not rows:
        return _fmp_result(configured=True, succeeded=False, error="no_valid_rows")
    return _fmp_result(rows=rows, configured=True, succeeded=True)


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


async def fetch_fmp_profiles(tickers: list[str]) -> dict[str, Any]:
    """批量公司资料（mktCap/companyName/sector），50 家一批、批数有硬上限。"""

    settings = get_settings()
    token = str(settings.fmp_api_key or "").strip()
    if not token:
        return {
            "configured": False,
            "succeeded": False,
            "error": "not_configured",
            "profiles": {},
        }
    wanted = [t for t in dict.fromkeys(tickers) if _SYMBOL_RE.fullmatch(t)]
    batches = [
        wanted[offset : offset + _FMP_PROFILE_BATCH]
        for offset in range(0, len(wanted), _FMP_PROFILE_BATCH)
    ][:_FMP_PROFILE_MAX_BATCHES]
    profiles: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=_FMP_TIMEOUT_SECONDS) as client:
        for batch in batches:
            try:
                response = await client.get(
                    f"{str(settings.fmp_base_url).rstrip('/')}{_FMP_PROFILE_PATH}/"
                    + ",".join(batch),
                    params={"apikey": token},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                errors.append(_fmp_error_code(exc))
                continue
            if not isinstance(payload, list):
                errors.append("protocol_error")
                continue
            for value in payload:
                if not isinstance(value, dict):
                    continue
                ticker = str(value.get("symbol") or "").strip().upper()
                if _SYMBOL_RE.fullmatch(ticker) is None:
                    continue
                profiles[ticker] = {
                    "market_cap": _positive(value.get("mktCap")),
                    "name": str(value.get("companyName") or "").strip() or None,
                    "sector": str(value.get("sector") or "").strip() or None,
                }
    return {
        "configured": True,
        "succeeded": not errors and bool(batches),
        "error": errors[0] if errors else None,
        "profiles": profiles,
    }


# ── 市值：持久缓存 + 可信来源优先级 ──────────────────────────


def market_cap_cache_path() -> Path:
    return get_data_paths().watchlist_snapshot.parent / MARKET_CAP_CACHE_FILENAME


def load_market_cap_cache(path: Path | None = None) -> dict[str, dict[str, Any]]:
    target = path or market_cap_cache_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for ticker, entry in entries.items():
        if (
            not isinstance(ticker, str)
            or _SYMBOL_RE.fullmatch(ticker) is None
            or not isinstance(entry, dict)
        ):
            continue
        market_cap = _positive(entry.get("market_cap"))
        source = entry.get("source")
        as_of = entry.get("as_of")
        if (
            market_cap is None
            or source not in _MARKET_CAP_SOURCES
            or not isinstance(as_of, str)
        ):
            continue
        out[ticker] = {"market_cap": market_cap, "source": source, "as_of": as_of}
    return out


def store_market_cap_cache(
    entries: Mapping[str, Mapping[str, Any]],
    path: Path | None = None,
) -> None:
    target = path or market_cap_cache_path()
    payload = json.dumps(
        {"version": 1, "entries": dict(entries)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _cache_entry_fresh(entry: Mapping[str, Any], *, now: datetime, days: int) -> bool:
    try:
        observed = datetime.fromisoformat(str(entry.get("as_of")))
    except ValueError:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (now - observed) <= timedelta(days=days)


async def resolve_market_caps(
    rows: list[dict[str, Any]],
    *,
    cache_days: int,
    massive_detail_budget: int = _MASSIVE_DETAIL_BUDGET,
    cache_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """为窗口内的行解析市值：行内已有值 → 持久缓存 → FMP 批量 → Massive 兜底。

    返回 {ticker: {market_cap, source, as_of, status}}；status ∈
    {"active","cached","unavailable"}。market_cap 缺失一律 unavailable（unknown），
    绝不猜测为小公司。
    """

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    resolved: dict[str, dict[str, Any]] = {}
    cache = load_market_cap_cache(cache_path)
    cache_dirty = False

    missing: list[str] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker or ticker in resolved:
            continue
        row_cap = _positive(row.get("market_cap"))
        if row_cap is not None:
            # Yahoo per-ticker info（策展池）在本次构建里刚观测到——最可信。
            resolved[ticker] = {
                "market_cap": row_cap,
                "source": "yahoo_info",
                "as_of": str(row.get("observed_at") or now_iso),
                "status": "active",
            }
            cache[ticker] = {
                "market_cap": row_cap,
                "source": "yahoo_info",
                "as_of": now_iso,
            }
            cache_dirty = True
            continue
        cached = cache.get(ticker)
        if cached is not None and _cache_entry_fresh(cached, now=now, days=cache_days):
            resolved[ticker] = {**cached, "status": "cached"}
            continue
        missing.append(ticker)

    if missing:
        fmp = await fetch_fmp_profiles(missing)
        for ticker, profile in fmp.get("profiles", {}).items():
            market_cap = _positive(profile.get("market_cap"))
            if market_cap is None:
                continue
            resolved[ticker] = {
                "market_cap": market_cap,
                "source": "fmp_profile",
                "as_of": now_iso,
                "status": "active",
                "name": profile.get("name"),
                "sector": profile.get("sector"),
            }
            cache[ticker] = {
                "market_cap": market_cap,
                "source": "fmp_profile",
                "as_of": now_iso,
            }
            cache_dirty = True
        missing = [t for t in missing if t not in resolved]

    if missing and massive_detail_budget > 0:
        from app.services import massive

        if massive.configured():
            # 只为近端仍缺失的少数代码做单只详情兜底，绝不批量扫全市场。
            by_urgency = sorted(
                missing,
                key=lambda ticker: next(
                    (
                        int(row.get("days_until") or 999)
                        for row in rows
                        if str(row.get("ticker") or "").upper() == ticker
                    ),
                    999,
                ),
            )
            for ticker in by_urgency[:massive_detail_budget]:
                try:
                    detail = await asyncio.to_thread(
                        massive.reference_ticker_detail, ticker
                    )
                except Exception:  # noqa: BLE001 - provider soft-fails per ticker
                    continue
                market_cap = _positive(detail.get("market_cap"))
                if market_cap is None:
                    continue
                resolved[ticker] = {
                    "market_cap": market_cap,
                    "source": "massive_reference",
                    "as_of": now_iso,
                    "status": "active",
                    "name": detail.get("name"),
                }
                cache[ticker] = {
                    "market_cap": market_cap,
                    "source": "massive_reference",
                    "as_of": now_iso,
                }
                cache_dirty = True
            missing = [t for t in missing if t not in resolved]

    # 过期缓存好过没有：明确标 cached（as_of 保留原时间，可识别陈旧）。
    for ticker in list(missing):
        cached = cache.get(ticker)
        if cached is not None:
            resolved[ticker] = {**cached, "status": "cached"}
            missing.remove(ticker)

    for ticker in missing:
        resolved[ticker] = {
            "market_cap": None,
            "source": None,
            "as_of": None,
            "status": "unavailable",
        }

    if cache_dirty:
        try:
            store_market_cap_cache(cache, cache_path)
        except OSError:
            pass
    return resolved


# ── 重点公司判定（公共部分；账号自选属账号上下文，由前端合并） ──


def featured_flags(
    ticker: str,
    market_cap: float | None,
    *,
    threshold: float,
    public_pool: frozenset[str] | set[str],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if market_cap is not None and market_cap >= threshold:
        reasons.append("market_cap")
    if ticker in public_pool:
        reasons.append("earnings_pool")
    return bool(reasons), reasons


# ── 预期波动：ATM straddle 数学（共享） ──────────────────────


def _contract_mark(contract: Mapping[str, Any]) -> float | None:
    """报价中值；只认 bid/ask 派生的 midpoint/mid，last price 不算报价。"""

    if not isinstance(contract, Mapping):
        return None
    bid = _finite(contract.get("bid"))
    ask = _finite(contract.get("ask"))
    if bid is not None and ask is not None:
        if bid <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2
        if mid <= 0:
            return None
        # 宽价差是低质量报价：中值不可信，宁缺毋滥。
        if (ask - bid) > max(_MAX_SPREAD_RATIO * mid, 0.05):
            return None
        return mid
    for field in ("midpoint", "mid"):
        value = _positive(contract.get(field))
        if value is not None:
            return value
    return None


def compute_straddle_move(
    snapshot: Mapping[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any] | None:
    """从一份真实链快照算 ATM call+put 直跨式涨跌幅（%）。"""

    if not isinstance(snapshot, Mapping):
        return None
    underlying = _positive(snapshot.get("underlying_price"))
    if underlying is None:
        return None
    def quotes(side: str) -> dict[float, tuple[float, Any]]:
        out: dict[float, tuple[float, Any]] = {}
        for row in snapshot.get(side) or []:
            if not isinstance(row, Mapping):
                continue
            strike = _positive(row.get("strike"))
            mark = _contract_mark(row)
            # A timestamp from another strike cannot validate this quote.
            # Providers with per-contract times explicitly include the field,
            # even when absent, so a missing time cannot inherit the chain's.
            observed_at = row.get("quote_as_of", snapshot.get("as_of"))
            if today is not None and not _quote_is_fresh(observed_at, today):
                continue
            if strike is not None and mark is not None:
                out[strike] = (mark, observed_at)
        return out

    calls = quotes("calls")
    puts = quotes("puts")
    common = set(calls).intersection(puts)
    if not common:
        return None
    strike = min(common, key=lambda value: abs(value - underlying))
    move = (calls[strike][0] + puts[strike][0]) / underlying * 100
    if not math.isfinite(move) or move <= 0 or move > 200:
        return None
    result = {
        "move_pct": round(move, 2),
        "strike": strike,
        "underlying_price": underlying,
    }
    if today is not None:
        result["observed_at"] = min(
            (calls[strike][1], puts[strike][1]),
            key=_quote_datetime,
        )
    return result


def _quote_epoch_iso(value: Any) -> str | None:
    epoch = _positive(value)
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _quote_datetime(observed_at: Any) -> datetime | None:
    try:
        observed = datetime.fromisoformat(str(observed_at))
    except (TypeError, ValueError):
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed


def _quote_is_fresh(observed_at: Any, today: date) -> bool:
    observed = _quote_datetime(observed_at)
    if observed is None:
        return False
    return (
        datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - observed
    ) <= timedelta(days=_MAX_QUOTE_AGE_DAYS)


def _expiration_window(
    report_date: date,
    timing: str | None,
) -> tuple[date, date]:
    minimum = report_date if timing == "bmo" else report_date + timedelta(days=1)
    return minimum, report_date + timedelta(days=_EXPECTED_MOVE_MAX_EXPIRY_GAP_DAYS)


def _failure(reason: str) -> dict[str, Any]:
    return {"expected_move_status": f"unavailable:{reason}"}


def _success(
    *,
    move: Mapping[str, Any],
    expiration: str,
    source: str,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "expected_move_pct": move["move_pct"],
        "expected_move_expiration": expiration,
        "expected_move_source": source,
        "expected_move_observed_at": move.get("observed_at") or observed_at,
        "expected_move_underlying_price": round(float(move["underlying_price"]), 4),
        "expected_move_method": EXPECTED_MOVE_METHOD,
        "expected_move_status": "active",
    }


def _massive_expected_move(
    ticker: str,
    report_date: date,
    today: date,
    timing: str | None,
) -> dict[str, Any]:
    from app.services import massive

    if not massive.configured():
        return _failure("not_configured")
    if massive.options_capability_known_denied():
        return _failure("not_permitted")
    minimum, maximum = _expiration_window(report_date, timing)
    try:
        expirations = massive.option_expirations(
            ticker,
            date_gte=minimum.isoformat(),
            date_lte=maximum.isoformat(),
        )
        parsed = sorted(
            candidate
            for raw in expirations
            if (candidate := _coerce_date(raw)) is not None
            and minimum <= candidate <= maximum
        )
        if not parsed:
            return _failure("no_expiration")
        expiration = parsed[0].isoformat()
        snapshot = massive.option_chain_snapshot(ticker, expiration)
    except massive.MassiveError as error:
        if error.code == "plan":
            return _failure("not_permitted")
        return _failure("provider_error")
    except Exception:  # noqa: BLE001
        return _failure("provider_error")
    observed_at = snapshot.get("as_of")
    if not isinstance(observed_at, str) or not observed_at:
        return _failure("no_quote_time")
    if not _quote_is_fresh(observed_at, today):
        return _failure("stale_quote")
    move = compute_straddle_move(snapshot, today=today)
    if move is None:
        return _failure("no_usable_straddle")
    return _success(
        move=move,
        expiration=expiration,
        source="Massive options",
        observed_at=observed_at,
    )


def _marketdata_expected_move(
    ticker: str,
    report_date: date,
    today: date,
    timing: str | None,
) -> dict[str, Any]:
    settings = get_settings()
    token = str(settings.marketdata_token or "").strip()
    if not token:
        return _failure("not_configured")
    base_url = str(settings.marketdata_base_url).rstrip("/")
    minimum, maximum = _expiration_window(report_date, timing)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=10.0, headers=headers) as client:
            expirations_response = client.get(
                f"{base_url}/v1/options/expirations/{ticker}/"
            )
            expirations_response.raise_for_status()
            expirations_payload = expirations_response.json()
            raw_expirations = (
                expirations_payload.get("expirations")
                if isinstance(expirations_payload, dict)
                else None
            )
            if not isinstance(raw_expirations, list):
                return _failure("provider_error")
            parsed = sorted(
                candidate
                for raw in raw_expirations
                if (candidate := _coerce_date(raw)) is not None
                and minimum <= candidate <= maximum
            )
            if not parsed:
                return _failure("no_expiration")
            expiration = parsed[0].isoformat()
            chain_response = client.get(
                f"{base_url}/v1/options/chain/{ticker}/",
                params={
                    "expiration": expiration,
                    "range": "all",
                    "columns": (
                        "side,strike,bid,ask,mid,underlyingPrice,updated"
                    ),
                },
            )
            chain_response.raise_for_status()
            chain = chain_response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            return _failure("not_permitted")
        return _failure("provider_error")
    except Exception:  # noqa: BLE001
        return _failure("provider_error")
    if not isinstance(chain, dict):
        return _failure("provider_error")
    sides = chain.get("side") or []
    strikes = chain.get("strike") or []
    bids = chain.get("bid") or []
    asks = chain.get("ask") or []
    underlying_prices = chain.get("underlyingPrice") or []
    updated = chain.get("updated") or []
    count = min(len(sides), len(strikes), len(bids), len(asks))
    calls: list[dict[str, Any]] = []
    puts: list[dict[str, Any]] = []
    for index in range(count):
        contract = {
            "strike": _finite(strikes[index]),
            "bid": _finite(bids[index]),
            "ask": _finite(asks[index]),
            "quote_as_of": _quote_epoch_iso(updated[index]) if index < len(updated) else None,
        }
        side = str(sides[index] or "").lower()
        if side == "call":
            calls.append(contract)
        elif side == "put":
            puts.append(contract)
    underlying = next(
        (value for value in underlying_prices if _positive(value) is not None),
        None,
    )
    observed_at = max(
        (row["quote_as_of"] for row in calls + puts if row["quote_as_of"] is not None),
        default=None,
    )
    if observed_at is None:
        return _failure("no_quote_time")
    if not _quote_is_fresh(observed_at, today):
        return _failure("stale_quote")
    move = compute_straddle_move(
        {"underlying_price": underlying, "calls": calls, "puts": puts},
        today=today,
    )
    if move is None:
        return _failure("no_usable_straddle")
    return _success(
        move=move,
        expiration=expiration,
        source="MarketData.app options",
        observed_at=observed_at,
    )


def _yahoo_expected_move(
    ticker: str,
    report_date: date,
    today: date,
    timing: str | None,
) -> dict[str, Any]:
    from app.services import yahoo as yahoo_provider

    minimum, maximum = _expiration_window(report_date, timing)
    try:
        expirations = yahoo_provider.get_expirations_snapshot(ticker).get(
            "expirations",
            [],
        )
        candidates: list[tuple[date, str]] = []
        for raw in expirations:
            parsed = _coerce_date(raw)
            if parsed is not None and minimum <= parsed <= maximum:
                candidates.append((parsed, str(raw)))
        if not candidates:
            return _failure("no_expiration")
        _expiration_date, expiration = min(candidates, key=lambda item: item[0])
        chain = yahoo_provider.get_option_chain(ticker, expiration)
    except Exception:  # noqa: BLE001
        return _failure("provider_error")
    if bool(chain.get("_stale")) or chain.get("source_status") not in {None, "active"}:
        return _failure("stale_quote")
    observed_at = chain.get("as_of")
    if not isinstance(observed_at, str) or not observed_at:
        return _failure("no_quote_time")
    if not _quote_is_fresh(observed_at, today):
        return _failure("stale_quote")
    move = compute_straddle_move(chain, today=today)
    if move is None:
        return _failure("no_usable_straddle")
    return _success(
        move=move,
        expiration=expiration,
        source="Yahoo/yfinance options",
        observed_at=observed_at,
    )


#: provider 优先级（名称 → 实现）；顺序即偏好，第一个成功值胜出。
EXPECTED_MOVE_PROVIDERS: tuple[
    tuple[str, Callable[[str, date, date, str | None], dict[str, Any]]],
    ...,
] = (
    ("massive", _massive_expected_move),
    ("marketdata", _marketdata_expected_move),
    ("yahoo", _yahoo_expected_move),
)


def expected_move_for_report(
    ticker: str,
    report_date: date,
    today: date,
    timing: str | None,
) -> dict[str, Any]:
    """按 provider 优先级取第一个成功的预期波动；失败保留最后一层原因。

    未配置/无权限的 provider 视为「不在场」而不是失败：真正的失败原因来自
    实际尝试过的最后一个 provider（通常是 Yahoo 兜底）。
    """

    last_failure: dict[str, Any] | None = None
    for _name, provider in EXPECTED_MOVE_PROVIDERS:
        result = provider(ticker, report_date, today, timing)
        status = str(result.get("expected_move_status") or "")
        if status == "active":
            return result
        if status not in {
            "unavailable:not_configured",
            "unavailable:not_permitted",
        }:
            last_failure = result
    return last_failure or _failure("not_configured")
