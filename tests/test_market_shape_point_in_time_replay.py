from datetime import datetime, timezone

import pandas as pd

from app.services.strength.market_regime import (
    _daily_replay_as_of,
    compute_market_regime,
)
from app.services.strength.market_shape import build_market_shape

from market_shape_support import snapshot


def test_future_snapshots_do_not_change_original_as_of_replay() -> None:
    history = [snapshot("bull", day) for day in range(4)]
    current = snapshot("distribution", 4)
    baseline = build_market_shape(
        current["regime"],
        as_of=current["as_of"],
        history=history,
    )
    with_future = build_market_shape(
        current["regime"],
        as_of=current["as_of"],
        history=[
            *history,
            snapshot("bear", 5),
            snapshot("bear", 6),
        ],
    )
    for key in (
        "state",
        "raw_state",
        "entered_at",
        "days_in_state",
        "pending_state",
        "pending_days",
        "transition_risk",
    ):
        assert with_future[key] == baseline[key]


def test_daily_replay_uses_actual_early_close() -> None:
    observed = _daily_replay_as_of(
        pd.Timestamp("2026-11-27"),
        datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc),
        0,
    )

    assert observed == datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)


def test_weekend_request_keeps_shape_time_on_last_completed_session() -> None:
    index = pd.bdate_range(end="2026-07-10", periods=260)

    def rising(start: float, step: float) -> pd.DataFrame:
        close = pd.Series(
            [start + position * step for position in range(len(index))],
            index=index,
            dtype=float,
        )
        return pd.DataFrame(
            {
                "Open": close - 0.2,
                "High": close + 0.8,
                "Low": close - 0.8,
                "Close": close,
                "Volume": 1_000_000.0,
            },
            index=index,
        )

    result = compute_market_regime(
        {
            "SPY": rising(100.0, .30),
            "QQQ": rising(95.0, .34),
            "RSP": rising(90.0, .28),
            "IWM": rising(85.0, .24),
        },
        as_of=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
    )

    shape = result["market_shape"]
    assert shape["as_of"] == "2026-07-10T20:00:00+00:00"
    assert shape["entered_at"].startswith("2026-")
    assert not shape["entered_at"].startswith("2026-07-12")
    assert shape["history_truncated"] is True
    assert shape["days_in_state_is_lower_bound"] is True
    assert shape["state_age_semantics"] == "lower_bound_from_replay_window"


def test_premarket_request_excludes_same_day_daily_bar() -> None:
    index = pd.bdate_range(end="2026-07-13", periods=260)

    def rising(start: float, step: float) -> pd.DataFrame:
        close = pd.Series(
            [start + position * step for position in range(len(index))],
            index=index,
            dtype=float,
        )
        return pd.DataFrame(
            {
                "Open": close - 0.2,
                "High": close + 0.8,
                "Low": close - 0.8,
                "Close": close,
                "Volume": 1_000_000.0,
            },
            index=index,
        )

    result = compute_market_regime(
        {
            "SPY": rising(100.0, .30),
            "QQQ": rising(95.0, .34),
            "RSP": rising(90.0, .28),
            "IWM": rising(85.0, .24),
        },
        as_of=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )

    assert result["market_shape"]["as_of"] == "2026-07-10T20:00:00+00:00"
