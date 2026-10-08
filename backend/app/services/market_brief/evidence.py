"""证据包：程序算好的事实，按块组装后交给模型解释。

约定（模型与校验共用）：
- 每条可引用的事实带 ``id``（``idx:^GSPC``、``sig:rsp_spy_5d``、``regime:market``、
  ``breadth:eod_batch``、``theme:<sector_id>``、``iv:<sector_id>``、``bo:<event_id>``、
  ``macro:composite`` / ``macro:<module_id>`` / ``macro:<factor_id>``、``hot:<event_group_id>``、
  ``news:<news_id>``、``earn:<TICKER>:<date>``、``cal:<event_id>``、``prior:<run_id>``）；
  研判里的 ``evidence_ids`` 只能取证据包里出现过的 id。
- 每个来源独立读取：缺失或失败只记入 ``coverage.missing_blocks``，不拖垮整份证据。
  缺失原因见 errors.py 的块原因码；意料之外的异常记 ``read_failed`` 并留下诊断。
- 只读：不调用会登记刷新需求、拉供应商或写盘的路由函数。
- 字节预算：紧凑 JSON 的 UTF-8 字节数不超过 ``max_bytes``；超限按新闻条数、主题领涨股、
  突破行数、日历行数、板块隐含波动率的顺序逐级裁剪，裁剪记入 ``coverage.trimmed``。
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, NamedTuple

from app.access import request_owner_access_context
from app.data_paths import get_data_paths
from app.failure_diagnostics import record_fallback_failure
from app.public_home_snapshot import (
    earnings_resource_parameters,
    public_home_resource_parameters,
    read_owner_public_home_entry,
)
from app.services.market_calendar import (
    ET,
    early_close_minutes,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
)
from app.services.sectors import SECTORS

from . import errors
from .schema import BriefSlot

if TYPE_CHECKING:
    from .store import BriefStore

EVIDENCE_VERSION = "market-brief-evidence-v1"
DEFAULT_MAX_BYTES = 56_000
# 没有全市场涨跌家数、均线上方占比、新高新低；广度只能用 11 只行业 ETF 代理。
BREADTH_BASIS = "sector_etf_proxy_11"
# 系统提示词与简体中文校验共用：证据里没有出现、但研判可以直接写代码的基准品种。
BENCHMARK_CODES: frozenset[str] = frozenset({
    "SPY", "QQQ", "IWM", "RSP", "DIA", "VIX", "^VIX", "HYG", "TLT", "IEF", "GLD", "USO", "UUP",
    "^GSPC", "^IXIC", "^DJI", "^RUT", "^SOX", "^N225", "000001.SS", "^TNX",
})

INDEX_NAMES_ZH: Mapping[str, str] = {
    "^GSPC": "标普500指数",
    "^IXIC": "纳斯达克综合指数",
    "^DJI": "道琼斯工业平均指数",
    "^N225": "日经225指数",
    "000001.SS": "上证综合指数",
}

# 主题名会被模型照抄进研判的板块名；简体中文校验不认「ADR」，这里给出能直接写的中文名。
_PROSE_SAFE_THEME_NAMES: Mapping[str, str] = {"china_adr": "中概股"}
# 突破雷达的状态与形态是英文枚举，正文不能照抄；给模型一份中文对照（只列本次出现的）。
BREAKOUT_STATE_NAMES_ZH: Mapping[str, str] = {
    "DISCOVERED": "刚发现",
    "WATCHING": "观察中",
    "TRIGGERED": "已触发",
    "CONFIRMED": "已确认",
    "HOLDING": "站稳",
    "RETESTING": "回踩中",
    "RETEST_HELD": "回踩守住",
    "REACCELERATING": "再加速",
    "EXTENDED": "涨幅过大",
    "FAILED": "突破失败",
    "EXPIRED": "已过期",
}
BREAKOUT_SETUP_NAMES_ZH: Mapping[str, str] = {
    "DAILY_BASE_BREAKOUT": "日线平台突破",
    "OPENING_RANGE_BREAKOUT": "开盘区间突破",
    "PREMARKET_GAP": "盘前跳空",
    "GAP_AND_GO": "跳空续涨",
    "GAP_HOLD": "跳空守住",
    "GAP_FADE": "跳空回落",
    "RETEST_BREAKOUT": "回踩后突破",
    "MOMENTUM_SPIKE": "动量急升",
    "RECOVERY_BREAKOUT": "修复性突破",
}

# 读 worker 快照时的「新鲜」窗口只决定 stale 标记；能不能用由各资源的硬期限决定。
_OWNER_FRESH_SECONDS = 2 * 60 * 60
_NEWS_LIMIT = 20
_FEED_LIMIT = 50
# 开盘前读最近 24 小时（隔夜到盘前）；收盘后读 36 小时：它要判断「价格表现与叙事是否
# 一致」，得把前一晚铺垫今天开盘的新闻也算进来，24 小时从深夜往回数会漏掉那一段。
_FEED_WINDOW_HOURS: Mapping[str, int] = {"pre_open": 24, "post_close": 36}
_SNIPPET_CHARS = 400
_HOT_SUMMARY_CHARS = 240
_HOT_TITLE_CHARS = 120
_HOT_TICKERS = 8
_HOT_SOURCE_NAMES = 5
_THEME_LEADERS = 3
_BREAKOUT_ROWS = 12
_EARNINGS_ROWS = 20
_EARNINGS_LOOKAHEAD_TRADING_DAYS = 2
_ECONOMIC_DAYS_BEFORE = 1
_ECONOMIC_DAYS_AFTER = 2
_SECTOR_IV_EACH_SIDE = 5
_WARNINGS = 5
_REFERENCE_VARIANT = "balanced|mid"
_ID_PATTERN = re.compile(r"^[a-z]+:[A-Za-z0-9_.^:\-]{1,90}$")
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.^:\-]")
_ID_MAX_LENGTH = 96


class _BlockMissing(Exception):
    """块内已知的缺失（快照不存在、功能关闭、没有条目），带原因码。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Block(NamedTuple):
    payload: Any
    through: Mapping[str, Any]


