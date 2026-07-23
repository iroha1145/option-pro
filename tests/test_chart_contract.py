from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.api import stocks
from app.models.schemas import BarsResponse
from app.services import massive


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
    assert pre["h"] == pytest.approx(max(pre["o"], pre["c"]))
    assert pre["l"] == pytest.approx(min(pre["o"], pre["c"]))

    post = bars[-1]
    assert post["session"] == "post"
    assert post["ext"] is True
    assert post["quote_only"] is True
    assert post["h"] == pytest.approx(max(post["o"], post["c"]))
    assert post["l"] == pytest.approx(min(post["o"], post["c"]))

    regular_times = [bar["t"] for bar in bars if bar["session"] == "regular"]
    assert payload["ema20"][0]["time"] == regular_times[19]
    assert payload["sma50"][0]["time"] == regular_times[49]
    assert all(point["time"] in regular_times for point in payload["ema20"] + payload["sma50"])
    assert payload["ema20"][0]["value"] != round(payload["ema20"][0]["value"], 2)


def test_extended_hours_bar_with_volume_keeps_real_extreme_prices() -> None:
    bar = {
        "o": 100.0,
        "h": 160.0,
        "l": 70.0,
        "c": 101.0,
        "v": 25,
        "ext": True,
        "session": "pre",
        "quote_only": False,
    }

    result = stocks._normalize_extended_quote_bar(bar, reference_close=100.0)

    assert result is bar
    assert result["h"] == pytest.approx(160.0)
    assert result["l"] == pytest.approx(70.0)
    assert result["quote_only"] is False


def test_zero_volume_extended_isolated_open_rejoins_previous_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eastern = ZoneInfo("America/New_York")
    history = pd.DataFrame(
        [
            {"Open": 315.12, "High": 315.2, "Low": 315.0, "Close": 315.1019, "Volume": 0},
            {"Open": 331.7827, "High": 331.7827, "Low": 315.0, "Close": 315.0559, "Volume": 0},
            {"Open": 315.05, "High": 315.3, "Low": 315.0, "Close": 315.1861, "Volume": 0},
        ],
        index=pd.DatetimeIndex(
            [
                datetime(2026, 7, 10, 17, 15, tzinfo=eastern),
                datetime(2026, 7, 10, 17, 20, tzinfo=eastern),
                datetime(2026, 7, 10, 17, 25, tzinfo=eastern),
            ]
        ),
    )

    class FakeTicker:
        def __init__(self, symbol: str):
            assert symbol == "AAPL"

        def history(self, **_kwargs):
            return history

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)

    payload = asyncio.run(stocks._stock_chart_impl("AAPL", "5m", "raw"))

    assert len(payload["bars"]) == 3
    previous, repaired, following = payload["bars"]
    assert datetime.fromtimestamp(repaired["t"], ZoneInfo("UTC")).isoformat() == "2026-07-10T21:20:00+00:00"
    assert repaired["session"] == "post"
    assert repaired["v"] == 0
    assert repaired["quote_only"] is True
    assert repaired["o"] == pytest.approx(previous["c"])
    assert repaired["o"] == pytest.approx(315.1019)
    assert repaired["c"] == pytest.approx(315.0559)
    assert repaired["h"] == pytest.approx(max(repaired["o"], repaired["c"]))
    assert repaired["l"] == pytest.approx(min(repaired["o"], repaired["c"]))
    assert following["c"] == pytest.approx(315.1861)


def test_zero_volume_extended_real_close_jump_is_not_reanchored() -> None:
    bar = {
        "o": 105.5,
        "h": 106.0,
        "l": 104.5,
        "c": 105.0,
        "v": 0,
        "ext": True,
        "session": "post",
        "quote_only": False,
    }

    result = stocks._normalize_extended_quote_bar(bar, reference_close=100.0)

    assert result is bar
    assert result["o"] == pytest.approx(105.5)
    assert result["c"] == pytest.approx(105.0)
    assert result["h"] == pytest.approx(105.5)
    assert result["l"] == pytest.approx(105.0)
    assert result["quote_only"] is True


