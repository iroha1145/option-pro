"""Six-month weekly trend attached to watchlist rows (trend_6m)."""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
import time

import pandas as pd
import pytest

from app.api import stocks
from app.services import massive
from app.services import watchlist_six_month as six_month
from app.services.strength import scanner


def _weekdays(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_weekly_closes_keep_last_usable_close_of_each_iso_week():
    daily = [
        (date(2026, 9, 14), 100.0),  # Mon
        (date(2026, 9, 18), 104.0),  # Fri — the week's close
        (date(2026, 9, 21), 101.0),
        (date(2026, 9, 24), 103.0),
        (date(2026, 9, 25), float("nan")),  # unusable, Thursday stays the close
    ]
    assert six_month.weekly_closes(daily) == (
        (date(2026, 9, 18), 104.0),
        (date(2026, 9, 24), 103.0),
    )


def test_weekly_closes_cap_at_twenty_seven_weeks():
    daily = [(day, float(index + 1)) for index, day in enumerate(_weekdays(date(2025, 9, 1), date(2026, 9, 25)))]
    weekly = six_month.weekly_closes(daily)
    assert len(weekly) == six_month.TREND_MAX_POINTS == 27
    assert weekly[-1] == (date(2026, 9, 25), float(len(daily)))


def test_compose_trend_puts_the_live_quote_in_the_current_week():
    weekly = ((date(2026, 9, 11), 90.0), (date(2026, 9, 18), 95.0), (date(2026, 9, 23), 97.0))
    same_week = six_month.compose_trend(weekly, price=99.5, quote_date=date(2026, 9, 25))
    assert same_week["points"][-1] == {"date": "2026-09-25", "close": 99.5}
    assert len(same_week["points"]) == 3
    new_week = six_month.compose_trend(weekly, price=101.0, quote_date=date(2026, 9, 28))
    assert new_week["points"][-2] == {"date": "2026-09-23", "close": 97.0}
    assert new_week["points"][-1] == {"date": "2026-09-28", "close": 101.0}
    assert (new_week["range"], new_week["interval"], new_week["adjustment"]) == ("6mo", "1wk", "split")


def test_compose_trend_needs_history_and_a_usable_price():
    assert six_month.compose_trend((), price=10.0, quote_date=date(2026, 9, 25)) is None
    weekly = ((date(2026, 9, 21), 90.0),)
    assert six_month.compose_trend(weekly, price=float("nan"), quote_date=date(2026, 9, 25)) is None
    # One week of history plus the same-week quote is still a single point.
    assert six_month.compose_trend(weekly, price=91.0, quote_date=date(2026, 9, 25)) is None


@pytest.mark.parametrize("quote_date", [date(2026, 9, 24), date(2026, 9, 18)])
def test_compose_trend_omits_history_newer_than_the_quote(quote_date):
    weekly = ((date(2026, 9, 18), 90.0), (date(2026, 9, 25), 110.0))
    assert six_month.compose_trend(weekly, price=100.0, quote_date=quote_date) is None


@pytest.mark.parametrize(
    "ticker,old_at,new_at,last_day,expected_day",
    [
        ("AAPL", "2026-09-25T14:00:00Z", "2026-09-25T15:00:00Z", "2026-09-25", "2026-09-25"),
        # Crossing UTC midnight does not change Tokyo's trading date.
        ("7203.T", "2026-09-24T23:59:00Z", "2026-09-25T00:05:00Z", "2026-09-25", "2026-09-25"),
        # Sunday evening in Chicago is the Monday futures session.
        ("ES=F", "2026-09-27T23:00:00Z", "2026-09-27T23:05:00Z", "2026-09-28", "2026-09-28"),
        # A previous session's history may predate a split; wait for its refresh.
        ("AAPL", "2026-09-25T14:00:00Z", "2026-09-28T14:00:00Z", "2026-09-25", None),
        ("AAPL", None, None, "2026-09-25", None),
    ],
)
def test_personal_quote_merge_realigns_only_same_session_trend(
    monkeypatch, ticker, old_at, new_at, last_day, expected_day,
):
    now = time.time()
    old_trend = six_month.compose_trend(
        ((date(2026, 9, 18), 90.0), (date.fromisoformat(last_day), 100.0)),
        price=100.0,
        quote_date=date.fromisoformat(last_day),
    )
    old_row = {
        "ticker": ticker, "price": 100.0, "quote_as_of": old_at,
        "spark": [99.0, 100.0], "trend_6m": old_trend,
    }
    entry = SimpleNamespace(
        value={"groups": [{"name": "", "stocks": [old_row]}]},
        fetched_at=now - 60, expires_at=now + 60,
    )
    monkeypatch.setattr(stocks, "_read_watchlist_snapshot", lambda *args, **kwargs: entry)
    monkeypatch.setattr(stocks, "_unexpired_hit", lambda *args: None)
    monkeypatch.setattr(stocks, "read_stock_pull_resource", lambda *args, **kwargs: {
        "payload": {"price": 120.0, "quote_as_of": new_at, "change": 20.0, "change_percent": 20.0},
        "saved_at": now,
    })

    def forbidden(*args, **kwargs):
        raise AssertionError("personal quote merge must not request history")

    monkeypatch.setattr(six_month, "fetch_six_month_daily", forbidden)
    monkeypatch.setattr(stocks.yf, "download", forbidden)
    row = stocks._cached_selected_watchlist([ticker])["groups"][0]["stocks"][0]
    assert row["price"] == 120.0
    assert row["spark"] == [99.0, 100.0]
    assert old_trend["points"][-1]["close"] == 100.0, "shared snapshot must remain unchanged"
    if expected_day is None:
        assert "trend_6m" not in row
    else:
        assert row["trend_6m"]["points"][-1] == {"date": expected_day, "close": 120.0}


def test_valid_trend_accepts_composed_output_and_rejects_malformed_series():
    weekly = ((date(2026, 9, 11), 90.0), (date(2026, 9, 18), 95.0))
    trend = six_month.compose_trend(weekly, price=96.0, quote_date=date(2026, 9, 25))
    assert six_month.valid_trend(trend)
    assert not six_month.valid_trend({**trend, "interval": "1d"})
    assert not six_month.valid_trend({**trend, "points": list(reversed(trend["points"]))})
    assert not six_month.valid_trend({**trend, "points": [*trend["points"][:-1], {"date": "2026-09-25", "close": 0}]})
    assert not six_month.valid_trend({**trend, "points": [{"date": "2026-9-1", "close": 1.0}, trend["points"][-1]]})
    too_many = [{"date": (date(2026, 1, 1) + timedelta(days=7 * index)).isoformat(), "close": 1.0} for index in range(28)]
    assert not six_month.valid_trend({**trend, "points": too_many})


def test_cached_history_fetches_only_stale_symbols_within_budget():
    calls = []
    quote_dates = {symbol: date(2026, 9, 25) for symbol in ("AAA", "BBB", "CCC")}

    def fetch(symbols):
        calls.append(list(symbols))
        return {symbol: [(date(2026, 9, 18), 10.0), (date(2026, 9, 25), 11.0)] for symbol in symbols}

    first = six_month.cached_weekly_history(["AAA", "BBB", "CCC"], fetch, quote_dates=quote_dates, now=0.0, budget=2)
    assert calls == [["AAA", "BBB"]]
    assert set(first) == {"AAA", "BBB"}
    second = six_month.cached_weekly_history(["AAA", "BBB", "CCC"], fetch, quote_dates=quote_dates, now=60.0, budget=2)
    assert calls[-1] == ["CCC"]
    assert set(second) == {"AAA", "BBB", "CCC"}
    six_month.cached_weekly_history(["AAA"], fetch, quote_dates=quote_dates, now=120.0)
    assert len(calls) == 2, "fresh history must not be requested again"
    six_month.cached_weekly_history(["AAA"], fetch, quote_dates=quote_dates, now=six_month.HISTORY_TTL_SECONDS + 1)
    assert calls[-1] == ["AAA"]


def test_failed_refresh_keeps_previous_history_and_backs_off():
    good = {"AAA": [(date(2026, 9, 18), 10.0), (date(2026, 9, 25), 11.0)]}
    quote_dates = {"AAA": date(2026, 9, 25)}
    six_month.cached_weekly_history(["AAA"], lambda _symbols: good, quote_dates=quote_dates, now=0.0)

    def broken(_symbols):
        raise RuntimeError("provider down")

    later = six_month.HISTORY_TTL_SECONDS + 1
    kept = six_month.cached_weekly_history(["AAA"], broken, quote_dates=quote_dates, now=later)
    assert kept["AAA"] == six_month.weekly_closes(good["AAA"])
    calls = []
    six_month.cached_weekly_history(["AAA"], lambda symbols: calls.append(symbols) or {}, quote_dates=quote_dates, now=later + 60)
    assert calls == [], "a failed symbol waits out the short retry window"


def test_fetch_prefers_massive_then_asks_yahoo_for_the_rest(monkeypatch):
    massive_frame = pd.DataFrame(
        {("AAA", "Close"): [50.0, 51.0]},
        index=pd.to_datetime(["2026-09-24", "2026-09-25"]),
    )
    massive_frame.columns = pd.MultiIndex.from_tuples(massive_frame.columns)
    monkeypatch.setattr(scanner, "_download_massive_history", lambda tickers, period: (massive_frame, ["^N225"]))
    captured = {}

    def download(**kwargs):
        captured.update(kwargs)
        # Batched Yahoo frames arrive normalised to UTC: Tokyo's Friday session
        # (00:00 JST) is Thursday 15:00 UTC.
        return pd.DataFrame(
            {"Close": [38_000.0]},
            index=pd.DatetimeIndex(["2026-09-24 15:00"], tz="UTC"),
        )

    closes = six_month.fetch_six_month_daily(
        ["AAA", "^N225"],
        download=download,
        market_timezone=stocks._watchlist_market_timezone,
    )
    assert closes["AAA"] == [(date(2026, 9, 24), 50.0), (date(2026, 9, 25), 51.0)]
    assert closes["^N225"] == [(date(2026, 9, 25), 38_000.0)]
    assert captured["tickers"] == "^N225"  # 分批下载器按空格拼接代码
    assert (captured["period"], captured["interval"], captured["auto_adjust"]) == ("6mo", "1d", False)


def test_built_watchlist_rows_carry_a_six_month_trend_that_survives_the_snapshot(monkeypatch):
    sessions = _weekdays(date(2026, 3, 23), date(2026, 9, 24))

    def download(*, interval, period, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [131.0]},
                index=pd.DatetimeIndex(["2026-09-25 15:55"], tz="America/New_York"),
            )
        days = sessions[-7:] if period == "7d" else sessions
        return pd.DataFrame(
            {"Close": [100.0 + index * 0.25 for index in range(len(days))]},
            index=pd.to_datetime([day.isoformat() for day in days]),
        )

    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "download", download)
    payload = asyncio.run(stocks._build_watchlist(["AAPL"]))
    row = payload["groups"][0]["stocks"][0]
    trend = row["trend_6m"]
    assert len(row["spark"]) <= 7, "the short spark keeps its seven-point contract"
    assert trend["points"][-1] == {"date": "2026-09-25", "close": 131.0}
    assert len(trend["points"]) == six_month.TREND_MAX_POINTS
    assert trend["points"][0]["date"] > "2026-03-23"
    assert stocks._clean_watchlist_snapshot_payload(payload) is not None

    broken = {**payload, "groups": [{**payload["groups"][0], "stocks": [{**row, "trend_6m": {**trend, "points": trend["points"][::-1]}}]}]}
    assert stocks._clean_watchlist_snapshot_payload(broken) is None


