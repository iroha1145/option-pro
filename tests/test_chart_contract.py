from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.api import stocks
from app.models.schemas import BarsResponse


def _intraday_history() -> pd.DataFrame:
    eastern = ZoneInfo("America/New_York")
    timestamps = [datetime(2026, 7, 6, 8, 0, tzinfo=eastern)]
    timestamps.extend(
        datetime(2026, 7, 6, 9, 30, tzinfo=eastern) + pd.Timedelta(minutes=5 * index)
        for index in range(60)
    )
    timestamps.extend(
        [
            datetime(2026, 7, 6, 16, 5, tzinfo=eastern),
            datetime(2026, 7, 6, 15, 0, tzinfo=eastern),
            datetime(2026, 7, 6, 15, 5, tzinfo=eastern),
        ]
    )

    rows: list[dict[str, float]] = [
        {
            "Open": 0.123456,
            "High": 0.199999,
            "Low": 0.100001,
            "Close": 0.124321,
            "Volume": 0,
        }
    ]
    for index in range(60):
        close = 10.123456 + index * 0.012345
        rows.append(
            {
                "Open": close - 0.004321,
                "High": close + 0.018765,
                "Low": close - 0.021234,
                "Close": close,
                "Volume": 1_000 + index,
            }
        )
    rows.extend(
        [
            {
                "Open": rows[-1]["Close"],
                "High": rows[-1]["Close"] + 0.2,
                "Low": rows[-1]["Close"] - 0.2,
                "Close": rows[-1]["Close"] + 0.01,
                "Volume": 0,
            },
            {
                "Open": 11.0,
                "High": 10.0,
                "Low": 9.0,
                "Close": 10.5,
                "Volume": 10,
            },
            {
                "Open": 11.0,
                "High": 11.2,
                "Low": 10.8,
                "Close": 11.1,
                "Volume": -1,
            },
        ]
    )
    return pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps))


def test_intraday_chart_has_explicit_adjustment_sessions_and_regular_only_indicators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class FakeTicker:
        def __init__(self, symbol: str):
            assert symbol == "PENNY"

        def history(self, **kwargs):
            calls.append(kwargs)
            return _intraday_history()

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)
    payload = asyncio.run(stocks._stock_chart_impl("penny", "5m", "raw"))

    assert calls == [
        {
            "period": "5d",
            "interval": "5m",
            "prepost": True,
            "auto_adjust": False,
        }
    ]
    assert payload["ticker"] == "PENNY"
    assert payload["range"] == "5m"
    assert payload["period"] == "5d"
    assert payload["interval"] == "5m"
    assert payload["exchange_timezone"] == "America/New_York"
    assert payload["price_adjustment"] == "raw"
    assert payload["include_extended_hours"] is True
    assert payload["moving_average_scope"] == "regular_session_only"
    assert payload["source_status"] == "active"
    assert payload["as_of"]
    assert payload["last_bar_at"]

    bars = payload["bars"]
    timestamps = [bar["t"] for bar in bars]
    assert timestamps == sorted(set(timestamps))
    assert all(bar["v"] >= 0 and isinstance(bar["v"], int) for bar in bars)
    assert all(bar["l"] <= min(bar["o"], bar["c"]) <= max(bar["o"], bar["c"]) <= bar["h"] for bar in bars)

    pre = bars[0]
    assert pre["session"] == "pre"
    assert pre["ext"] is True
    assert pre["quote_only"] is True
    assert pre["o"] == pytest.approx(0.123456)
    assert pre["h"] == pytest.approx(0.199999)

    post = bars[-1]
    assert post["session"] == "post"
    assert post["ext"] is True
    assert post["quote_only"] is True

    regular_times = [bar["t"] for bar in bars if bar["session"] == "regular"]
    assert payload["ema20"][0]["time"] == regular_times[19]
    assert payload["sma50"][0]["time"] == regular_times[49]
    assert all(point["time"] in regular_times for point in payload["ema20"] + payload["sma50"])
    assert payload["ema20"][0]["value"] != round(payload["ema20"][0]["value"], 2)


def test_adjusted_chart_explicitly_enables_yfinance_auto_adjust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    class FakeTicker:
        def __init__(self, _symbol: str):
            pass

        def history(self, **kwargs):
            calls.append(kwargs)
            return pd.DataFrame()

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)
    payload = asyncio.run(stocks._stock_chart_impl("AAPL", "1d", "adjusted"))

    assert calls[0]["auto_adjust"] is True
    assert calls[0]["prepost"] is False
    assert payload["price_adjustment"] == "adjusted"
    assert payload["source_status"] == "empty"
    assert payload["bars"] == []


def test_naive_daily_index_is_interpreted_as_new_york_across_dst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = pd.DataFrame(
        [
            {"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1_000},
            {"Open": 101, "High": 103, "Low": 100, "Close": 102, "Volume": 1_200},
        ],
        index=pd.DatetimeIndex(["2026-03-06", "2026-03-09"]),
    )

    class FakeTicker:
        def __init__(self, _symbol: str):
            pass

        def history(self, **_kwargs):
            return history

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)
    payload = asyncio.run(stocks._stock_chart_impl("AAPL", "1d", "raw"))

    first = datetime.fromtimestamp(payload["bars"][0]["t"], ZoneInfo("UTC"))
    second = datetime.fromtimestamp(payload["bars"][1]["t"], ZoneInfo("UTC"))
    assert first.hour == 5
    assert second.hour == 4
    assert payload["bars"][0]["session"] == "regular"
    assert payload["bars"][1]["session"] == "regular"


def test_bars_response_remains_compatible_with_bars_only_payload() -> None:
    response = BarsResponse(bars=[])

    assert response.bars == []
    assert response.price_adjustment == "raw"
    assert response.ema20 == []
    assert response.sma50 == []


def test_stock_overview_preserves_sub_dollar_quote_precision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTicker:
        def __init__(self, symbol: str):
            assert symbol == "PENNY"
            self.fast_info = SimpleNamespace(
                last_price=0.123456,
                previous_close=0.120001,
                last_volume=None,
                market_cap=None,
            )
            self.info = {
                "shortName": "Penny Test",
                "dayHigh": 0.129999,
                "dayLow": 0.118765,
                "open": 0.121234,
            }

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)
    payload = asyncio.run(stocks._stock_overview_impl("penny"))

    assert payload["price"] == pytest.approx(0.123456)
    assert payload["prev_close"] == pytest.approx(0.120001)
    assert payload["change"] == pytest.approx(0.003455)
    assert payload["price"] != round(payload["price"], 2)
    assert payload["volume"] is None
    assert payload["market_cap"] is None
