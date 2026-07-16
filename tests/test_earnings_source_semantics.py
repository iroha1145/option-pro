from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd
import pytest

from app.api import earnings


@pytest.fixture(autouse=True)
def _clear_earnings_state() -> None:
    earnings.cache.clear()
    earnings._refresh_deadlines.clear()
    yield
    earnings.cache.clear()
    earnings._refresh_deadlines.clear()


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

    payload = asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 10)))

    row = payload["earnings"][0]
    assert row["earnings_date"] == "2026-07-20"
    assert row["eps_estimate"] == 1.25
    assert row["revenue_estimate"] == 12_000_000_000
    assert row["earnings_date_source"] == "calendar"
    assert row["estimate_source"] == "calendar"
    assert row["source_status"] == "active"
    assert row["observed_at"]


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