def test_trend_failure_never_costs_the_quote(monkeypatch):
    def download(*, interval, period, **_kwargs):
        if period == "6mo":
            raise RuntimeError("history endpoint down")
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [105.0]},
                index=pd.DatetimeIndex(["2026-09-25 15:55"], tz="America/New_York"),
            )
        return pd.DataFrame({"Close": [98.0, 100.0]}, index=pd.to_datetime(["2026-09-23", "2026-09-24"]))

    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "download", download)
    row = asyncio.run(stocks._build_watchlist(["AAPL"]))["groups"][0]["stocks"][0]
    assert row["price"] == 105.0
    assert "trend_6m" not in row


def test_sunday_futures_quote_appends_to_cached_friday_history(monkeypatch):
    # Fetched during Sunday's Monday session, before the Monday daily bar exists.
    six_month.cached_weekly_history(
        ["ES=F"],
        lambda symbols: {"ES=F": [(date(2026, 9, 18), 90.0), (date(2026, 9, 25), 100.0)]},
        quote_dates={"ES=F": date(2026, 9, 28)},
    )

    def download(*, interval, period, **kwargs):
        assert period != "6mo", "fresh history must be reused"
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [105.0]},
                index=pd.DatetimeIndex(["2026-09-27 18:00"], tz="America/Chicago"),
            )
        return pd.DataFrame({"Close": [99.0, 100.0]}, index=pd.to_datetime(["2026-09-24", "2026-09-25"]))

    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "download", download)
    monkeypatch.setattr(stocks, "_fetch_watchlist_provider_previous_closes", lambda *args, **kwargs: {"ES=F": 100.0})
    row = asyncio.run(stocks._build_watchlist(["ES=F"]))["groups"][0]["stocks"][0]
    assert row["price"] == 105.0
    assert row["trend_6m"]["points"] == [
        {"date": "2026-09-18", "close": 90.0},
        {"date": "2026-09-25", "close": 100.0},
        {"date": "2026-09-28", "close": 105.0},
    ]


