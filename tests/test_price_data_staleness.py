from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.adapters import price_data
from app.services.breakouts.adapters.price_data import YahooPriceDataAdapter
from app.services.breakouts.feature_engine import compute_feature_snapshot
from app.services.breakouts.models import MarketSession, TemporalCutoff


NY = ZoneInfo("America/New_York")


def test_intraday_snapshot_marks_missing_complete_bars_stale(monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [1_000.0, 1_000.0],
        },
        index=pd.DatetimeIndex(
            [
                datetime(2026, 7, 10, 10, 10, tzinfo=NY),
                datetime(2026, 7, 10, 10, 15, tzinfo=NY),
            ]
        ),
    )
    monkeypatch.setattr(price_data.yf, "download", lambda **_kwargs: frame)
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )

    snapshot = asyncio.run(
        YahooPriceDataAdapter().intraday(["AAPL"], cutoff=cutoff, interval="5m")
    )["AAPL"]

    assert snapshot.data_through.astimezone(NY).strftime("%H:%M") == "10:20"
    assert snapshot.feature_cutoff_at.astimezone(NY).strftime("%H:%M") == "10:30"
    assert snapshot.completeness == "stale_complete_bars"
    assert "data_through_before_feature_cutoff" in snapshot.warnings
    assert 0 < snapshot.quality < 1


def test_intraday_adapter_preserves_later_close_volume_bar_without_high_low(
    monkeypatch,
) -> None:
    frame = pd.DataFrame(
        {
            "Open": [10.0, float("nan")],
            "High": [11.0, float("nan")],
            "Low": [9.0, float("nan")],
            "Close": [10.0, 20.0],
            "Volume": [100.0, 200.0],
        },
        index=pd.DatetimeIndex(
            [
                datetime(2026, 7, 10, 10, 10, tzinfo=NY),
                datetime(2026, 7, 10, 10, 15, tzinfo=NY),
            ]
        ),
    )
    monkeypatch.setattr(price_data.yf, "download", lambda **_kwargs: frame)
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )

    snapshot = asyncio.run(
        YahooPriceDataAdapter().intraday(["AAPL"], cutoff=cutoff, interval="5m")
    )["AAPL"]
    daily = pd.DataFrame(
        {
            "Open": [9.0],
            "High": [11.0],
            "Low": [8.0],
            "Close": [10.0],
            "Volume": [1_000.0],
        },
        index=pd.DatetimeIndex(["2026-07-09"]),
    )
    features = compute_feature_snapshot(
        daily=daily,
        intraday=snapshot.frame,
        cutoff=cutoff,
    )

    assert len(snapshot.frame) == 2
    assert snapshot.data_through.astimezone(NY).strftime("%H:%M") == "10:20"
    assert features["cumulative_dollar_volume"] == 5_000.0
    assert features["data_through"] == "2026-07-10T14:20:00+00:00"
