"""Frozen experiment protocol for screener / radar research.

The sealed window is not a suggestion. Metric helpers refuse sealed dates
unless an explicit unblind token matching the frozen hash is supplied after
original, repaired, and candidate configurations are locked.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Literal, Mapping
from zoneinfo import ZoneInfo

from app.services.market_calendar import is_trading_day
from app.services.strength.scoring import (
    FEATURE_VERSION,
    NORMALIZATION_VERSION,
    SCORE_VERSION,
)


RESEARCH_PROTOCOL_VERSION = "screener-radar-research-protocol-v1"
NEW_YORK = ZoneInfo("America/New_York")
SplitName = Literal["warmup", "development", "validation", "sealed"]

EVIDENCE_GRADES = {
    "A": "真实发布记录回放；仅当存在当时 completed + published_at 快照时成立。",
    "B": "点时历史重建：同一生产逻辑读取当时可见的历史输入，反事实而非当年实盘。",
    "C": "有缺口的降级研究：当前主题股票池、仅日线、缺 Discovery 或特征。",
    "D": "合成数据 / Mock：只验证程序，不得宣称收益或预测能力。",
}

# Calendar splits are frozen before any return is computed. The sealed end is
# "last completed NYSE session at freeze time" and is filled by the data
# audit, not by peeking at returns.
FROZEN_SPLITS = {
    "warmup": {"start": date(2018, 1, 2), "end": date(2018, 12, 31)},
    "development": {"start": date(2019, 1, 2), "end": date(2022, 12, 30)},
    "validation": {"start": date(2023, 1, 3), "end": date(2024, 6, 28)},
    "sealed": {"start": date(2024, 7, 1), "end": date(2026, 9, 11)},
}

PRIMARY_HORIZONS = (1, 5, 20, 63)
SUPPLEMENTAL_HORIZONS = (2, 10)
ALL_LABEL_HORIZONS = PRIMARY_HORIZONS + SUPPLEMENTAL_HORIZONS
PRIMARY_HORIZON = 20
PRIMARY_TOP_K = (5, 10, 20)

SCREENER_MODES = (
    {"timeframe": "all", "profile": "balanced"},
    {"timeframe": "short", "profile": "balanced"},
    {"timeframe": "mid", "profile": "balanced"},
    {"timeframe": "long", "profile": "balanced"},
    {"timeframe": "all", "profile": "conservative"},
    {"timeframe": "all", "profile": "aggressive"},
)

DEFAULT_SCAN_PARAMETERS = {
    "universe": "themes",
    "timeframe": "all",
    "profile": "balanced",
    "top": 20,
    "sector_id": None,
    "min_price": 5.0,
    "min_avg_dollar_volume": 10_000_000.0,
    "include_options": False,
    "enrich_live": False,
}

PORTFOLIO_PROTOCOL = {
    "initial_cash": 100_000.0,
    "account": "cash",
    "margin": False,
    "shorting": False,
    "max_positions": 10,
    "max_weight": 0.10,
    "rebalance": "signal_close_next_open",
    "hold_trading_days": 20,
    "cost_scenarios_bps": (5, 10, 25),
    "same_bar_stop_ambiguity": "conservative_stop_first",
    "fill": "next_session_open",
    "missing_fill": "unavailable",
}

PRE_REGISTERED_TRIALS = (
    {
        "trial_id": "original-screener-balanced-all",
        "layer": "original",
        "family": "screener",
        "description": "生产默认 balanced/all 排序，当前主题池点时重建。",
    },
    {
        "trial_id": "original-screener-all-profiles",
        "layer": "original",
        "family": "screener",
        "description": "六个预登记选股模式分别报告，不合并胜率。",
    },
    {
        "trial_id": "original-radar-daily-base-theme-universe",
        "layer": "original",
        "family": "radar",
        "description": "生产 detect_base/detect_breakout 的日线重建；结构用 T-1。",
    },
    {
        "trial_id": "original-combo-screener-then-radar",
        "layer": "original",
        "family": "combo",
        "description": "只用雷达触发前已发布的选股快照做交集。",
    },
    {
        "trial_id": "baseline-momentum-63d",
        "layer": "baseline",
        "family": "screener",
        "description": "同日同池按 63 日收益排序的简单动量对照。",
    },
    {
        "trial_id": "candidate-unadjusted-min-price",
        "layer": "candidate",
        "family": "screener",
        "description": "用未复权收盘做 min_price，检验复权绝对价格过滤偏差。",
    },
    {
        "trial_id": "candidate-disable-market-fit",
        "layer": "candidate",
        "family": "screener",
        "description": "消融 market_fit，检验环境层是否有增量。",
    },
    {
        "trial_id": "candidate-radar-triggered-only",
        "layer": "candidate",
        "family": "radar",
        "description": "只评估 TRIGGERED，不用后来的 CONFIRMED 回溯入场。",
    },
    {
        "trial_id": "candidate-radar-exclude-chase-extended",
        "layer": "candidate",
        "family": "radar",
        "description": "去掉 extended/chase 事件后的全流程覆盖，不只看留下的好样本。",
    },
)

STOP_RULES = {
    "max_candidate_trials": 4,
    "no_profit_search": True,
    "primary_screener_metric": "daily_rank_ic_20d_excess_vs_universe",
    "primary_radar_metric": "triggered_20d_excess_vs_universe",
    "minimum_economic_edge": {
        "rank_ic": 0.02,
        "horizon_20d_excess": 0.002,
        "cost_scenario_bps": 10,
        "ci_must_exclude_zero": True,
    },
    "if_unmet": "report_no_advantage_and_stop",
}

FROZEN_PROTOCOL: dict[str, Any] = {
    "protocol_version": RESEARCH_PROTOCOL_VERSION,
    "base_commit": "31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc",
    "score_version": SCORE_VERSION,
    "feature_version": FEATURE_VERSION,
    "normalization_version": NORMALIZATION_VERSION,
    "range_persistence_mode": "shadow",
    "universe": "themes-current-membership-grade-c",
    "splits": {
        name: {
            "start": window["start"].isoformat(),
            "end": window["end"].isoformat(),
        }
        for name, window in FROZEN_SPLITS.items()
    },
    "horizons": {
        "primary": list(PRIMARY_HORIZONS),
        "supplemental": list(SUPPLEMENTAL_HORIZONS),
        "primary_horizon": PRIMARY_HORIZON,
    },
    "screener_modes": list(SCREENER_MODES),
    "default_scan_parameters": DEFAULT_SCAN_PARAMETERS,
    "portfolio": PORTFOLIO_PROTOCOL,
    "trials": list(PRE_REGISTERED_TRIALS),
    "stop_rules": STOP_RULES,
    "evidence_policy": EVIDENCE_GRADES,
    "unverifiable_without_intraday": [
        "OPENING_RANGE_BREAKOUT",
        "PREMARKET_GAP",
        "GAP_HOLD",
        "GAP_AND_GO",
        "GAP_FADE",
        "intraday_rvol_time_of_day",
        "same_bar_stop_takeprofit_path",
        "first_stop_vs_first_target",
    ],
}


def protocol_hash(payload: Mapping[str, Any] | None = None) -> str:
    source = dict(payload or FROZEN_PROTOCOL)
    source.pop("protocol_hash", None)
    encoded = json.dumps(
        source,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


FROZEN_PROTOCOL["protocol_hash"] = protocol_hash(
    {key: value for key, value in FROZEN_PROTOCOL.items() if key != "protocol_hash"}
)


def parse_session_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.astimezone(NEW_YORK).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(NEW_YORK).date()
    return date.fromisoformat(text)


def split_for_date(value: date | datetime | str) -> SplitName | None:
    session = parse_session_date(value)
    for name, window in FROZEN_SPLITS.items():
        if window["start"] <= session <= window["end"]:
            return name  # type: ignore[return-value]
    return None


def assert_split_access(
    value: date | datetime | str,
    *,
    allow_sealed: bool = False,
    purpose: str = "metric",
) -> SplitName | None:
    """Refuse sealed-window peeking unless the caller explicitly unblinds."""

    name = split_for_date(value)
    if name == "sealed" and not allow_sealed:
        raise PermissionError(
            f"sealed split is frozen and cannot be used for {purpose}; "
            "unblind only after original/repair/candidate configs are locked"
        )
    return name


def iter_split_dates(
    split: SplitName,
    *,
    allow_sealed: bool = False,
    trading_days_only: bool = True,
) -> list[date]:
    if split == "sealed" and not allow_sealed:
        raise PermissionError("sealed split dates are hidden until unblind")
    window = FROZEN_SPLITS[split]
    cursor = window["start"]
    dates: list[date] = []
    while cursor <= window["end"]:
        if not trading_days_only or is_trading_day(cursor):
            dates.append(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return dates
