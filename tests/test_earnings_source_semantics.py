from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from app.api import earnings
from app.public_home_snapshot import validate_public_home_payload


_REAL_EXPECTED_MOVE_FOR_REPORT = earnings._expected_move_for_report


def _finnhub_success(
    rows: list[dict[str, object]],
    *,
    truncated: bool = False,
) -> dict[str, object]:
    return earnings._finnhub_fetch_result(
        rows=rows,
        configured=True,
        succeeded=True,
        truncated=truncated,
    )


@pytest.fixture(autouse=True)
def _clear_earnings_state(monkeypatch: pytest.MonkeyPatch) -> None:
    earnings.cache.clear()
    earnings._refresh_deadlines.clear()
    monkeypatch.setattr(
        earnings,
        "_expected_move_for_report",
        lambda _ticker, _report_date, _today, _timing: {},
    )
    yield
    earnings.cache.clear()
    earnings._refresh_deadlines.clear()


def test_expected_move_uses_nearest_at_the_money_real_straddle() -> None:
    snapshot = {
        "underlying_price": 100,
        "calls": [
            {"strike": 95, "midpoint": 7.2},
            {"strike": 100, "bid": 3.8, "ask": 4.2, "midpoint": 4.0},
            {"strike": 105, "midpoint": 1.9},
        ],
        "puts": [
            {"strike": 95, "midpoint": 1.8},
            {"strike": 100, "midpoint": 3.5},
            {"strike": 105, "midpoint": 6.8},
        ],
    }

    assert earnings._expected_move_from_chain_snapshot(snapshot) == 7.5
    assert earnings._expected_move_from_chain_snapshot(
        {"underlying_price": 100, "calls": [], "puts": []}
    ) is None


def test_expected_move_keeps_real_chain_provenance_and_rejects_stale_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import yahoo as yahoo_provider

    monkeypatch.setattr(
        yahoo_provider,
        "get_expirations_snapshot",
        lambda _ticker: {"expirations": ["2026-08-07"]},
    )
    chain = {
        "source_status": "active",
        "as_of": "2026-07-23T20:15:00Z",
        "underlying_price": 100,
        "calls": [{"strike": 100, "midpoint": 4.0}],
        "puts": [{"strike": 100, "midpoint": 3.5}],
    }
    monkeypatch.setattr(
        yahoo_provider,
        "get_option_chain",
        lambda _ticker, _expiration: dict(chain),
    )

    result = _REAL_EXPECTED_MOVE_FOR_REPORT(
        "TEST",
        date(2026, 8, 6),
        date(2026, 7, 23),
        "amc",
    )

    assert result == {
        "expected_move_pct": 7.5,
        "expected_move_expiration": "2026-08-07",
        "expected_move_source": "Yahoo/yfinance options",
        "expected_move_observed_at": "2026-07-23T20:15:00Z",
        "expected_move_source_status": "active",
    }

    monkeypatch.setattr(
        yahoo_provider,
        "get_option_chain",
        lambda _ticker, _expiration: {**chain, "_stale": True},
    )
    assert (
        _REAL_EXPECTED_MOVE_FOR_REPORT(
            "TEST",
            date(2026, 8, 6),
            date(2026, 7, 23),
            "amc",
        )
        == {}
    )