@dataclass(frozen=True)
class EvidencePack:
    payload: dict[str, Any]
    bytes: int
    coverage: dict[str, Any]
    allowed_codes: frozenset[str]
    evidence_ids: frozenset[str]
    source_texts: tuple[str, ...]
    data_through: dict[str, Any]

    def block_missing(self, block: str) -> bool:
        return any(item.get("block") == block for item in self.coverage.get("missing_blocks") or ())


@dataclass
class _Context:
    slot: BriefSlot
    trading_date: date
    now: datetime
    store: BriefStore | None
    settings: Any
    _catalyst: Any = field(default=None, repr=False)

    @property
    def epoch(self) -> float:
        return self.now.timestamp()

    def settings_or_default(self) -> Any:
        if self.settings is None:
            from app.config import get_settings

            self.settings = get_settings()
        return self.settings

    def catalyst_service(self) -> Any:
        # 新闻与经济日历共用一个服务实例：构造时会打开 AI 任务库与运行设置。
        if self._catalyst is None:
            self._catalyst = _catalyst_service()
        return self._catalyst


# ---------------------------------------------------------------------------
# 读取入口：模块级小函数，测试按名替换，不必合成整份上游快照。
# 重依赖（pandas、API 层、催化剂库）在函数内导入：API 进程只为读 store 也会导入本包。


def _read_public_home(resource: str, parameters: Mapping[str, Any], now_epoch: float) -> dict[str, Any] | None:
    return read_owner_public_home_entry(
        resource,
        parameters=parameters,
        fresh_for_seconds=_OWNER_FRESH_SECONDS,
        now=now_epoch,
    )


def _read_strength_context(now: datetime) -> Mapping[str, Any]:
    from app.services.eod_limited.context_snapshot import read_context_snapshot

    return read_context_snapshot(now=now)


def _read_eod_batch(root: Path | None = None) -> Mapping[str, Any] | None:
    from app.services.eod_limited.store import read_batch

    return read_batch(root)


def _read_sector_iv(sector_id: str, now_epoch: float) -> Mapping[str, Any] | None:
    # 只用无副作用的快照读取；路由里的 _request_iv_payload 会登记刷新需求。
    from app.api.sectors import _read_sector_iv_snapshot

    return _read_sector_iv_snapshot(sector_id, now=now_epoch)


def _read_breakout_scan() -> Mapping[str, Any] | None:
    from app.services.breakouts.repository import BreakoutRepository

    path = get_data_paths().optix_db
    if not path.is_file():
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    # 直接读仓库而不是 api/breakouts 的路由：路由会登记展示过的股票（写副作用）。
    return BreakoutRepository(path, read_only=True).latest_completed_scan()


def _read_macro_context() -> Mapping[str, Any] | None:
    from app.services.catalysts.local_intelligence import macro_conditions_context

    return macro_conditions_context()


def _macro_module_names() -> Mapping[str, str]:
    from app.services.macro_conditions.registry import MODULES_BY_ID

    return {module_id: spec.display_name_zh for module_id, spec in MODULES_BY_ID.items()}


def _catalyst_service() -> Any:
    from app.services.catalysts.config import get_catalyst_settings
    from app.services.catalysts.personal_service import PersonalCatalystService

    return PersonalCatalystService(get_catalyst_settings())


# ---------------------------------------------------------------------------
# 小工具


def _aware(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def _iso_z(value: Any) -> str | None:
    """统一成秒精度的 UTC ``...Z``；无法解析的字符串原样保留（只截断）。"""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or value <= 0:
            return None
        moment = datetime.fromtimestamp(float(value), timezone.utc)
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:40]
        if parsed.tzinfo is None:
            # 纯日期或不带时区的时刻：日期原样保留，时刻按 UTC 解读。
            return text[:40] if len(text) <= 10 else parsed.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        moment = parsed
    else:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finite(value: Any, digits: int = 2) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    return int(value)


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _finite(value, 4)
    if isinstance(value, str):
        return _clip(value, 200)
    return None


