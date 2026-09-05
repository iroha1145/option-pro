"""Completed-minute analysis must share the exact bars returned by the chart."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.api import stocks
from app.models.schemas import BarsResponse
from app.services import massive
from app.services.technical.chart_analysis import (
    assemble_intraday_analysis,
    bar_fingerprint,
    mark_intraday_closed,
    series_from_chart_bars,
)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _bar(stamp: datetime, close: float = 100, **extra) -> dict:
    return {
        "t": int(stamp.timestamp()), "o": close, "h": close + 1,
        "l": close - 1, "c": close, "v": 1000,
        "ext": False, "quote_only": False, **extra,
    }


@pytest.mark.parametrize("chart_range, seconds", [("5m", 300), ("15m", 900), ("1h", 3600)])
def test_completion_uses_bar_end_and_keeps_all_chart_rows(chart_range, seconds):
    start = _time("2026-07-06T14:30:00Z")
    step = timedelta(seconds=seconds)
    bars = [_bar(start - step), _bar(start), _bar(start + step)]
    original = copy.deepcopy(bars)
    marked = mark_intraday_closed(bars, chart_range, now=start + step - timedelta(seconds=1))
    assert [bar["closed"] for bar in marked] == [True, False, False]
    assert len(marked) == len(bars)
    assert bars == original
    assert mark_intraday_closed(bars, chart_range, now=start + step)[1]["closed"] is True


@pytest.mark.parametrize("start, close", [
    ("2026-07-06T19:30:00Z", "2026-07-06T20:00:00Z"),
    ("2026-01-06T20:30:00Z", "2026-01-06T21:00:00Z"),
    ("2025-11-28T17:30:00Z", "2025-11-28T18:00:00Z"),
])
def test_last_hour_bar_closes_at_regular_or_early_session_end(start, close):
    bars = [_bar(_time(start))]
    close_time = _time(close)
    assert mark_intraday_closed(bars, "1h", now=close_time - timedelta(seconds=1))[0]["closed"] is False
    assert mark_intraday_closed(bars, "1h", now=close_time)[0]["closed"] is True


def test_explicit_partial_snapshot_is_never_upgraded_without_a_refetch():
    start = _time("2026-07-06T13:35:00Z")
    original = [_bar(start)]
    cached = mark_intraday_closed(original, "5m", now=start + timedelta(minutes=2))
    assert cached[0]["closed"] is False
    later = start + timedelta(days=1)
    assert mark_intraday_closed(cached, "5m", now=later)[0]["closed"] is False
    assert assemble_intraday_analysis(cached, chart_range="5m", now=later) is None
    # A fresh, unmarked provider response can establish completion.
    assert mark_intraday_closed(original, "5m", now=later)[0]["closed"] is True


def test_future_bar_cannot_claim_completion_with_a_true_flag():
    start = _time("2026-07-06T14:30:00Z")
    marked = mark_intraday_closed([_bar(start, closed=True)], "5m", now=start)
    assert marked[0]["closed"] is False


@pytest.mark.parametrize("stamp", [None, True, float("nan"), float("inf"), -1, "garbage", "2026-07-06T13:30:00"])
def test_invalid_or_ambiguous_start_is_not_marked_complete(stamp):
    bars = [{**_bar(_time("2026-07-06T13:30:00Z")), "t": stamp}]
    marked = mark_intraday_closed(bars, "5m", now=_time("2026-07-06T14:00:00Z"))
    assert marked[0]["closed"] is False
    assert assemble_intraday_analysis(marked, chart_range="5m") is None


@pytest.mark.parametrize("stamp", [1783344600, 1783344600000, "2026-07-06T13:30:00Z", "2026-07-06T09:30:00-04:00"])
def test_seconds_milliseconds_and_zoned_iso_times_share_a_completion_boundary(stamp):
    bars = [{**_bar(_time("2026-07-06T13:30:00Z")), "t": stamp}]
    assert mark_intraday_closed(bars, "5m", now=_time("2026-07-06T13:34:59Z"))[0]["closed"] is False
    assert mark_intraday_closed(bars, "5m", now=_time("2026-07-06T13:35:00Z"))[0]["closed"] is True


def test_live_tail_changes_do_not_change_analysis_until_it_closes():
    start = _time("2026-07-06T13:30:00Z")
    now = start + timedelta(minutes=17)
    bars = [_bar(start + timedelta(minutes=5 * i), 100 + i) for i in range(4)]
    first = assemble_intraday_analysis(bars, ticker="AAPL", chart_range="5m", now=now)
    changed = copy.deepcopy(bars)
    changed[-1].update(o=999, h=1001, l=998, c=1000, v=999999)
    second = assemble_intraday_analysis(changed, ticker="AAPL", chart_range="5m", now=now)
    assert first is not None and second is not None
    assert first["barCount"] == 3
    assert first["lastBarDate"] == str(bars[-2]["t"])
    assert first["barFingerprint"] == second["barFingerprint"]
    assert first["overlays"] == second["overlays"]
    completed = assemble_intraday_analysis(changed, ticker="AAPL", chart_range="5m", now=start + timedelta(minutes=20))
    assert completed is not None
    assert completed["barCount"] == 4
    assert completed["barFingerprint"] != first["barFingerprint"]


def test_analysis_excludes_extended_quotes_and_out_of_session_rows():
    bars = [
        _bar(_time("2026-07-06T12:00:00Z"), ext=True),
        _bar(_time("2026-07-06T13:30:00Z"), 101),
        _bar(_time("2026-07-06T13:35:00Z"), 102, quote_only=True),
        _bar(_time("2026-07-06T20:05:00Z"), 103),
        _bar(_time("2026-07-05T13:30:00Z"), 104),
    ]
    bundle = assemble_intraday_analysis(bars, ticker="AAPL", chart_range="5m", now=_time("2026-07-07T14:00:00Z"))
    assert bundle is not None
    assert bundle["barCount"] == 1
    assert bundle["lastClose"] == 101


def test_daily_and_weekly_missing_flags_keep_legacy_compatibility():
    bars = [_bar(_time("2026-07-06T13:30:00Z")), _bar(_time("2026-07-07T13:30:00Z"), closed=False)]
    for chart_range in ["1d", "1w"]:
        assert mark_intraday_closed(bars, chart_range) == bars
        series = series_from_chart_bars(bars, chart_range)
        assert series is not None and len(series["closes"]) == 1


def test_chart_loader_freezes_completion_before_one_provider_call(monkeypatch):
    clock = [_time("2026-07-06T13:37:00Z")]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz is not None else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(stocks, "datetime", Clock)
    bars = [
        _bar(_time("2026-07-06T13:20:00Z"), ext=True),
        _bar(_time("2026-07-06T13:30:00Z"), 101),
        _bar(_time("2026-07-06T13:35:00Z"), 102),
        _bar(_time("2026-07-06T13:40:00Z"), 103),
    ]
    provider_payload = {"bars": bars, "price_provider": "test provider", "range": "5m"}
    original = copy.deepcopy(provider_payload)
    calls = []

    async def provider(ticker, chart_range, adjustment):
        calls.append((ticker, chart_range, adjustment))
        clock[0] = _time("2026-07-06T13:41:00Z")
        return provider_payload

    monkeypatch.setattr(stocks, "_stock_chart_impl", provider)
    payload = asyncio.run(stocks._load_stock_chart("AAPL", "5m", "raw"))
    assert calls == [("AAPL", "5m", "raw")]
    assert provider_payload == original
    assert [bar["closed"] for bar in payload["bars"]] == [True, True, False, False]
    assert [bar.closed for bar in BarsResponse(**payload).bars] == [True, True, False, False]
    assert len(payload["bars"]) == 4
    bundle = payload["chart_analysis"]
    assert bundle["barCount"] == 1
    completed = series_from_chart_bars(payload["bars"], "5m")
    assert bundle["barFingerprint"] == bar_fingerprint(completed)
    assert bundle["lastBarDate"] == str(bars[1]["t"])


def test_provider_chart_tags_early_close_afternoon_as_extended(monkeypatch):
    index = pd.DatetimeIndex(["2025-11-28T17:30:00Z", "2025-11-28T18:00:00Z"])
    history = pd.DataFrame({"Open": [100, 101], "High": [102, 103], "Low": [99, 100], "Close": [101, 102], "Volume": [1000, 500]}, index=index)
    calls = []

    class Ticker:
        def history(self, **kwargs):
            calls.append(kwargs)
            return history

    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _symbol: Ticker())
    payload = asyncio.run(stocks._load_stock_chart("AAPL", "1h", "raw"))
    assert len(calls) == 1
    assert [bar["session"] for bar in payload["bars"]] == ["regular", "post"]
    assert [bar["ext"] for bar in payload["bars"]] == [False, True]
    assert payload["chart_analysis"]["barCount"] == 1