def test_finnhub_fetch_accepts_valid_symbols_outside_curated_yahoo_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str]] = []
    active = 0
    max_active = 0

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "earningsCalendar": [
                    {
                        "symbol": "AAOI",
                        "date": "2026-07-30",
                        "hour": "amc",
                        "epsEstimate": 0.31,
                    },
                    {
                        "symbol": "NBIS",
                        "date": "2026-08-03",
                        "epsEstimate": -0.22,
                    },
                    {
                        "symbol": "NBIS",
                        "date": "2026-08-03",
                        "epsActual": -0.20,
                    },
                    {
                        "symbol": "../INVALID",
                        "date": "2026-08-03",
                    },
                    {
                        "symbol": "TOOLATE",
                        "date": "2027-02-01",
                    },
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, params):
            nonlocal active, max_active
            calls.append(dict(params))
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return Response()

    monkeypatch.setattr(
        earnings,
        "get_settings",
        lambda: SimpleNamespace(
            finnhub_api_key="test-token",
            finnhub_base_url="https://finnhub.example/api/v1",
        ),
    )
    monkeypatch.setattr(
        earnings.httpx,
        "AsyncClient",
        lambda **_kwargs: Client(),
    )
    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["AAPL"])

    result = asyncio.run(
        earnings._fetch_finnhub_earnings(date(2026, 7, 23))
    )
    rows = result["rows"]

    assert result["configured"] is True
    assert result["succeeded"] is True
    assert result["truncated"] is False
    assert result["error"] is None
    assert calls == [
        {"from": "2026-07-20", "to": "2026-07-26"},
        {"from": "2026-07-27", "to": "2026-08-02"},
        {"from": "2026-08-03", "to": "2026-08-09"},
        {"from": "2026-08-10", "to": "2026-08-16"},
        {"from": "2026-08-17", "to": "2026-08-22"},
    ]
    assert max_active == 5
    assert [row["ticker"] for row in rows] == ["AAOI", "NBIS"]
    assert rows[1]["eps_estimate"] == -0.22
    assert rows[1]["eps_actual"] == -0.20


def test_finnhub_saturated_week_splits_to_days_and_keeps_google_actual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str]] = []

    class Response:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self._rows = rows

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"earningsCalendar": self._rows}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, params):
            request = dict(params)
            calls.append(request)
            if request == {"from": "2026-07-20", "to": "2026-07-26"}:
                return Response(
                    [
                        {"symbol": "HARD-CAP", "date": "2026-07-22"}
                    ]
                    * earnings.FINNHUB_RESPONSE_HARD_LIMIT
                )
            if request == {"from": "2026-07-22", "to": "2026-07-22"}:
                return Response(
                    [
                        {
                            "symbol": "GOOGL",
                            "date": "2026-07-22",
                            "hour": "amc",
                            "epsEstimate": 2.95,
                            "epsActual": 3.22,
                            "revenueEstimate": 82_800_000_000,
                            "revenueActual": 85_100_000_000,
                            "quarter": 2,
                            "year": 2026,
                        }
                    ]
                )
            return Response([])

    monkeypatch.setattr(
        earnings,
        "get_settings",
        lambda: SimpleNamespace(
            finnhub_api_key="test-token",
            finnhub_base_url="https://finnhub.example/api/v1",
        ),
    )
    monkeypatch.setattr(
        earnings.httpx,
        "AsyncClient",
        lambda **_kwargs: Client(),
    )

    result = asyncio.run(
        earnings._fetch_finnhub_earnings(date(2026, 7, 23))
    )

    daily_calls = [
        call
        for call in calls
        if call["from"] == call["to"] and "2026-07-20" <= call["from"] <= "2026-07-26"
    ]
    assert [call["from"] for call in daily_calls] == [
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
        "2026-07-23",
        "2026-07-24",
        "2026-07-25",
        "2026-07-26",
    ]
    assert result["succeeded"] is True
    assert result["truncated"] is False
    assert result["error"] is None
    assert result["rows"] == [
        {
            "ticker": "GOOGL",
            "earnings_date": "2026-07-22",
            "days_until": -1,
            "timing": "amc",
            "eps_estimate": 2.95,
            "eps_actual": 3.22,
            "revenue_estimate": 82_800_000_000.0,
            "revenue_actual": 85_100_000_000.0,
            "quarter": 2,
            "year": 2026,
        }
    ]


def test_finnhub_daily_hard_cap_is_reported_as_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self._rows = rows

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"earningsCalendar": self._rows}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, params):
            request = dict(params)
            if request == {"from": "2026-07-20", "to": "2026-07-26"}:
                return Response(
                    [
                        {"symbol": "HARD-CAP", "date": "2026-07-22"}
                    ]
                    * earnings.FINNHUB_RESPONSE_HARD_LIMIT
                )
            if request == {"from": "2026-07-22", "to": "2026-07-22"}:
                return Response(
                    [
                        {"symbol": "GOOGL", "date": "2026-07-22"}
                    ]
                    * earnings.FINNHUB_RESPONSE_HARD_LIMIT
                )
            return Response([])

    monkeypatch.setattr(
        earnings,
        "get_settings",
        lambda: SimpleNamespace(
            finnhub_api_key="test-token",
            finnhub_base_url="https://finnhub.example/api/v1",
        ),
    )
    monkeypatch.setattr(
        earnings.httpx,
        "AsyncClient",
        lambda **_kwargs: Client(),
    )

    result = asyncio.run(
        earnings._fetch_finnhub_earnings(date(2026, 7, 23))
    )

    assert result["succeeded"] is True
    assert result["truncated"] is True
    assert [row["ticker"] for row in result["rows"]] == ["GOOGL"]