def _clip(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ticker(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    return text if 0 < len(text) <= 12 and re.fullmatch(r"[A-Z0-9.^\-]+", text) else None


def _evidence_id(prefix: str, raw: Any) -> str | None:
    """把上游 id 收敛到契约的 EvidenceId 字符集与长度；收敛不出合法值时返回 None。"""

    text = _ID_UNSAFE.sub("_", str(raw if raw is not None else "").strip())
    limit = min(90, _ID_MAX_LENGTH - len(prefix) - 1)
    candidate = f"{prefix}:{text[:limit]}"
    return candidate if text and _ID_PATTERN.fullmatch(candidate) else None


def _strings(values: Any, *, limit: int, chars: int = 80) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for value in values:
        text = _clip(value, chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _tickers(values: Any, *, limit: int) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for value in values:
        ticker = _ticker(value)
        if ticker and ticker not in result:
            result.append(ticker)
        if len(result) >= limit:
            break
    return result


def _theme_name(sector_id: str) -> str:
    return _PROSE_SAFE_THEME_NAMES.get(sector_id) or (SECTORS.get(sector_id) or {}).get("name") or sector_id


def _date_text(value: Any) -> str | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 各来源（块）。返回 _Block；已知缺失抛 _BlockMissing；其余异常在 build_evidence 的块边界兜底。


def _session(ctx: _Context) -> _Block:
    local = ctx.now.astimezone(ET)
    early = early_close_minutes(ctx.trading_date)
    close_minutes = early or 16 * 60
    return _Block(
        {
            "slot": ctx.slot,
            "trading_date": ctx.trading_date.isoformat(),
            "now_utc": _iso_z(ctx.now),
            "now_et": local.isoformat(timespec="minutes"),
            "trading_day": is_trading_day(ctx.trading_date),
            "early_close": early is not None,
            "close_time_et": f"{close_minutes // 60:02d}:{close_minutes % 60:02d}",
            "next_trading_day": next_trading_day(ctx.trading_date).isoformat(),
        },
        {},
    )


def _indices(ctx: _Context) -> _Block:
    entry = _read_public_home(
        "indices",
        public_home_resource_parameters("indices", now=ctx.epoch),
        ctx.epoch,
    )
    if entry is None:
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    payload = entry["payload"]
    rows = []
    for row in payload.get("indices") or []:
        if not isinstance(row, Mapping):
            continue
        symbol = _ticker(row.get("symbol"))
        evidence_id = _evidence_id("idx", symbol) if symbol else None
        if evidence_id is None:
            continue
        rows.append({
            "id": evidence_id,
            "symbol": symbol,
            "name_zh": INDEX_NAMES_ZH.get(symbol),
            "price": _finite(row.get("price")),
            "change_percent": _finite(row.get("change_percent")),
        })
    if not rows:
        raise _BlockMissing(errors.EMPTY)
    as_of = _iso_z(payload.get("as_of"))
    return _Block(
        {
            "as_of": as_of,
            "saved_at": _iso_z(entry.get("saved_at")),
            "stale": not bool(entry.get("fresh")),
            "unit": "change_percent 为百分数",
            "rows": rows,
        },
        {"indices": as_of},
    )


def _market_signals(ctx: _Context) -> _Block:
    entry = _read_public_home(
        "market_signals",
        public_home_resource_parameters("market_signals", now=ctx.epoch),
        ctx.epoch,
    )
    if entry is None:
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    payload = entry["payload"]
    signals = payload.get("signals") if isinstance(payload.get("signals"), Mapping) else {}
    rows = []
    for name in sorted(signals):
        item = signals[name]
        if name.startswith("_") or not isinstance(item, Mapping):
            continue
        evidence_id = _evidence_id("sig", name)
        if evidence_id is None:
            continue
        rows.append({
            "id": evidence_id,
            "field": name,
            "value": _finite(item.get("value")),
            "label": _clip(item.get("label"), 60),
        })
    if not rows:
        raise _BlockMissing(errors.EMPTY)
    breadth = signals.get("_breadth_coverage") if isinstance(signals.get("_breadth_coverage"), Mapping) else {}
    as_of = _iso_z(payload.get("as_of"))
    return _Block(
        {
            "as_of": as_of,
            "saved_at": _iso_z(entry.get("saved_at")),
            "stale": not bool(entry.get("fresh")),
            "sector_etf_breadth": {
                "available": _count(breadth.get("available")),
                "expected": _count(breadth.get("expected")),
                "above_50dma": _count(breadth.get("above_count")),
            },
            "signals": rows,
        },
        {"market_signals": as_of},
    )


_REGIME_FIELDS = (
    "score", "label", "status", "index_trend_score", "market_momentum_score", "market_breadth_score",
    "market_volume_score", "risk_appetite_score", "risk_on_spread_score", "risk_on_spread_label",
    "spy_20d", "qqq_20d", "iwm_20d", "spy_above_sma200", "vix",
)
_REGIME_BREADTH_FIELDS = (
    "sectors_above_50dma", "sectors_above_200dma", "sector_50dma_coverage", "sector_200dma_coverage",
    "rsp_spy_20d", "iwm_spy_20d",
)


def _market_regime(ctx: _Context) -> _Block:
    context = _read_strength_context(ctx.now)
    regime = context.get("market_regime")
    if not isinstance(regime, Mapping):
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    breadth = regime.get("breadth") if isinstance(regime.get("breadth"), Mapping) else {}
    served = _date_text(context.get("served_session"))
    block: dict[str, Any] = {
        "id": "regime:market",
        "served_session": served,
        "as_of": _iso_z(regime.get("as_of") or context.get("as_of")),
        "stale": bool(context.get("_stale")),
        "stale_reason": _clip(context.get("stale_reason"), 60),
    }
    block.update({name: _scalar(regime.get(name)) for name in _REGIME_FIELDS})
    block["breadth"] = {name: _scalar(breadth.get(name)) for name in _REGIME_BREADTH_FIELDS}
    block["warnings"] = _strings(regime.get("warnings"), limit=_WARNINGS)
    return _Block(block, {"market_regime": served})


def _breadth_counts(ctx: _Context) -> _Block:
    batch = _read_eod_batch()
    if not isinstance(batch, Mapping):
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    coverage = batch.get("coverage") if isinstance(batch.get("coverage"), Mapping) else {}
    variants = batch.get("variants") if isinstance(batch.get("variants"), Mapping) else {}
    reference = variants.get(_REFERENCE_VARIANT) if isinstance(variants.get(_REFERENCE_VARIANT), Mapping) else {}
    served = _date_text(batch.get("served_session"))
    return _Block(
        {
            "id": "breadth:eod_batch",
            "served_session": served,
            "generated_at": _iso_z(batch.get("generated_at")),
            "universe_count": _count(coverage.get("eligible_count")),
            "screened_count": _count(coverage.get("complete_bar_count")),
            "scored_count": _count(coverage.get("scored_count")),
            "reference_view": _REFERENCE_VARIANT,
            "eligible_n": _count(reference.get("eligible_n")),
            "watch_n": _count(reference.get("watch_n")),
            "rejected_n": _count(reference.get("rejected_n")),
            "breadth_basis": BREADTH_BASIS,
            "not_available": ["advance_decline_counts", "share_above_moving_averages", "new_highs_new_lows"],
        },
        {"eod_batch": served},
    )


def _themes(ctx: _Context) -> _Block:
    batch = _read_eod_batch()
    if not isinstance(batch, Mapping):
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    statistics_block = batch.get("theme_statistics")
    rows_in = statistics_block.get("sectors") if isinstance(statistics_block, Mapping) else None
    if not isinstance(rows_in, list) or not rows_in:
        raise _BlockMissing(errors.EMPTY)
    spy = {"spy_return_1mo": None, "spy_return_3mo": None, "spy_return_6mo": None}
    rows = []
    for row in rows_in:
        if not isinstance(row, Mapping):
            continue
        sector_id = str(row.get("sector_id") or "")
        evidence_id = _evidence_id("theme", sector_id)
        if evidence_id is None:
            continue
        for key in spy:
            if spy[key] is None:
                spy[key] = _finite(row.get(key))
        leaders = []
        for leader in row.get("leaders") or []:
            ticker = _ticker(leader.get("ticker")) if isinstance(leader, Mapping) else None
            if ticker:
                leaders.append({"ticker": ticker, "score": _finite(leader.get("score"), 1)})
            if len(leaders) >= _THEME_LEADERS:
                break
        rows.append({
            "id": evidence_id,
            "name": _theme_name(sector_id),
            "member_count": _count(row.get("member_count")),
            "scored_count": _count(row.get("scored_count")),
            "avg_strength": _finite(row.get("avg_strength"), 1),
            "avg_return_1mo": _finite(row.get("avg_return_1mo")),
            "avg_return_3mo": _finite(row.get("avg_return_3mo")),
            "avg_return_6mo": _finite(row.get("avg_return_6mo")),
            "excess_vs_spy_1mo": _finite(row.get("excess_vs_spy_1mo")),
            "excess_vs_spy_3mo": _finite(row.get("excess_vs_spy_3mo")),
            "excess_vs_spy_6mo": _finite(row.get("excess_vs_spy_6mo")),
            "score_status": _clip(row.get("score_source_status"), 20),
            "leaders": leaders,
        })
    if not rows:
        raise _BlockMissing(errors.EMPTY)
    # 一个月超额收益从高到低；缺值排最后，保持稳定次序。
    rows.sort(key=lambda item: (item["excess_vs_spy_1mo"] is None, -(item["excess_vs_spy_1mo"] or 0.0)))
    served = _date_text(statistics_block.get("served_session")) or _date_text(batch.get("served_session"))
    return _Block(
        {
            "served_session": served,
            "reference": "balanced/mid/A_trend_quality",
            "unit": "收益率与超额收益为百分数，avg_strength 为 0-100 强度分",
            **spy,
            "rows": rows,
        },
        {},
    )


def _sector_iv(ctx: _Context) -> _Block:
    summaries = []
    latest: str | None = None
    for sector_id in SECTORS:
        payload = _read_sector_iv(sector_id, ctx.epoch)
        if not isinstance(payload, Mapping):
            continue
        rankings = [row for row in payload.get("rankings") or [] if isinstance(row, Mapping)]
        valued = [
            (iv, ticker)
            for row in rankings
            if (iv := _finite(row.get("atm_iv_percent"), 1)) is not None
            and (ticker := _ticker(row.get("ticker"))) is not None
        ]
        evidence_id = _evidence_id("iv", sector_id)
        if not valued or evidence_id is None:
            continue
        top_iv, top_ticker = max(valued)
        as_of = _iso_z(payload.get("as_of"))
        latest = max(filter(None, (latest, as_of)), default=None)
        summaries.append({
            "id": evidence_id,
            "name": _theme_name(sector_id),
            "median_atm_iv_pct": round(statistics.median(iv for iv, _ in valued), 1),
            "sample": len(valued),
            "highest": {"ticker": top_ticker, "atm_iv_pct": top_iv},
            "as_of": as_of,
            "stale": bool(payload.get("_stale")),
        })
    if not summaries:
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    summaries.sort(key=lambda item: (-item["median_atm_iv_pct"], item["id"]))
    if len(summaries) > 2 * _SECTOR_IV_EACH_SIDE:
        highest = summaries[:_SECTOR_IV_EACH_SIDE]
        lowest = summaries[-_SECTOR_IV_EACH_SIDE:][::-1]
    else:
        highest, lowest = summaries, []
    return _Block(
        {
            "sector_count": len(summaries),
            "unit": "平值隐含波动率，百分数；各板块取成分股的中位数",
            "highest": highest,
            "lowest": lowest,
        },
        {"sector_iv": latest},
    )


def _breakouts(ctx: _Context) -> _Block:
    scan = _read_breakout_scan()
    if not isinstance(scan, Mapping):
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    events = [event for event in scan.get("events") or [] if isinstance(event, Mapping)]
    states = Counter(str(event.get("lifecycle_state") or "UNKNOWN") for event in events)
    rows = []
    for event in events:
        evidence_id = _evidence_id("bo", event.get("event_id"))
        ticker = _ticker(event.get("ticker"))
        if evidence_id is None or ticker is None:
            continue
        features = event.get("features") if isinstance(event.get("features"), Mapping) else {}
        scores = event.get("scores") if isinstance(event.get("scores"), Mapping) else {}
        rows.append({
            "id": evidence_id,
            "ticker": ticker,
            "setup_type": _clip(event.get("setup_type"), 40),
            "lifecycle_state": _clip(event.get("lifecycle_state"), 30),
            "session_change_pct": _finite(features.get("session_change_pct")),
            "breakout_quality_score": _finite(scores.get("breakout_quality_score"), 1),
            "alert_priority_score": _finite(scores.get("alert_priority_score"), 1),
        })
        if len(rows) >= _BREAKOUT_ROWS:
            break
    published = _iso_z(scan.get("published_at"))
    setups = {str(event.get("setup_type") or "") for event in events}
    return _Block(
        {
            "published_at": published,
            "session": _clip(scan.get("session"), 20),
            "event_count": len(events),
            "lifecycle_counts": dict(sorted(states.items())),
            "state_names_zh": {key: name for key, name in BREAKOUT_STATE_NAMES_ZH.items() if key in states},
            "setup_names_zh": {key: name for key, name in BREAKOUT_SETUP_NAMES_ZH.items() if key in setups},
            "note": "只有事件当前状态，没有历史突破成功率",
            "rows": rows,
        },
        {"breakouts": published},
    )


def _macro(ctx: _Context) -> _Block:
    block = _read_macro_context()
    if not isinstance(block, Mapping):
        # macro_conditions_context 在关闭、缺密钥或读库失败时都返回 None（读库失败已自行留下诊断）；
        # 没配 FRED 密钥是部署选择，记 disabled，其余记 unavailable。
        configured = getattr(ctx.settings_or_default(), "macro_conditions_configured", True)
        raise _BlockMissing(errors.UNAVAILABLE if configured else errors.DISABLED)
    names = _macro_module_names()
    modules = []
    scores = block.get("module_scores") if isinstance(block.get("module_scores"), Mapping) else {}
    for module_id, score in sorted(scores.items()):
        evidence_id = _evidence_id("macro", module_id)
        if evidence_id is not None:
            modules.append({"id": evidence_id, "name_zh": names.get(module_id), "score": _finite(score, 1)})

    def drivers(key: str) -> list[dict[str, Any]]:
        result = []
        for item in block.get(key) or []:
            if not isinstance(item, Mapping):
                continue
            evidence_id = _evidence_id("macro", item.get("factor_id"))
            if evidence_id is not None:
                result.append({
                    "id": evidence_id,
                    "name_zh": _clip(item.get("display_name_zh"), 40),
                    "score": _finite(item.get("score"), 1),
                    "score_change_7d": _finite(item.get("score_change_7d"), 1),
                })
        return result

    data_through = _date_text(block.get("data_through")) or _iso_z(block.get("data_through"))
    return _Block(
        {
            "id": "macro:composite",
            "status": _clip(block.get("status"), 20),
            "as_of": _iso_z(block.get("as_of")),
            "data_through": data_through,
            "unit": "分位分数 0-100，越高越宽松或越支持风险资产",
            "composite_score": _finite(block.get("composite_score"), 1),
            "score_change_7d": _finite(block.get("score_change_7d"), 1),
            "regime": _clip(block.get("regime"), 40),
            "confidence": _scalar(block.get("confidence")),
            "modules": modules,
            "improving": drivers("top_improving"),
            "deteriorating": drivers("top_deteriorating"),
            "warnings": _strings(block.get("warnings"), limit=_WARNINGS),
        },
        {"macro": data_through},
    )


def _catalyst_status(payload: Mapping[str, Any]) -> None:
    status = payload.get("status")
    if status == "disabled":
        raise _BlockMissing(errors.DISABLED)
    if status == "unavailable":
        raise _BlockMissing(errors.UNAVAILABLE)


def _news(ctx: _Context) -> _Block:
    service = ctx.catalyst_service()
    # 匿名语境读取：只要已发布的热点与新闻原文，不要 owner 才有的队列状态；
    # owner 语境会逐条重建 feed 项并查任务库，生产实测要多花数秒。
    with request_owner_access_context(False):
        hotspots = service.hotspots(limit=_NEWS_LIMIT, now=ctx.now, include_owner_state=False)
    _catalyst_status(hotspots)
    groups = [item for item in hotspots.get("items") or [] if isinstance(item, Mapping)]
    if not groups:
        raise _BlockMissing(errors.EMPTY)
    # 原文片段只在内部 feed 项的 _validation_* 字段里：服务层的公开投影删掉了原文摘要，
    # 而 title/summary 对已分析条目已被替换成中文分析。热点状态正常说明本地库已就绪。
    with request_owner_access_context(False):
        feed = service.intelligence.feed(
            as_of=ctx.now,
            window_hours=_FEED_WINDOW_HOURS[ctx.slot],
            limit=_FEED_LIMIT,
            include_unanalyzed=True,
            include_neutral=True,
        )
    by_news_id: dict[int, Mapping[str, Any]] = {}
    for item in feed.get("items") or []:
        news_id = _count(item.get("news_id")) if isinstance(item, Mapping) else None
        if news_id is not None:
            by_news_id[news_id] = item
    rows = []
    for group in groups:
        evidence_id = _evidence_id("hot", group.get("event_group_id"))
        title = _clip(group.get("representative_title"), _HOT_TITLE_CHARS)
        if evidence_id is None or title is None:
            continue
        news_id = _count(group.get("representative_news_id"))
        source_item = by_news_id.get(news_id) if news_id is not None else None
        rows.append({
            "id": evidence_id,
            "title_zh": title,
            "summary_zh": _clip(group.get("summary_zh"), _HOT_SUMMARY_CHARS),
            "hot_score": _finite(group.get("hot_score"), 1),
            "event_type": _clip(group.get("event_type"), 40),
            "tickers": _tickers(group.get("validated_tickers"), limit=_HOT_TICKERS),
            "source_count": _count(group.get("source_count")),
            "source_names": _strings(group.get("source_names"), limit=_HOT_SOURCE_NAMES, chars=40),
            "first_published_at": _iso_z(group.get("first_published_at")),
            "last_published_at": _iso_z(group.get("last_published_at")),
            "news": _news_source(news_id, source_item),
        })
    if not rows:
        raise _BlockMissing(errors.EMPTY)
    through = _iso_z(hotspots.get("data_through")) or _iso_z(feed.get("data_through"))
    return _Block(
        {
            "as_of": _iso_z(hotspots.get("as_of")),
            "feed_window_hours": _FEED_WINDOW_HOURS[ctx.slot],
            "feed_count": _count((feed.get("summary") or {}).get("count")) if isinstance(feed.get("summary"), Mapping) else None,
            "items": rows,
        },
        {"news": through},
    )


def _news_source(news_id: int | None, item: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if news_id is None or item is None:
        return None
    evidence_id = _evidence_id("news", news_id)
    if evidence_id is None:
        return None
    original_title = _clip(item.get("_validation_title"), _SNIPPET_CHARS)
    original_summary = _clip(item.get("_validation_summary"), _SNIPPET_CHARS)
    original = bool(original_title or original_summary)
    return {
        "id": evidence_id,
        "url": _clip(item.get("url"), 500),
        "published_at": _iso_z(item.get("published_at")),
        "source": _clip(item.get("source"), 60),
        "text_origin": "source" if original else "zh_fallback",
        "title": original_title if original else _clip(item.get("title_zh"), _SNIPPET_CHARS),
        "summary": original_summary if original else _clip(item.get("summary_zh"), _SNIPPET_CHARS),
    }


def _earnings(ctx: _Context) -> _Block:
    # worker 按发布当时的美东日期写 market_date，且收盘后 12 小时才刷新一次；
    # 开盘前读到的常是前一天的条目，所以依次尝试几个日期，再按行自己的财报日过滤。
    local_today = ctx.now.astimezone(ET).date()
    candidates = dict.fromkeys((
        local_today,
        ctx.trading_date,
        local_today - timedelta(days=1),
        previous_trading_day(local_today),
    ))
    entry = None
    for market_date in candidates:
        entry = _read_public_home("earnings", earnings_resource_parameters(market_date), ctx.epoch)
        if entry is not None:
            break
    if entry is None:
        raise _BlockMissing(errors.SNAPSHOT_MISSING)
    start = ctx.trading_date
    end = start
    for _ in range(_EARNINGS_LOOKAHEAD_TRADING_DAYS):
        end = next_trading_day(end)
    rows = []
    for row in entry["payload"].get("earnings") or []:
        if not isinstance(row, Mapping):
            continue
        ticker = _ticker(row.get("ticker"))
        earnings_date = _date_text(row.get("earnings_date"))
        if ticker is None or earnings_date is None or not start.isoformat() <= earnings_date <= end.isoformat():
            continue
        evidence_id = _evidence_id("earn", f"{ticker}:{earnings_date}")
        if evidence_id is None:
            continue
        market_cap = _finite(row.get("market_cap"), 0)
        rows.append({
            "id": evidence_id,
            "ticker": ticker,
            "name": _clip(row.get("name"), 60),
            "earnings_date": earnings_date,
            "timing": _clip(row.get("timing"), 10),
            "eps_estimate": _finite(row.get("eps_estimate"), 3),
            "market_cap_usd_bn": round(market_cap / 1e9, 1) if market_cap is not None else None,
            "release_status": _clip(row.get("release_status"), 30),
        })
    rows.sort(key=lambda item: (item["market_cap_usd_bn"] is None, -(item["market_cap_usd_bn"] or 0.0), item["ticker"]))
    as_of = _iso_z(entry["payload"].get("as_of"))
    return _Block(
        {
            "as_of": as_of,
            "window": [start.isoformat(), end.isoformat()],
            "timing_note": "bmo 为盘前公布，amc 为盘后公布",
            "rows": rows[:_EARNINGS_ROWS],
        },
        {"earnings": as_of},
    )


def _economic_calendar(ctx: _Context) -> _Block:
    service = ctx.catalyst_service()
    offset = ctx.now.astimezone(ET).utcoffset() or timedelta(0)
    date_from = ctx.trading_date - timedelta(days=_ECONOMIC_DAYS_BEFORE)
    date_to = ctx.trading_date + timedelta(days=_ECONOMIC_DAYS_AFTER)
    with request_owner_access_context(False):
        payload = service.calendar(
            date_from=date_from,
            date_to=date_to,
            as_of=ctx.now,
            currencies=("USD",),
            min_impact="medium",
            timezone_offset_minutes=int(offset.total_seconds() // 60),
            include_owner_state=False,
        )
    _catalyst_status(payload)
    rows = []
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        evidence_id = _evidence_id("cal", item.get("event_id"))
        if evidence_id is None:
            continue
        rows.append({
            "id": evidence_id,
            "title": _clip(item.get("title"), 80),
            "impact": _clip(item.get("impact"), 10),
            "scheduled_at": _iso_z(item.get("scheduled_at_utc") or item.get("scheduled_at")),
            "forecast": _scalar(item.get("forecast")),
            "previous": _scalar(item.get("previous")),
            "actual": _scalar(item.get("actual")),
            "release_status": _clip(item.get("release_status"), 20),
        })
    through = _iso_z(payload.get("data_through"))
    return _Block(
        {
            "window": [date_from.isoformat(), date_to.isoformat()],
            "currency": "USD",
            "min_impact": "medium",
            "rows": rows,
        },
        {"calendar": through},
    )


def _prior_brief(ctx: _Context) -> _Block:
    record = ctx.store.latest_record() if ctx.store is not None else None
    result = record.result if record is not None else None
    if record is None or not isinstance(result, Mapping):
        # 首份研判没有上一份，不算缺失。
        return _Block(None, {})
    evidence_id = _evidence_id("prior", record.run_id)
    return _Block(
        {
            "id": evidence_id,
            "run_id": record.run_id,
            "trading_date": record.trading_date.isoformat(),
            "slot": record.slot,
            "generated_at": _iso_z(record.completed_at or record.started_at),
            "headline": result.get("headline"),
            "regime": result.get("regime"),
            "evidence_sufficiency": result.get("evidence_sufficiency"),
            "watch_items": list(result.get("watch_items") or []),
            "invalidators": list(result.get("invalidators") or []),
        },
        {},
    )


# 块顺序即证据包里的顺序；元组第二项是 coverage.missing_blocks 用的块名。
_SOURCES: tuple[tuple[str, Callable[[_Context], _Block]], ...] = (
    ("indices", _indices),
    ("market_signals", _market_signals),
    ("market_regime", _market_regime),
    ("breadth_counts", _breadth_counts),
    ("themes", _themes),
    ("sector_iv", _sector_iv),
    ("breakouts", _breakouts),
    ("macro", _macro),
    ("news", _news),
    ("earnings", _earnings),
    ("economic_calendar", _economic_calendar),
    ("prior_brief", _prior_brief),
)


# ---------------------------------------------------------------------------
# 字节预算与裁剪


def _encoded_size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _record_trim(coverage: dict[str, Any], block: str, before: int, after: int) -> None:
    for item in coverage["trimmed"]:
        if item["block"] == block:
            item["to"] = after
            return
    coverage["trimmed"].append({"block": block, "from": before, "to": after})


def _trim_list(block: str, items: list[Any], coverage: dict[str, Any]) -> bool:
    if not items:
        return False
    before = len(items)
    items.pop()
    _record_trim(coverage, block, before, len(items))
    return True


def _trim_news(payload: dict[str, Any], coverage: dict[str, Any]) -> bool:
    news = payload.get("news")
    return isinstance(news, dict) and _trim_list("news", news["items"], coverage)


def _trim_theme_leaders(payload: dict[str, Any], coverage: dict[str, Any]) -> bool:
    themes = payload.get("themes")
    if not isinstance(themes, dict):
        return False
    depth = max((len(row["leaders"]) for row in themes["rows"]), default=0)
    if depth == 0:
        return False
    for row in themes["rows"]:
        del row["leaders"][depth - 1:]
    _record_trim(coverage, "themes.leaders", depth, depth - 1)
    return True


def _trim_breakouts(payload: dict[str, Any], coverage: dict[str, Any]) -> bool:
    breakouts = payload.get("breakouts")
    return isinstance(breakouts, dict) and _trim_list("breakouts", breakouts["rows"], coverage)


def _trim_calendar(payload: dict[str, Any], coverage: dict[str, Any]) -> bool:
    calendar = payload.get("calendar")
    if not isinstance(calendar, dict):
        return False
    lists = [
        (name, block["rows"])
        for name in ("earnings", "economic")
        if isinstance((block := calendar.get(name)), dict) and block["rows"]
    ]
    if not lists:
        return False
    name, rows = max(lists, key=lambda pair: len(pair[1]))
    return _trim_list(f"calendar.{name}", rows, coverage)


def _trim_sector_iv(payload: dict[str, Any], coverage: dict[str, Any]) -> bool:
    sector_iv = payload.get("sector_iv")
    if not isinstance(sector_iv, dict):
        return False
    highest, lowest = sector_iv["highest"], sector_iv["lowest"]
    rows = highest if len(highest) >= len(lowest) else lowest
    return _trim_list("sector_iv", rows, coverage)


_TRIM_STEPS: tuple[Callable[[dict[str, Any], dict[str, Any]], bool], ...] = (
    _trim_news,
    _trim_theme_leaders,
    _trim_breakouts,
    _trim_calendar,
    _trim_sector_iv,
)
# 逐行裁剪仍超限时整块撤下的顺序（payload 键，块名）；session 与 coverage 不撤。
_DROP_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("news", ("news",)),
    ("themes", ("themes",)),
    ("breakouts", ("breakouts",)),
    ("calendar", ("earnings", "economic_calendar")),
    ("sector_iv", ("sector_iv",)),
    ("prior_brief", ("prior_brief",)),
    ("macro", ("macro",)),
    ("breadth_counts", ("breadth_counts",)),
    ("internals", ("market_signals", "market_regime")),
    ("indices", ("indices",)),
)


def _drop_block(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if key in {"internals", "calendar"}:
        # 这两块是两个来源的组合：各来源置空，结构保留。
        if not isinstance(value, dict) or all(item is None for item in value.values()):
            return False
        payload[key] = {name: None for name in value}
        return True
    if value is None:
        return False
    payload[key] = None
    return True


def _walk(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _collect_codes(payload: Mapping[str, Any]) -> frozenset[str]:
    codes: set[str] = set()
    for key, value in _walk({key: item for key, item in payload.items() if key != "coverage"}):
        if key in {"symbol", "ticker"}:
            ticker = _ticker(value)
            if ticker:
                codes.add(ticker)
        elif key == "tickers" and isinstance(value, list):
            codes.update(ticker for item in value if (ticker := _ticker(item)))
    return frozenset(codes)


def _collect_ids(payload: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        value
        for key, value in _walk(payload)
        if key == "id" and isinstance(value, str) and _ID_PATTERN.fullmatch(value)
    )


def _collect_source_texts(payload: Mapping[str, Any]) -> tuple[str, ...]:
    texts: list[str] = []
    news = payload.get("news")
    for item in news.get("items") if isinstance(news, Mapping) else ():
        texts.extend(item.get("source_names") or ())
        source = item.get("news")
        if isinstance(source, Mapping):
            texts.extend(text for key in ("title", "summary", "source") if (text := source.get(key)))
    calendar = payload.get("calendar")
    earnings = calendar.get("earnings") if isinstance(calendar, Mapping) else None
    for row in earnings.get("rows") if isinstance(earnings, Mapping) else ():
        if row.get("name"):
            texts.append(row["name"])
    return tuple(dict.fromkeys(text for text in texts if isinstance(text, str) and text.strip()))


def _fit_budget(payload: dict[str, Any], max_bytes: int) -> int:
    coverage = payload["coverage"]

    def measure() -> int:
        coverage["allowed_codes_count"] = len(_collect_codes(payload) | BENCHMARK_CODES)
        # 先用预算本身占位：最终字节数不会比它多位数，回填后总量只会持平或变小。
        coverage["evidence_bytes"] = max_bytes
        return _encoded_size(payload)

    size = measure()
    for step in _TRIM_STEPS:
        while size > max_bytes and step(payload, coverage):
            size = measure()
    for key, blocks in _DROP_ORDER:
        if size <= max_bytes:
            break
        if not _drop_block(payload, key):
            continue
        for block in blocks:
            if not any(item["block"] == block for item in coverage["missing_blocks"]):
                coverage["missing_blocks"].append({"block": block, "reason": errors.OVER_BUDGET})
        size = measure()
    if size > max_bytes:
        raise ValueError(f"market brief evidence cannot fit in {max_bytes} bytes")
    for _ in range(3):
        coverage["evidence_bytes"] = size
        settled = _encoded_size(payload)
        if settled == size:
            break
        size = settled
    return size


# ---------------------------------------------------------------------------


def build_evidence(
    *,
    slot: BriefSlot,
    trading_date: date,
    now: datetime,
    store: BriefStore | None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    settings: Any = None,
) -> EvidencePack:
    """组装一份证据包。各来源互不影响；只有预算本身放不下最小证据时才抛 ValueError。

    settings 是 ``app.config.Settings``（缺省读进程配置），用来区分「未配置」与「暂不可用」。
    """

    if slot not in _FEED_WINDOW_HOURS:
        raise ValueError(f"unknown market brief slot: {slot!r}")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    ctx = _Context(slot=slot, trading_date=trading_date, now=_aware(now), store=store, settings=settings)
    missing: list[dict[str, str]] = []
    block_errors: dict[str, str] = {}
    data_through: dict[str, Any] = {}
    results: dict[str, Any] = {}
    for block, builder in _SOURCES:
        try:
            built = builder(ctx)
        except _BlockMissing as exc:
            missing.append({"block": block, "reason": exc.reason})
            results[block] = None
            continue
        except Exception as exc:
            # 证据块边界：单个来源的任何故障只让这一块缺席，原因与异常类型留在 coverage。
            record_fallback_failure(f"market_brief_evidence_{block}", exc)
            missing.append({"block": block, "reason": errors.READ_FAILED})
            block_errors[block] = type(exc).__name__
            results[block] = None
            continue
        results[block] = built.payload
        data_through.update(built.through)

    indices = results["indices"]
    breadth = results["breadth_counts"]
    coverage: dict[str, Any] = {
        "universe_size": breadth["universe_count"] if breadth else None,
        "scored_count": breadth["scored_count"] if breadth else None,
        "quotes_valid": sum(1 for row in indices["rows"] if row["price"] is not None) if indices else 0,
        "breadth_basis": BREADTH_BASIS,
        "data_through": data_through,
        "missing_blocks": missing,
        "trimmed": [],
        "generated_at": _iso_z(ctx.now),
        "allowed_codes_count": 0,
        "evidence_bytes": 0,
    }
    payload: dict[str, Any] = {
        "version": EVIDENCE_VERSION,
        "session": _session(ctx).payload,
        "coverage": coverage,
        "indices": indices,
        "internals": {"market_signals": results["market_signals"], "market_regime": results["market_regime"]},
        "breadth_counts": breadth,
        "themes": results["themes"],
        "sector_iv": results["sector_iv"],
        "breakouts": results["breakouts"],
        "macro": results["macro"],
        "news": results["news"],
        "calendar": {"earnings": results["earnings"], "economic": results["economic_calendar"]},
        "prior_brief": results["prior_brief"],
    }
    size = _fit_budget(payload, max_bytes)
    allowed_codes = _collect_codes(payload) | BENCHMARK_CODES
    pack_coverage = dict(coverage)
    if block_errors:
        # 异常类型只给运行记录与 owner 排查用，不放进模型看到的证据包。
        pack_coverage["block_errors"] = block_errors
    return EvidencePack(
        payload=payload,
        bytes=size,
        coverage=pack_coverage,
        allowed_codes=allowed_codes,
        evidence_ids=_collect_ids(payload),
        source_texts=_collect_source_texts(payload),
        data_through=dict(data_through),
    )


def eod_batch_served_session(root: Path | None = None) -> date | None:
    """当前已发布的全市场 EOD 批次对应哪个交易日；没有可用批次时返回 None。

    worker 用它判断收盘后槽能不能发车（当日批次美东 22:00 左右才发布）。
    """

    batch = _read_eod_batch(root)
    if not isinstance(batch, Mapping):
        return None
    served = _date_text(batch.get("served_session"))
    return date.fromisoformat(served) if served else None


__all__ = [
    "BENCHMARK_CODES",
    "BREAKOUT_SETUP_NAMES_ZH",
    "BREAKOUT_STATE_NAMES_ZH",
    "BREADTH_BASIS",
    "DEFAULT_MAX_BYTES",
    "EVIDENCE_VERSION",
    "EvidencePack",
    "INDEX_NAMES_ZH",
    "build_evidence",
    "eod_batch_served_session",
]