def test_history_ahead_of_quote_only_omits_the_optional_trend(monkeypatch):
    def download(*, interval, period, **kwargs):
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [105.0]},
                index=pd.DatetimeIndex(["2026-09-24 15:55"], tz="America/New_York"),
            )
        days = ["2026-09-18", "2026-09-25"] if period == "6mo" else ["2026-09-23", "2026-09-24"]
        return pd.DataFrame({"Close": [98.0, 110.0]}, index=pd.to_datetime(days))

    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "download", download)
    row = asyncio.run(stocks._build_watchlist(["AAPL"]))["groups"][0]["stocks"][0]
    assert row["price"] == 105.0
    assert row["quote_as_of"] == "2026-09-24T19:55:00+00:00"
    assert row["spark"] == [98.0, 105.0]
    assert "trend_6m" not in row


def test_history_cache_waits_for_successful_refresh_after_quote_session_changes(monkeypatch):
    state = {
        "clock": 0.0, "day": date(2026, 9, 24), "price": 100.0,
        "history_fails": False, "history_calls": 0,
    }
    monkeypatch.setattr(six_month, "time", SimpleNamespace(monotonic=lambda: state["clock"]))
    monkeypatch.setattr(massive, "configured", lambda: False)

    def download(*, interval, period, **kwargs):
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [state["price"]]},
                index=pd.DatetimeIndex([f'{state["day"]} 04:00'], tz="America/New_York"),
            )
        if period == "6mo":
            state["history_calls"] += 1
            if state["history_fails"]:
                raise RuntimeError("history unavailable")
            days = [date(2026, 9, 18), state["day"] - timedelta(days=1)]
        else:
            days = [state["day"] - timedelta(days=2), state["day"] - timedelta(days=1)]
        return pd.DataFrame(
            {"Close": [state["price"], state["price"]]}, index=pd.to_datetime(days),
        )

    monkeypatch.setattr(stocks.yf, "download", download)

    def build():
        return asyncio.run(stocks._build_watchlist(["AAPL"]))["groups"][0]["stocks"][0]

    # A fresh fetch in today's quote session may contain only yesterday's bars.
    initial = build()
    assert initial["trend_6m"]["points"][-1] == {"date": "2026-09-24", "close": 100.0}
    state["clock"] = 60.0
    assert build()["trend_6m"] == initial["trend_6m"]
    assert state["history_calls"] == 1, "same-session history is reused"

    # A new split-adjusted quote must not meet the old history basis. Moving
    # sessions alone must not add a history request before the existing TTL.
    state.update(clock=5 * 60 * 60, day=date(2026, 9, 25), price=50.0)
    split_row = build()
    assert split_row["price"] == 50.0 and split_row["change_percent"] == 0.0
    assert "trend_6m" not in split_row
    assert state["history_calls"] == 1

    # Expired + failed refresh preserves the old session label, and preserves
    # the ordinary retry backoff instead of requesting history each build.
    state.update(clock=six_month.HISTORY_TTL_SECONDS + 1, history_fails=True)
    assert "trend_6m" not in build()
    assert state["history_calls"] == 2
    state["clock"] += 60
    assert "trend_6m" not in build()
    assert state["history_calls"] == 2

    # The normal retry succeeds with the new basis; yesterday's final bar is
    # still valid because acquisition occurred in the current quote session.
    state.update(clock=six_month.HISTORY_TTL_SECONDS + six_month.MISSING_TTL_SECONDS + 2, history_fails=False)
    restored = build()
    assert state["history_calls"] == 3
    assert restored["trend_6m"]["points"] == [
        {"date": "2026-09-18", "close": 50.0},
        {"date": "2026-09-25", "close": 50.0},
    ]