def test_finnhub_unconfigured_returns_explicit_unavailable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        earnings,
        "get_settings",
        lambda: SimpleNamespace(
            finnhub_api_key="",
            finnhub_base_url="https://finnhub.example/api/v1",
        ),
    )

    result = asyncio.run(
        earnings._fetch_finnhub_earnings(date(2026, 7, 23))
    )

    assert result == {
        "rows": [],
        "configured": False,
        "succeeded": False,
        "truncated": False,
        "error": "not_configured",
    }


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    (
        ("timeout", "timeout"),
        ("unauthorized", "unauthorized"),
        ("protocol", "protocol_error"),
        ("empty", "empty_payload"),
    ),
)
def test_finnhub_failures_return_explicit_status(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_error: str,
) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, params):
            if mode == "timeout":
                raise earnings.httpx.ReadTimeout("provider timed out")
            if mode == "unauthorized":
                return earnings.httpx.Response(
                    401,
                    request=earnings.httpx.Request("GET", url, params=params),
                )

            class InvalidPayload:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, object]:
                    return (
                        {"earningsCalendar": []}
                        if mode == "empty"
                        else {"unexpected": []}
                    )

            return InvalidPayload()

    monkeypatch.setattr(
        earnings,
        "get_settings",
        lambda: SimpleNamespace(
            finnhub_api_key="test-token",
            finnhub_base_url="https://finnhub.example/api/v1",
        ),
    )
    monkeypatch.setattr(
        earnings.httpx,
        "AsyncClient",
        lambda **_kwargs: Client(),
    )

    result = asyncio.run(
        earnings._fetch_finnhub_earnings(date(2026, 7, 23))
    )

    assert result["rows"] == []
    assert result["configured"] is True
    assert result["succeeded"] is False
    assert result["truncated"] is False
    assert result["error"] == expected_error


def test_finnhub_truncation_is_reported_and_degrades_final_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "earningsCalendar": [
                    {"symbol": "AAOI", "date": "2026-07-30"},
                    {"symbol": "NBIS", "date": "2026-07-31"},
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, params):
            return Response()

    monkeypatch.setattr(
        earnings,
        "get_settings",
        lambda: SimpleNamespace(
            finnhub_api_key="test-token",
            finnhub_base_url="https://finnhub.example/api/v1",
        ),
    )
    monkeypatch.setattr(
        earnings.httpx,
        "AsyncClient",
        lambda **_kwargs: Client(),
    )
    monkeypatch.setattr(earnings, "MAX_FINNHUB_EARNINGS_ROWS", 1)
    result = asyncio.run(
        earnings._fetch_finnhub_earnings(date(2026, 7, 23))
    )

    async def truncated_result(_today):
        return result

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", [])
    monkeypatch.setattr(
        earnings,
        "_fetch_finnhub_earnings",
        truncated_result,
    )
    payload = asyncio.run(
        earnings._build_upcoming_earnings(date(2026, 7, 23))
    )

    assert [row["ticker"] for row in result["rows"]] == ["AAOI"]
    assert result["truncated"] is True
    assert payload["data_limited"] is True
    assert payload["source_status"] == "degraded"


