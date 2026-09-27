"""Deterministic all-market style panels shared by the v1.6 identity test and the v1.7 switch tests.

The panel is synthetic (``structured_close`` trends with periodic pullbacks), so
everything here is reproducible without market data. Stocks carry a synthetic
SIC-style classification with groups large enough (>= 5 peers) for the G factor.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from app.services.eod_limited.market_registry import ALL_MARKET_STOCKS
from app.services.eod_limited.worker import build_synthetic_panel
from app.services.research_eod_v1.fixtures import make_series, structured_close

SESSION = date(2026, 9, 18)
STOCK_COUNT = 60
# Four synthetic four-digit SIC codes; three-digit groups are their prefixes.
SIC_CODES = ("2834", "3674", "7372", "6022", "2836")


def stock_ids(count: int = STOCK_COUNT) -> list[str]:
    return [f"NEW{index:03}" for index in range(count)]


def build_panel(session: date = SESSION, count: int = STOCK_COUNT) -> dict[str, Any]:
    """SPY, QQQ, four more funds and ``count`` synthetic stocks with varied trends and extensions."""
    seed = build_synthetic_panel(sessions=370, end=session)
    panel = {sid: seed[sid] for sid in ("SPY", "QQQ")}
    days = seed["SPY"].dates
    for name, start, drift, cycle in (("XLE", 80.0, 0.06, 18), ("TLT", 95.0, -0.02, 30), ("IWM", 180.0, 0.09, 24),
                                      ("GLD", 150.0, 0.12, 40)):
        panel[name] = make_series(
            name, days, structured_close(370, start, drift, cycle), theme_ids=("etfs",),
            asset_track="etf", security_type="ETF", industry_id=None, parent_industry_id=None,
        ).with_close_price_return()
    for index, sid in enumerate(stock_ids(count)):
        themes = (ALL_MARKET_STOCKS,) + (("semiconductors",) if index % 12 == 0 else ())
        panel[sid] = make_series(
            sid, days, structured_close(370, 15 + index, 0.04 + 0.012 * (index % 8), 16 + (index * 5) % 13),
            theme_ids=themes, volume=3_000_000 + 40_000 * index,
        )
    return panel


def sic_table(count: int = STOCK_COUNT) -> list[dict[str, Any]]:
    """Frozen-table style records: every fifth stock is left unclassified (an ADR, say)."""
    rows = []
    for index, sid in enumerate(stock_ids(count)):
        code = None if index % 5 == 4 else SIC_CODES[index % len(SIC_CODES)]
        rows.append({"ticker": sid, "cik": f"{index:010d}", "as_of": "2026-09-25", "sic_code": code,
                     "sic_description": None if code is None else f"GROUP {code}"})
    rows.append({"ticker": "SPY", "cik": None, "as_of": "2026-09-25", "sic_code": None, "sic_description": None})
    return rows


def directory_rows(count: int = STOCK_COUNT) -> list[dict[str, Any]]:
    """A Massive-style directory for the same securities, including CIKs."""
    rows = [
        {"ticker": name, "name": name, "market": "stocks", "locale": "us", "type": "ETF",
         "primary_exchange": "ARCX", "active": True, "cik": None}
        for name in ("SPY", "QQQ", "XLE", "TLT")
    ]
    for index, sid in enumerate(stock_ids(count)):
        rows.append({"ticker": sid, "name": f"New {index}", "market": "stocks", "locale": "us",
                     "type": "ADRC" if index % 5 == 4 else "CS", "primary_exchange": "XNAS", "active": True,
                     "cik": f"{index:010d}"})
    return rows


def rounded(value: Any, digits: int = 6) -> Any:
    """Round floats recursively so the fixture survives BLAS/LAPACK differences."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {str(key): rounded(item, digits) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(item, digits) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(rounded(item, digits) for item in value)
    return value


def stable_json(value: Any) -> str:
    return json.dumps(rounded(value), sort_keys=True, default=str, separators=(",", ":"))
