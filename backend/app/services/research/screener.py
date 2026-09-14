"""Replay production screener scoring on an offline panel."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

import pandas as pd

from app.services.research.calendar import session_close
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import (
    DEFAULT_SCAN_PARAMETERS,
    SCREENER_MODES,
    assert_split_access,
    parse_session_date,
)
from app.services.strength.features import _safe_float
from app.services.strength.scanner import _scan_sync, _sort_scored, _theme_universe
from app.services.strength.scoring import score_profile_fit, score_ranking


def replay_screener_day(
    dataset: OfflineOHLCV,
    signal_date: date | datetime | str,
    *,
    parameters: Mapping[str, Any] | None = None,
    allow_sealed: bool = False,
    include_future_bars: bool = False,
    panel: Any | None = None,
) -> dict[str, Any]:
    """Run the production scan loop at the regular close of ``signal_date``.

    Evidence grade is C for universe membership (today's theme pool) and B
    for the scoring math itself. This is not a published snapshot replay.
    """

    session = parse_session_date(signal_date)
    assert_split_access(session, allow_sealed=allow_sealed, purpose="screener_replay")
    as_of = session_close(session)
    params = {**DEFAULT_SCAN_PARAMETERS, **dict(parameters or {})}
    history = panel
    if history is None:
        tickers, _meta = _theme_universe()
        symbols = list(dict.fromkeys([*tickers, *dataset.tickers()]))
        through = None if include_future_bars else session
        history = dataset.adjusted_panel(
            symbols,
            through=through,
            allow_sealed=allow_sealed,
        )
    elif not isinstance(history, pd.DataFrame):
        raise TypeError("panel must be a DataFrame")
    payload = _scan_sync(
        universe=str(params["universe"]),
        timeframe=str(params["timeframe"]),
        profile=str(params["profile"]),
        top=int(params["top"]),
        sector_id=params.get("sector_id"),
        min_price=float(params["min_price"]),
        min_avg_dollar_volume=float(params["min_avg_dollar_volume"]),
        include_options=False,
        raw_history=history,
        as_of=as_of,
        enrich_live=False,
    )
    payload["research"] = {
        "evidence_grade": "C",
        "evidence_notes": [
            "评分与排序调用生产 _scan_sync / _feature_row / score_*。",
            "股票池是当前 themes 成员，不是当时可投资全集。",
            "价格为离线导入的复权日线，trust=external_unverified。",
            "未使用期权、Finnhub 基本面或当前宏观影子。",
        ],
        "signal_date": session.isoformat(),
        "as_of": as_of.isoformat(),
        "available_at": as_of.isoformat(),
        "executable_from": "next_session_open",
        "include_future_bars": include_future_bars,
    }
    return payload


def momentum_baseline_ranks(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Same-day same-pool 63-day return rank. Not a production score."""

    scored = []
    for row in rows:
        item = dict(row)
        item["baseline_score"] = row.get("return_63d")
        scored.append(item)
    scored.sort(
        key=lambda item: (
            item.get("baseline_score") is not None,
            item.get("baseline_score") if item.get("baseline_score") is not None else -999,
            str(item.get("ticker") or ""),
        ),
        reverse=True,
    )
    for rank, item in enumerate(scored, start=1):
        item["baseline_rank"] = rank
    return scored


def _coverage_ratio(row: Mapping[str, Any], name: str) -> float | None:
    block = (row.get("coverage") or {}).get(name) if isinstance(row.get("coverage"), Mapping) else None
    if isinstance(block, Mapping) and block.get("ratio") is not None:
        try:
            return float(block["ratio"])
        except (TypeError, ValueError):
            return None
    return None


def compact_screener_row(
    row: Mapping[str, Any],
    *,
    signal_date: date | datetime | str,
    dataset: OfflineOHLCV | None = None,
) -> dict[str, Any]:
    """Keep score keys needed for mode reranks. Drop bulky production breakdowns."""

    session = parse_session_date(signal_date)
    ticker = str(row.get("ticker") or "")
    bar = dataset.bar(ticker, session) if dataset is not None and ticker else None
    return {
        "signal_date": session.isoformat(),
        "ticker": ticker,
        "selected_view_rank": row.get("selected_view_rank"),
        "ranking_score": row.get("ranking_score"),
        "intrinsic_score": row.get("intrinsic_score"),
        "score_short": row.get("score_short"),
        "score_mid": row.get("score_mid"),
        "score_long": row.get("score_long"),
        "market_fit_score": row.get("market_fit_score"),
        "profile_fit_score": row.get("profile_fit_score"),
        "intrinsic_confidence": row.get("intrinsic_confidence"),
        "market_fit_confidence": _coverage_ratio(row, "market_fit"),
        "profile_fit_confidence": _coverage_ratio(row, "profile_fit"),
        "return_63d": row.get("return_63d"),
        "atr_pct": row.get("atr_pct"),
        "ma_alignment": row.get("ma_alignment"),
        "avg_dollar_volume_20d": row.get("avg_dollar_volume_20d"),
        "price": row.get("price"),
        "unadjusted_close": None if bar is None else bar.get("close"),
        "adj_close": None if bar is None else bar.get("adj_close"),
        "daily_data_through": row.get("daily_data_through"),
        "score_status": row.get("score_status"),
        "labels": row.get("labels"),
        "excess": row.get("excess"),
    }


def production_sort_score(row: Mapping[str, Any], timeframe: str) -> float | None:
    """The exact key used by production `_sort_scored`."""

    ranking = _safe_float(row.get("ranking_score"), 4)
    if timeframe in {"short", "mid", "long"}:
        term = _safe_float(row.get(f"score_{timeframe}"), 4)
        if term is None and ranking is None:
            return None
        return (term or 0.0) * 0.94 + (ranking or 0.0) * 0.06
    return ranking


def apply_screener_mode(
    rows: list[Mapping[str, Any]],
    *,
    timeframe: str,
    profile: str,
) -> list[dict[str, Any]]:
    """Re-apply production sort/profile math to a compact dump. No new features."""

    copies: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if profile != "balanced":
            profile_fit = score_profile_fit(item, profile)
            ranking = score_ranking(
                {
                    "score": item.get("intrinsic_score"),
                    "confidence": item.get("intrinsic_confidence") or 0.0,
                },
                {
                    "score": item.get("market_fit_score"),
                    "confidence": item.get("market_fit_confidence") or 0.0,
                },
                profile_fit,
            )
            item["ranking_score"] = ranking.get("score")
            item["profile_fit_score"] = profile_fit.get("score")
        item["mode_sort_score"] = production_sort_score(item, timeframe)
        item["mode_timeframe"] = timeframe
        item["mode_profile"] = profile
        copies.append(item)
    _sort_scored(copies, timeframe)
    for rank, item in enumerate(copies, start=1):
        item["selected_view_rank"] = rank
    return copies


def apply_disable_market_fit(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Production score_ranking with market_fit forced unavailable."""

    copies: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        ranking = score_ranking(
            {
                "score": item.get("intrinsic_score"),
                "confidence": item.get("intrinsic_confidence") or 0.0,
            },
            {"score": None, "status": "insufficient_data", "confidence": 0.0},
            {
                "score": item.get("profile_fit_score"),
                "confidence": item.get("profile_fit_confidence") or 0.0,
            },
        )
        item["ranking_score"] = ranking.get("score")
        item["mode_sort_score"] = ranking.get("score")
        item["mode_timeframe"] = "all"
        item["mode_profile"] = "balanced"
        item["ablation"] = "disable_market_fit"
        copies.append(item)
    _sort_scored(copies, "all")
    for rank, item in enumerate(copies, start=1):
        item["selected_view_rank"] = rank
    return copies


def apply_price_filter(
    rows: list[Mapping[str, Any]],
    *,
    min_price: float,
    price_key: str = "price",
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        value = row.get(price_key)
        if value is None:
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price < min_price:
            continue
        kept.append(dict(row))
    return kept


def iter_screener_modes() -> tuple[dict[str, str], ...]:
    return SCREENER_MODES
