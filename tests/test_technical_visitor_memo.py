"""Visitor technical structure is computed once per exact input set."""

from __future__ import annotations

import asyncio
import math
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.access import request_owner_access_context
from app.api import stocks
from app.public_stock_data import public_stock_snapshot_path
from app.services.market_calendar import is_trading_day
from app.stock_pull_snapshot import write_stock_pull_resources

NY = ZoneInfo("America/New_York")
LAST_SESSION = date(2026, 7, 17)


def _chart(symbol: str, closes: list[float], *, last_day: date = LAST_SESSION) -> dict:
    days: list[date] = []
    day = last_day
    while len(days) < len(closes):
        if is_trading_day(day):
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    bars = [
        {
            "t": int(datetime(d.year, d.month, d.day, tzinfo=NY).timestamp()),
            "o": close * 0.995,
            "h": close * 1.01,
            "l": close * 0.985,
            "c": close,
            "v": 1_000_000 + index,
            "ext": False,
            "quote_only": False,
            "session": "regular",
        }
        for index, (d, close) in enumerate(zip(days, closes))
    ]
    return {
        "ticker": symbol,
        "range": "1d",
        "price_adjustment": "raw",
        "price_provider": "fixture",
        "as_of": "2026-07-17T21:00:00+00:00",
        "bars": bars,
    }


def _closes(count: int = 140, *, drift: float = 0.2) -> list[float]:
    return [100 + 10 * math.sin(index / 9) + index * drift for index in range(count)]


def _publish(symbol: str, chart: dict, saved_at: float) -> None:
    write_stock_pull_resources(
        symbol,
        {"daily_chart": (chart, saved_at)},
        path=public_stock_snapshot_path(symbol),
    )


@pytest.fixture
def computations(monkeypatch) -> list[str]:
    from app.services.technical import structure

    calls: list[str] = []
    real = structure.compute_technical_structure

    def counting(bars, **kwargs):
        calls.append(kwargs.get("ticker"))
        return real(bars, **kwargs)

    monkeypatch.setattr(structure, "compute_technical_structure", counting)
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    return calls


def _visit(symbol: str) -> dict:
    with request_owner_access_context(False):
        return asyncio.run(stocks.stock_technical(symbol))


def test_visitor_requests_compute_once_until_the_snapshot_changes(computations):
    now = time.time()
    _publish("AAOI", _chart("AAOI", _closes()), now - 120)
    _publish("SPY", _chart("SPY", _closes(drift=0.05)), now - 120)

    responses = [_visit("AAOI") for _ in range(20)]

    assert computations == ["AAOI"]
    assert all(response == responses[0] for response in responses)
    assert responses[0]["chart_analysis"]["ticker"] == "AAOI"

    _publish("AAOI", _chart("AAOI", _closes(drift=0.3)), now - 60)
    changed = _visit("AAOI")

    assert computations == ["AAOI", "AAOI"]
    assert changed != responses[0]


def test_every_input_is_part_of_the_key(computations):
    bars = _chart("AAOI", _closes())["bars"]
    spy = {f"2026-07-{day:02d}": 500.0 + day for day in range(1, 18)}

    first = stocks._visitor_technical_result("AAOI", bars, spy)
    assert stocks._visitor_technical_result("AAOI", bars, spy) is first
    assert len(computations) == 1

    stocks._visitor_technical_result("AAOI", bars, {**spy, "2026-07-17": 1.0})
    stocks._visitor_technical_result("AAOI", bars, None)
    stocks._visitor_technical_result("AAOI", _chart("AAOI", _closes(drift=0.3))["bars"], spy)
    stocks._visitor_technical_result("NBIS", bars, spy)
    assert len(computations) == 5


def test_the_session_close_starts_a_new_result(computations, monkeypatch):
    bars = _chart("AAOI", _closes())["bars"]

    class _Clock(datetime):
        current = datetime(2026, 7, 17, 14, 0, tzinfo=NY)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz is not None else cls.current

    monkeypatch.setattr(stocks, "datetime", _Clock)

    open_session = stocks._visitor_technical_result("AAOI", bars, None)
    _Clock.current = datetime(2026, 7, 17, 17, 0, tzinfo=NY)
    closed = stocks._visitor_technical_result("AAOI", bars, None)
    _Clock.current = datetime(2026, 7, 17, 19, 0, tzinfo=NY)
    stocks._visitor_technical_result("AAOI", bars, None)

    assert len(computations) == 2
    assert open_session["last_bar"]["closed"] is False
    assert closed["last_bar"]["closed"] is True


def test_missing_results_are_not_remembered(monkeypatch):
    from app.services.technical import structure

    calls: list[int] = []

    def no_structure(bars, **kwargs):
        calls.append(len(bars))
        return None

    monkeypatch.setattr(structure, "compute_technical_structure", no_structure)
    bars = _chart("AAOI", _closes())["bars"]

    assert stocks._visitor_technical_result("AAOI", bars, None) is None
    assert stocks._visitor_technical_result("AAOI", bars, None) is None
    assert len(calls) == 2
    assert not stocks._technical_visitor_results


def test_decorating_a_response_does_not_touch_the_remembered_result(computations):
    now = time.time()
    _publish("AAOI", _chart("AAOI", _closes()), now - 120)

    first = _visit("AAOI")
    first["chart_analysis"]["ticker"] = "MUTATED"
    first["basis"] = "MUTATED"
    second = _visit("AAOI")

    assert computations == ["AAOI"]
    assert second["chart_analysis"]["ticker"] == "AAOI"
    assert second["basis"] == "raw_daily"