def test_pure_finnhub_calendar_covers_non_curated_company_and_reports_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def finnhub_rows(_today):
        return _finnhub_success([
            {
                "ticker": "AAOI",
                "earnings_date": "2026-07-30",
                "days_until": 7,
                "timing": "amc",
                "eps_estimate": 0.31,
                "eps_actual": None,
                "revenue_estimate": 118_000_000,
                "revenue_actual": None,
                "quarter": 2,
                "year": 2026,
            }
        ])

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", [])
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", finnhub_rows)

    payload = asyncio.run(
        earnings._build_upcoming_earnings(date(2026, 7, 23))
    )

    assert [row["ticker"] for row in payload["earnings"]] == ["AAOI"]
    assert set(payload["earnings"][0]) == set(earnings._EARNINGS_OUTPUT_FIELDS)
    assert payload["earnings"][0]["earnings_date_source"] == "finnhub_calendar"
    assert payload["providers"] == ["Finnhub"]
    assert payload["attempted"] == 1
    assert payload["succeeded"] == 1
    assert payload["data_limited"] is False
    assert payload["source_status"] == "active"
    assert validate_public_home_payload("earnings", payload) == payload


def test_complete_finnhub_window_is_not_downgraded_by_yahoo_enrichment_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def finnhub_rows(_today):
        return _finnhub_success([
            {
                "ticker": "AAOI",
                "earnings_date": "2026-07-30",
                "days_until": 7,
                "timing": "amc",
                "eps_estimate": 0.31,
                "eps_actual": None,
                "revenue_estimate": 118_000_000,
                "revenue_actual": None,
                "quarter": 2,
                "year": 2026,
            }
        ])

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["YFONLY"])
    monkeypatch.setattr(
        earnings.yf,
        "Ticker",
        lambda _symbol: (_ for _ in ()).throw(RuntimeError("Yahoo unavailable")),
    )
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", finnhub_rows)

    payload = asyncio.run(
        earnings._build_upcoming_earnings(date(2026, 7, 23))
    )

    assert payload["failed_symbols"] == ["YFONLY"]
    assert payload["providers"] == ["Finnhub"]
    assert payload["data_limited"] is False
    assert payload["source_status"] == "active"
    assert validate_public_home_payload("earnings", payload) == payload


def test_calendar_date_and_estimates_publish_matching_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CalendarTicker:
        calendar = {
            "Earnings Date": [date(2026, 7, 20)],
            "Earnings Average": [1.25],
            "Earnings High": [1.40],
            "Earnings Low": [1.10],
            "Revenue Average": [12_000_000_000],
        }
        info = {"shortName": "Calendar Corp"}

        def get_earnings_dates(self, limit=12):
            raise AssertionError("fallback must not run for a usable calendar date")

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["CAL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: CalendarTicker())

    async def unavailable_finnhub(_today):
        return earnings._finnhub_fetch_result(
            configured=False,
            succeeded=False,
            error="not_configured",
        )

    monkeypatch.setattr(
        earnings,
        "_fetch_finnhub_earnings",
        unavailable_finnhub,
    )

    payload = asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 10)))

    row = payload["earnings"][0]
    assert row["earnings_date"] == "2026-07-20"
    assert row["eps_estimate"] == 1.25
    assert row["revenue_estimate"] == 12_000_000_000
    assert row["earnings_date_source"] == "calendar"
    assert row["estimate_source"] == "calendar"
    assert row["source_status"] == "active"
    assert row["observed_at"]
    assert payload["providers"] == ["Yahoo Finance"]
    assert payload["data_limited"] is True
    assert payload["source_status"] == "degraded"


def test_fallback_date_does_not_reuse_estimates_from_an_old_calendar_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FallbackTicker:
        calendar = {
            "Earnings Date": [date(2026, 7, 1)],
            "Earnings Average": [9.99],
            "Earnings High": [10.50],
            "Earnings Low": [9.50],
            "Revenue Average": [999_000_000],
        }
        info = {"shortName": "Fallback Corp"}

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame(index=pd.DatetimeIndex(["2026-08-15"]))

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["FALL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: FallbackTicker())

    payload = asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 10)))

    row = payload["earnings"][0]
    assert row["earnings_date"] == "2026-08-15"
    assert row["earnings_date_source"] == "earnings_dates"
    assert row["estimate_source"] is None
    assert row["eps_estimate"] is None
    assert row["eps_high"] is None
    assert row["eps_low"] is None
    assert row["revenue_estimate"] is None
    assert row["source_status"] == "active"


