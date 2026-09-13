"""Replay production screener scoring on an offline panel."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

import pandas as pd

from app.services.research.calendar import session_close
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import (
    DEFAULT_SCAN_PARAMETERS,
    assert_split_access,
    parse_session_date,
)
from app.services.strength.scanner import _scan_sync, _theme_universe


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