def test_daily_raw_and_adjusted_keep_zero_volume_high_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = pd.DataFrame(
        [{"Open": 100.0, "High": 160.0, "Low": 70.0, "Close": 101.0, "Volume": 0}],
        index=pd.DatetimeIndex(["2026-07-10"]),
    )
    calls: list[dict] = []

    class FakeTicker:
        def __init__(self, _symbol: str):
            pass

        def history(self, **kwargs):
            calls.append(kwargs)
            return history

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)

    raw = asyncio.run(stocks._stock_chart_impl("AAPL", "1d", "raw"))
    adjusted = asyncio.run(stocks._stock_chart_impl("AAPL", "1d", "adjusted"))

    for payload in (raw, adjusted):
        assert len(payload["bars"]) == 1
        bar = payload["bars"][0]
        assert bar["h"] == pytest.approx(160.0)
        assert bar["l"] == pytest.approx(70.0)
        assert bar["v"] == 0
        assert bar["session"] == "regular"
        assert bar["quote_only"] is False

    assert [call["prepost"] for call in calls] == [False, False]
    assert [call["auto_adjust"] for call in calls] == [False, True]


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
    monkeypatch.setattr(massive, "configured", lambda: False)
    payload = asyncio.run(stocks._stock_overview_impl("penny"))

    assert payload["price"] == pytest.approx(0.123456)
    assert payload["prev_close"] == pytest.approx(0.120001)
    assert payload["change"] == pytest.approx(0.003455)
    assert payload["price"] != round(payload["price"], 2)
    assert payload["volume"] is None
    assert payload["market_cap"] is None
    assert payload["price_provider"] == "Yahoo/yfinance"


def test_stock_overview_overlays_massive_quote_and_keeps_yahoo_fundamentals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_at = datetime(2026, 7, 23, 14, 15, tzinfo=timezone.utc)

    class FakeTicker:
        def __init__(self, symbol: str):
            assert symbol == "AAPL"
            self.fast_info = SimpleNamespace(
                last_price=190.0,
                previous_close=188.0,
                last_volume=1_000,
                market_cap=3_000_000_000_000,
            )
            self.info = {
                "shortName": "Apple Inc.",
                "regularMarketTime": int(
                    datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc).timestamp()
                ),
                "dayHigh": 191.0,
                "dayLow": 187.0,
                "open": 189.0,
                "longBusinessSummary": "Consumer technology company.",
                "industry": "Consumer Electronics",
                "trailingPE": 31.0,
            }

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(
        massive,
        "snapshot_batch",
        lambda symbols: {
            "AAPL": {
                "minute": {
                    "t": int(quote_at.timestamp() * 1_000),
                    "c": 200.25,
                },
                "day": {
                    "t": int(quote_at.timestamp() * 1_000),
                    "o": 197.0,
                    "h": 202.0,
                    "l": 196.5,
                    "c": 200.25,
                    "v": 2_500,
                },
                "prev_close": 198.0,
                "as_of": quote_at.isoformat(),
            }
        },
    )

    payload = asyncio.run(stocks._stock_overview_impl("aapl"))

    assert payload["price"] == pytest.approx(200.25)
    assert payload["prev_close"] == pytest.approx(198.0)
    assert payload["change"] == pytest.approx(2.25)
    assert payload["open"] == pytest.approx(197.0)
    assert payload["high"] == pytest.approx(202.0)
    assert payload["low"] == pytest.approx(196.5)
    assert payload["volume"] == 2_500
    assert payload["as_of"] == quote_at.isoformat()
    assert payload["price_provider"] == "Massive"
    assert payload["name_en"] == "Apple Inc."
    assert payload["sic_description"] == "Consumer Electronics"
    assert payload["pe_ratio"] == pytest.approx(31.0)


def test_stock_overview_falls_back_to_yahoo_quote_when_massive_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_at = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)

    class FakeTicker:
        def __init__(self, symbol: str):
            assert symbol == "AAPL"
            self.fast_info = SimpleNamespace(
                last_price=190.0,
                previous_close=188.0,
                last_volume=1_000,
                market_cap=3_000_000_000_000,
            )
            self.info = {
                "shortName": "Apple Inc.",
                "regularMarketTime": int(quote_at.timestamp()),
                "dayHigh": 191.0,
                "dayLow": 187.0,
                "open": 189.0,
            }

    def fail(_symbols):
        raise massive.MassiveError("plan", code="plan", status=403)

    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "snapshot_batch", fail)

    payload = asyncio.run(stocks._stock_overview_impl("AAPL"))

    assert payload["price"] == pytest.approx(190.0)
    assert payload["prev_close"] == pytest.approx(188.0)
    assert payload["open"] == pytest.approx(189.0)
    assert payload["high"] == pytest.approx(191.0)
    assert payload["low"] == pytest.approx(187.0)
    assert payload["volume"] == 1_000
    assert payload["as_of"] == quote_at.isoformat()
    assert payload["price_provider"] == "Yahoo/yfinance"