def test_finnhub_failure_marks_yahoo_fallback_as_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class YahooTicker:
        calendar = {"Earnings Date": [date(2026, 7, 30)]}
        info = {"shortName": "Yahoo Fallback Corp"}

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame()

    async def failed_finnhub(_today):
        return earnings._finnhub_fetch_result(
            configured=True,
            succeeded=False,
            error="timeout",
        )

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["FALL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: YahooTicker())
    monkeypatch.setattr(
        earnings,
        "_fetch_finnhub_earnings",
        failed_finnhub,
    )

    payload = asyncio.run(
        earnings._build_upcoming_earnings(date(2026, 7, 23))
    )

    assert payload["providers"] == ["Yahoo Finance"]
    assert payload["data_limited"] is True
    assert payload["source_status"] == "degraded"


def test_recent_reported_earnings_remain_visible_after_calendar_rolls_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReportedTicker:
        calendar = {
            "Earnings Date": [date(2026, 10, 28)],
            "Earnings Average": [3.10],
        }
        info = {
            "shortName": "Alphabet Inc.",
            "marketCap": 3_000_000_000_000,
            "sector": "Communication Services",
        }

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame(
                {
                    "EPS Estimate": [2.95, 3.10],
                    "Reported EPS": [3.22, None],
                },
                index=pd.DatetimeIndex(["2026-07-23", "2026-10-28"]),
            )

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["GOOGL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: ReportedTicker())

    payload = asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 23)))

    row = payload["earnings"][0]
    assert row["ticker"] == "GOOGL"
    assert row["earnings_date"] == "2026-07-23"
    assert row["days_until"] == 0
    assert row["eps_estimate"] == 2.95
    assert row["eps_actual"] == 3.22
    assert row["earnings_date_source"] == "earnings_dates"
    assert row["estimate_source"] == "earnings_dates"
    assert row["release_status"] == "released"


def test_yahoo_same_day_calendar_reads_reported_eps_from_earnings_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SameDayTicker:
        calendar = {
            "Earnings Date": [date(2026, 7, 23)],
            "Earnings Average": [2.95],
        }
        info = {
            "shortName": "Alphabet Inc.",
            "marketCap": 3_000_000_000_000,
            "sector": "Communication Services",
        }

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame(
                {
                    "EPS Estimate": [2.95],
                    "Reported EPS": [3.22],
                },
                index=pd.DatetimeIndex(["2026-07-23"]),
            )

    async def unavailable_finnhub(_today):
        return earnings._finnhub_fetch_result(
            configured=False,
            succeeded=False,
            error="not_configured",
        )

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["GOOGL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: SameDayTicker())
    monkeypatch.setattr(
        earnings,
        "_fetch_finnhub_earnings",
        unavailable_finnhub,
    )

    payload = asyncio.run(
        earnings._build_upcoming_earnings(date(2026, 7, 23))
    )

    row = payload["earnings"][0]
    assert row["earnings_date"] == "2026-07-23"
    assert row["days_until"] == 0
    assert row["eps_estimate"] == 2.95
    assert row["eps_actual"] == 3.22
    assert row["release_status"] == "released"
    assert row["earnings_date_source"] == "earnings_dates"
    assert row["estimate_source"] == "calendar"
    assert row["actual_source"] == "earnings_dates"


def test_yahoo_recent_report_waiting_for_actual_does_not_jump_to_next_quarter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedActualTicker:
        calendar = {"Earnings Date": [date(2026, 10, 28)]}
        info = {
            "shortName": "Alphabet Inc.",
            "marketCap": 3_000_000_000_000,
            "sector": "Communication Services",
        }

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame(
                {
                    "EPS Estimate": [2.95, 3.10],
                    "Reported EPS": [None, None],
                },
                index=pd.DatetimeIndex(["2026-07-22", "2026-10-28"]),
            )

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["GOOGL"])
    monkeypatch.setattr(
        earnings.yf,
        "Ticker",
        lambda _symbol: DelayedActualTicker(),
    )

    payload = asyncio.run(
        earnings._build_upcoming_earnings(date(2026, 7, 23))
    )

    row = payload["earnings"][0]
    assert row["earnings_date"] == "2026-07-22"
    assert row["days_until"] == -1
    assert row["eps_actual"] is None
    assert row["release_status"] == "reported_pending_actual"
    assert row["earnings_date_source"] == "earnings_dates"
    assert payload["providers"] == ["Yahoo Finance"]


