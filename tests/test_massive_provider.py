"""Massive 主源 provider 的纯单元测试(不出网,HTTP 层全部打桩)。"""

from __future__ import annotations

import types

import pytest

from app.services import massive


def test_symbol_mapping_covers_indices_classes_and_unsupported() -> None:
    assert massive.to_symbol("AAPL") == "AAPL"
    assert massive.to_symbol(" nvda ") == "NVDA"
    assert massive.to_symbol("^GSPC") == "I:SPX"
    assert massive.to_symbol("^VIX") == "I:VIX"
    assert massive.to_symbol("BRK-B") == "BRK.B"
    assert massive.to_symbol("BRK.B") == "BRK.B"
    assert massive.to_symbol("CWEN.A") == "CWEN.A"
    assert massive.to_yahoo_symbol("BRK.B") == "BRK-B"
    assert massive.to_yahoo_symbol("AAOI") == "AAOI"
    assert massive.to_symbol("ES=F") is None      # 期货不在范围
    assert massive.to_symbol("RMS.PA") is None    # 非美市场后缀
    assert massive.to_symbol("^UNKNOWNIDX") is None
    assert massive.to_symbol("") is None


def test_grouped_daily_parses_and_drops_invalid_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "results": [
            {"T": "AAPL", "t": 1_700_000_000_000, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10},
            {"T": "BAD", "t": 1_700_000_000_000, "c": 0},          # 非法收盘价
            {"T": "", "t": 1_700_000_000_000, "c": 3.0},           # 空代码
            {"T": "NAN", "t": 1_700_000_000_000, "c": float("nan")},
        ]
    }
    monkeypatch.setattr(massive, "_get", lambda path, params=None: payload)
    rows = massive.grouped_daily("2026-07-18")
    assert set(rows) == {"AAPL"}
    assert rows["AAPL"]["c"] == 1.5


def test_watchlist_daily_closes_covers_and_reports_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    days = ["2026-07-18", "2026-07-17", "2026-07-16"]
    monkeypatch.setattr(massive, "recent_session_days", lambda sessions, today=None: days)
    by_day = {
        "2026-07-18": {"AAPL": {"t": 3, "c": 103.0}, "BRK.B": {"t": 3, "c": 403.0}},
        "2026-07-17": {"AAPL": {"t": 2, "c": 102.0}, "BRK.B": {"t": 2, "c": 402.0}},
        "2026-07-16": {"AAPL": {"t": 1, "c": 101.0}},
    }
    monkeypatch.setattr(massive, "grouped_daily", lambda day: by_day.get(day, {}))

    covered, missing = massive.watchlist_daily_closes(
        ["AAPL", "BRK-B", "^GSPC", "ES=F", "GHOST"], sessions=3
    )
    # 升序、按票覆盖
    assert covered["AAPL"] == [(1, 101.0), (2, 102.0), (3, 103.0)]
    assert covered["BRK-B"] == [(2, 402.0), (3, 403.0)]
    # 指数走 I: 前缀不进 grouped、期货不支持、无数据代码 → missing
    assert set(missing) == {"^GSPC", "ES=F", "GHOST"}


def test_watchlist_daily_closes_propagates_hard_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        massive, "recent_session_days", lambda sessions, today=None: ["2026-07-18"]
    )

    def boom(day):
        raise massive.MassiveError("rate limited", code="rate_limited", status=429)

    monkeypatch.setattr(massive, "grouped_daily", boom)
    with pytest.raises(massive.MassiveError):
        massive.watchlist_daily_closes(["AAPL"], sessions=1)


def test_snapshot_batch_batches_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get(path, params=None):
        calls.append(params["tickers"].split(","))
        return {
            "tickers": [
                {
                    "ticker": symbol,
                    "min": {"t": 1_700_000_000_000, "c": 10.0, "o": 9.0, "h": 11.0, "l": 8.0, "v": 5},
                    "day": {
                        "t": 1_700_000_000_000,
                        "c": 10.5,
                        "o": 9.5,
                        "h": 11.5,
                        "l": 8.5,
                        "v": 50,
                    },
                    "prevDay": {"c": 9.5},
                }
                for symbol in calls[-1][:1]  # 每批只回第一只,验证部分覆盖可行
            ]
        }

    monkeypatch.setattr(massive, "_get", fake_get)
    symbols = [f"S{i}" for i in range(150)]
    out = massive.snapshot_batch(symbols)
    assert len(calls) == 2                      # 100 + 50 分批
    assert set(out) == {"S0", "S100"}
    assert out["S0"]["minute"]["c"] == 10.0
    assert out["S0"]["day"]["o"] == 9.5
    assert out["S0"]["day"]["v"] == 50
    assert out["S0"]["prev_close"] == 9.5
    assert out["S0"]["as_of"] == "2023-11-14T22:13:20+00:00"


def test_ticker_range_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_get(path, params=None):
        seen["path"] = path
        seen["params"] = params
        return {"results": [{"t": 1_700_000_000_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3}]}

    monkeypatch.setattr(massive, "_get", fake_get)
    bars = massive.ticker_range("AAPL", 5, "minute", "2026-07-01", "2026-07-18")
    assert seen["path"] == "/v2/aggs/ticker/AAPL/range/5/minute/2026-07-01/2026-07-18"
    assert seen["params"]["adjusted"] == "false"
    assert seen["params"]["sort"] == "asc"
    assert bars[0]["c"] == 1.5


