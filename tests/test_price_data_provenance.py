from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.services.breakouts.adapters import price_data
from app.services.breakouts.adapters.price_data import YahooPriceDataAdapter
from app.services.breakouts.models import MarketSession, TemporalCutoff
from app.services.breakouts.protocols import PriceDataSnapshot
from app.services import massive
from app.services.strength import scanner


NY = ZoneInfo("America/New_York")


def test_raw_as_of_is_always_the_data_through_compatibility_alias() -> None:
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )
    actual = datetime(2026, 7, 10, 14, 20, tzinfo=timezone.utc)
    snapshot = PriceDataSnapshot(
        ticker="AAPL",
        frame=pd.DataFrame(),
        source="fixture",
        raw_as_of=cutoff.event_at.astimezone(timezone.utc),
        cutoff=cutoff,
        session=cutoff.session,
        adjustment="unadjusted",
        completeness="stale_complete_bars",
        data_through=actual,
    )

    assert snapshot.raw_as_of == actual
    assert snapshot.data_through == actual
    assert snapshot.feature_cutoff_at == cutoff.event_at


def test_snapshot_rejects_market_data_after_feature_cutoff() -> None:
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )
    with pytest.raises(ValueError, match="data_through"):
        PriceDataSnapshot(
            ticker="AAPL",
            frame=pd.DataFrame(),
            source="fixture",
            raw_as_of=cutoff.event_at.astimezone(timezone.utc),
            cutoff=cutoff,
            session=cutoff.session,
            adjustment="unadjusted",
            completeness="invalid",
            data_through=cutoff.event_at.astimezone(timezone.utc)
            + pd.Timedelta(minutes=5),
        )


def test_daily_adapter_records_request_receive_source_and_actual_market_close(
    monkeypatch,
) -> None:
    frame = pd.DataFrame(
        {"Close": [100.0, 101.0], "Volume": [1_000.0, 2_000.0]},
        index=pd.DatetimeIndex(["2026-11-25", "2026-11-27"]),
    )
    frame.attrs["price_source"] = {
        "provider": "fixture-daily",
        "status": "active",
    }
    monkeypatch.setattr(scanner, "_download_history", lambda *_args: frame)
    requested = datetime(2026, 11, 27, 18, 5, tzinfo=timezone.utc)
    received = datetime(2026, 11, 27, 18, 5, 1, tzinfo=timezone.utc)
    times = iter((requested, received))
    monkeypatch.setattr(price_data, "_utc_now", lambda: next(times))
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 11, 27, 14, 0, tzinfo=NY),
        session=MarketSession.POSTMARKET,
    )

    snapshot = asyncio.run(
        YahooPriceDataAdapter().daily(["AAPL"], cutoff=cutoff, period="2y")
    )["AAPL"]

    assert snapshot.requested_at == requested
    assert snapshot.received_at == received
    assert snapshot.data_through == datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    assert snapshot.raw_as_of == snapshot.data_through
    assert snapshot.feature_cutoff_at == cutoff.event_at
    assert snapshot.source == "fixture-daily"
    assert snapshot.adjustment == "auto_adjusted"
    assert snapshot.completeness == "completed_daily_sessions"
    assert snapshot.quality == 1.0


def test_intraday_adapter_labels_massive_when_massive_supplies_the_frame(
    monkeypatch,
) -> None:
    bar_at = datetime(2026, 7, 10, 10, 20, tzinfo=NY)
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )

    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(
        massive,
        "ticker_range",
        lambda *_args, **_kwargs: [
            {
                "t": int(bar_at.timestamp() * 1_000),
                "o": 100.0,
                "h": 102.0,
                "l": 99.0,
                "c": 101.0,
                "v": 1_000.0,
            }
        ],
    )

    def unexpected_yahoo(*_args, **_kwargs):
        raise AssertionError("Yahoo fallback must not run after a Massive success")

    monkeypatch.setattr(
        price_data,
        "download_in_bounded_batches",
        unexpected_yahoo,
    )

    snapshot = asyncio.run(
        YahooPriceDataAdapter().intraday(["AAPL"], cutoff=cutoff, interval="5m")
    )["AAPL"]

    assert snapshot.source == "Massive"
    assert snapshot.data_through == datetime(
        2026,
        7,
        10,
        14,
        25,
        tzinfo=timezone.utc,
    )


def test_intraday_adapter_labels_yahoo_after_massive_failure(monkeypatch) -> None:
    bar_at = datetime(2026, 7, 10, 10, 20, tzinfo=NY)
    fallback = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [102.0],
            "Low": [99.0],
            "Close": [101.0],
            "Volume": [1_000.0],
        },
        index=pd.DatetimeIndex([bar_at]),
    )
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )

    def fail_massive(*_args, **_kwargs):
        raise massive.MassiveError("plan", code="plan", status=403)

    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "ticker_range", fail_massive)
    monkeypatch.setattr(
        price_data,
        "download_in_bounded_batches",
        lambda *_args, **_kwargs: fallback,
    )

    snapshot = asyncio.run(
        YahooPriceDataAdapter().intraday(["AAPL"], cutoff=cutoff, interval="5m")
    )["AAPL"]

    assert snapshot.source == "Yahoo/yfinance"