def test_finnhub_recent_release_overrides_rolled_forward_yahoo_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FutureTicker:
        calendar = {"Earnings Date": [date(2026, 10, 28)]}
        info = {
            "shortName": "Alphabet Inc.",
            "marketCap": 3_000_000_000_000,
            "sector": "Communication Services",
        }

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame()

    async def finnhub_rows(_today):
        return _finnhub_success([
            {
                "ticker": "GOOGL",
                "earnings_date": "2026-07-22",
                "days_until": -1,
                "timing": "amc",
                "eps_estimate": 2.9753,
                "eps_actual": 9.11,
                "revenue_estimate": 120_361_387_510.0,
                "revenue_actual": 103_617_000_000.0,
                "quarter": 2,
                "year": 2026,
            }
        ])

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["GOOGL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: FutureTicker())
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", finnhub_rows)

    payload = asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 23)))

    row = payload["earnings"][0]
    assert row["earnings_date"] == "2026-07-22"
    assert row["days_until"] == -1
    assert row["timing"] == "amc"
    assert row["eps_estimate"] == 2.9753
    assert row["eps_actual"] == 9.11
    assert row["revenue_actual"] == 103_617_000_000.0
    assert row["earnings_date_source"] == "finnhub_calendar"
    assert row["release_status"] == "released"
    assert row["actual_source"] == "finnhub_calendar"
    assert payload["providers"] == ["Finnhub"]


def test_finnhub_yesterday_without_actual_remains_visible_for_googl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FutureTicker:
        calendar = {"Earnings Date": [date(2026, 10, 28)]}
        info = {
            "shortName": "Alphabet Inc.",
            "marketCap": 3_000_000_000_000,
            "sector": "Communication Services",
        }

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame()

    async def finnhub_rows(_today):
        return _finnhub_success([
            {
                "ticker": "GOOGL",
                "earnings_date": "2026-07-22",
                "days_until": -1,
                "timing": "amc",
                "eps_estimate": 2.9753,
                "eps_actual": None,
                "revenue_estimate": 120_361_387_510.0,
                "revenue_actual": None,
                "quarter": 2,
                "year": 2026,
            },
            {
                "ticker": "GOOGL",
                "earnings_date": "2026-10-28",
                "days_until": 97,
                "timing": "amc",
                "eps_estimate": 3.10,
                "eps_actual": None,
                "revenue_estimate": None,
                "revenue_actual": None,
                "quarter": 3,
                "year": 2026,
            },
        ])

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["GOOGL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: FutureTicker())
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", finnhub_rows)

    payload = asyncio.run(
        earnings._build_upcoming_earnings(date(2026, 7, 23))
    )

    row = payload["earnings"][0]
    assert row["earnings_date"] == "2026-07-22"
    assert row["days_until"] == -1
    assert row["eps_actual"] is None
    assert row["release_status"] == "reported_pending_actual"
    assert row["actual_source"] is None
    assert payload["providers"] == ["Finnhub"]


def test_expected_move_uses_final_finnhub_report_date_and_timing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FutureTicker:
        calendar = {"Earnings Date": [date(2026, 8, 10)]}
        info = {
            "shortName": "Final Date Corp",
            "marketCap": 20_000_000_000,
            "sector": "Technology",
        }

        def get_earnings_dates(self, limit=12):
            return pd.DataFrame()

    async def finnhub_rows(_today):
        return _finnhub_success([
            {
                "ticker": "FINAL",
                "earnings_date": "2026-08-12",
                "days_until": 20,
                "timing": "amc",
                "eps_estimate": 1.5,
                "eps_actual": None,
                "revenue_estimate": 2_000_000_000.0,
                "revenue_actual": None,
                "quarter": 2,
                "year": 2026,
            }
        ])

    observed: list[tuple[str, date, date, str | None]] = []

    def expected_move(
        ticker: str,
        report_date: date,
        today: date,
        timing: str | None,
    ) -> dict[str, object]:
        observed.append((ticker, report_date, today, timing))
        return {
            "expected_move_pct": 6.4,
            "expected_move_expiration": "2026-08-14",
            "expected_move_source": "Yahoo/yfinance options",
            "expected_move_observed_at": "2026-07-23T20:15:00Z",
            "expected_move_source_status": "active",
        }

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["FINAL"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: FutureTicker())
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", finnhub_rows)
    monkeypatch.setattr(earnings, "_expected_move_for_report", expected_move)

    payload = asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 23)))

    row = payload["earnings"][0]
    assert observed == [
        ("FINAL", date(2026, 8, 12), date(2026, 7, 23), "amc")
    ]
    assert row["earnings_date"] == "2026-08-12"
    assert row["expected_move_pct"] == 6.4
    assert row["expected_move_expiration"] == "2026-08-14"
    assert row["expected_move_source_status"] == "active"