def test_reference_tickers_reads_complete_us_directory_with_safe_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(massive, "_REFERENCE_PAGE_SIZE", 2)

    def fake_get(path, params=None):
        assert path == "/v3/reference/tickers"
        calls.append(dict(params))
        if "cursor" not in params:
            return {
                "results": [
                    {
                        "ticker": "AAOI",
                        "name": "Applied Optoelectronics, Inc.",
                        "market": "stocks",
                        "locale": "us",
                        "type": "CS",
                        "active": True,
                    },
                    {
                        "ticker": "NBIS",
                        "name": "Nebius Group N.V. Class A",
                        "market": "stocks",
                        "locale": "us",
                        "type": "CS",
                        "active": True,
                    },
                ],
                "next_url": (
                    "https://api.massive.com/v3/reference/tickers"
                    "?cursor=opaque-page-2&apiKey=must-not-be-forwarded"
                ),
            }
        assert params == {"cursor": "opaque-page-2"}
        return {
            "results": [
                {
                    "ticker": "ZZZZ",
                    "name": "",
                    "market": "stocks",
                    "locale": "us",
                    "type": "CS",
                    "active": True,
                }
            ]
        }

    monkeypatch.setattr(massive, "_get", fake_get)
    rows = massive.reference_tickers()

    assert [row["ticker"] for row in rows] == ["AAOI", "NBIS", "ZZZZ"]
    assert rows[-1]["name"] == "ZZZZ"
    assert len(calls) == 2
    assert calls[0]["market"] == "stocks"
    assert calls[0]["locale"] == "us"
    assert calls[0]["active"] == "true"
    assert "apiKey" not in calls[0]
    assert all("apiKey" not in call for call in calls)


def test_reference_tickers_does_not_publish_a_partial_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_get(_path, params=None):
        nonlocal calls
        calls += 1
        if "cursor" not in params:
            return {
                "results": [
                    {
                        "ticker": "AAOI",
                        "name": "Applied Optoelectronics, Inc.",
                        "market": "stocks",
                        "locale": "us",
                        "type": "CS",
                        "active": True,
                    }
                ],
                "next_url": (
                    "https://api.massive.com/v3/reference/tickers"
                    "?cursor=opaque-page-2"
                ),
            }
        return {"status": "OK"}

    monkeypatch.setattr(massive, "_get", fake_get)
    with pytest.raises(massive.MassiveError) as captured:
        massive.reference_tickers()
    assert captured.value.code == "protocol"
    assert calls == 2


@pytest.mark.parametrize(
    "next_url",
    [
        "https://attacker.invalid/v3/reference/tickers?cursor=page-2",
        "https://api.massive.com/v3/reference/options/contracts?cursor=page-2",
        "https://api.massive.com/v3/reference/tickers?apiKey=secret",
        "https://api.massive.com/v3/reference/tickers?cursor=page-2&cursor=again",
    ],
)
def test_reference_tickers_rejects_unsafe_or_invalid_next_urls(
    monkeypatch: pytest.MonkeyPatch,
    next_url: str,
) -> None:
    monkeypatch.setattr(
        massive,
        "_get",
        lambda *_args, **_kwargs: {
            "results": [],
            "next_url": next_url,
        },
    )
    with pytest.raises(massive.MassiveError) as captured:
        massive.reference_tickers()
    assert captured.value.code == "protocol"


def test_unconfigured_get_raises_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        massive,
        "get_settings",
        lambda: types.SimpleNamespace(massive_api_key="", massive_base_url="https://api.massive.com"),
    )
    assert massive.configured() is False
    with pytest.raises(massive.MassiveError) as exc_info:
        massive._get("/v2/aggs/ticker/AAPL/prev")
    assert exc_info.value.code == "not_configured"


def test_get_fails_fast_when_provider_concurrency_gate_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = types.SimpleNamespace(
        acquire=lambda **_kwargs: False,
        release=lambda: (_ for _ in ()).throw(
            AssertionError("unacquired gate was released")
        ),
    )
    monkeypatch.setattr(
        massive,
        "get_settings",
        lambda: types.SimpleNamespace(
            massive_api_key="configured-for-test",
            massive_base_url="https://api.massive.com",
        ),
    )
    monkeypatch.setattr(massive, "_request_gate", gate)
    monkeypatch.setattr(
        massive,
        "_http",
        lambda: (_ for _ in ()).throw(
            AssertionError("busy gate still called provider")
        ),
    )

    with pytest.raises(massive.MassiveError) as exc_info:
        massive._get("/v2/aggs/ticker/AAPL/prev")

    assert exc_info.value.code == "provider_busy"


def test_chart_history_helper_builds_yfinance_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import stocks

    fake_provider = types.SimpleNamespace(
        MassiveError=massive.MassiveError,
        ticker_range=lambda *a, **k: [
            {"t": 1_700_000_000_000, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 7},
            {"t": 1_700_000_300_000, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 8},
        ],
    )
    frame = stocks._massive_chart_history(fake_provider, "AAPL", "5m", adjusted=False)
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(frame) == 2
    assert frame.index.tz is not None           # tz-aware,下游无需 replace
    assert float(frame["Close"].iloc[-1]) == 2.0


def test_chart_history_helper_falls_back_to_none_on_error() -> None:
    from app.api import stocks

    def boom(*a, **k):
        raise massive.MassiveError("plan", code="plan", status=403)

    fake_provider = types.SimpleNamespace(MassiveError=massive.MassiveError, ticker_range=boom)
    assert stocks._massive_chart_history(fake_provider, "AAPL", "1d", adjusted=False) is None
    assert stocks._massive_chart_history(fake_provider, "AAPL", "nope", adjusted=False) is None
