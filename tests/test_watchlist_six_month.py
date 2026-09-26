"""Six-month weekly trend attached to watchlist rows (trend_6m)."""

import asyncio
from datetime import date, timedelta

import pandas as pd

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

    def fetch(symbols):
        calls.append(list(symbols))
        return {symbol: [(date(2026, 9, 18), 10.0), (date(2026, 9, 25), 11.0)] for symbol in symbols}

    first = six_month.cached_weekly_history(["AAA", "BBB", "CCC"], fetch, now=0.0, budget=2)
    assert calls == [["AAA", "BBB"]]
    assert set(first) == {"AAA", "BBB"}
    second = six_month.cached_weekly_history(["AAA", "BBB", "CCC"], fetch, now=60.0, budget=2)
    assert calls[-1] == ["CCC"]
    assert set(second) == {"AAA", "BBB", "CCC"}
    six_month.cached_weekly_history(["AAA"], fetch, now=120.0)
    assert len(calls) == 2, "fresh history must not be requested again"
    six_month.cached_weekly_history(["AAA"], fetch, now=six_month.HISTORY_TTL_SECONDS + 1)
    assert calls[-1] == ["AAA"]


def test_failed_refresh_keeps_previous_history_and_backs_off():
    good = {"AAA": [(date(2026, 9, 18), 10.0), (date(2026, 9, 25), 11.0)]}
    six_month.cached_weekly_history(["AAA"], lambda _symbols: good, now=0.0)

    def broken(_symbols):
        raise RuntimeError("provider down")

    later = six_month.HISTORY_TTL_SECONDS + 1
    kept = six_month.cached_weekly_history(["AAA"], broken, now=later)
    assert kept["AAA"] == six_month.weekly_closes(good["AAA"])
    calls = []
    six_month.cached_weekly_history(["AAA"], lambda symbols: calls.append(symbols) or {}, now=later + 60)
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