def test_explicit_refresh_is_bounded_and_replaces_the_cached_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    calls = 0

    async def build(_today: date):
        nonlocal calls
        calls += 1
        return {
            "earnings": [{"ticker": f"T{calls}"}],
            "source_status": "active",
            "as_of": f"snapshot-{calls}",
        }

    monkeypatch.setattr(earnings, "_market_today", lambda: date(2026, 7, 10))
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", build)
    monkeypatch.setattr(earnings.time, "monotonic", lambda: clock[0])

    initial = asyncio.run(earnings.upcoming_earnings())
    refreshed = asyncio.run(earnings.refresh_upcoming_earnings())
    cooled = asyncio.run(earnings.refresh_upcoming_earnings())

    assert initial["earnings"] == [{"ticker": "T1"}]
    assert refreshed["earnings"] == [{"ticker": "T2"}]
    assert refreshed["refresh_status"] == "refreshed"
    assert cooled["earnings"] == [{"ticker": "T2"}]
    assert cooled["refresh_status"] == "cooldown"
    assert cooled["refresh_retry_after_seconds"] == 60
    assert calls == 2


def test_failed_explicit_refresh_preserves_cached_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def build(_today: date):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("provider down")
        return {
            "earnings": [{"ticker": "SAFE"}],
            "source_status": "active",
            "as_of": "snapshot-1",
        }

    monkeypatch.setattr(earnings, "_market_today", lambda: date(2026, 7, 11))
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", build)
    monkeypatch.setattr(earnings.time, "monotonic", lambda: 200.0)

    initial = asyncio.run(earnings.upcoming_earnings())
    stale = asyncio.run(earnings.refresh_upcoming_earnings())

    assert initial["earnings"] == [{"ticker": "SAFE"}]
    assert stale["earnings"] == [{"ticker": "SAFE"}]
    assert stale["_stale"] is True
    assert stale["source_status"] == "stale"
    assert stale["refresh_status"] == "failed_stale"
    assert stale["refresh_error"] == "provider_refresh_failed"
    assert calls == 2


def test_degraded_explicit_refresh_cannot_replace_complete_same_day_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def build(_today: date):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "earnings": [{"ticker": "COMPLETE"}],
                "data_limited": False,
                "source_status": "active",
                "as_of": "complete-generation",
            }
        return {
            "earnings": [{"ticker": "PARTIAL"}],
            "data_limited": True,
            "source_status": "degraded",
            "as_of": "partial-generation",
        }

    market_date = date(2026, 7, 23)
    key = f"earnings:upcoming:{market_date.isoformat()}"
    monkeypatch.setattr(earnings, "_market_today", lambda: market_date)
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", build)
    monkeypatch.setattr(earnings.time, "monotonic", lambda: 300.0)

    initial = asyncio.run(earnings.upcoming_earnings())
    stale = asyncio.run(earnings.refresh_upcoming_earnings())
    persisted = earnings.cache.get(key)

    assert initial["earnings"] == [{"ticker": "COMPLETE"}]
    assert stale["earnings"] == [{"ticker": "COMPLETE"}]
    assert stale["data_limited"] is False
    assert stale["_stale"] is True
    assert stale["source_status"] == "stale"
    assert stale["refresh_status"] == "failed_stale"
    assert stale["refresh_error"] == "provider_refresh_incomplete"
    assert persisted["earnings"] == [{"ticker": "COMPLETE"}]
    assert persisted["source_status"] == "active"
    assert "refresh_status" not in persisted
    assert calls == 2
