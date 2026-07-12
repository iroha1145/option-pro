from __future__ import annotations

import pandas as pd


def history(
    size: int = 260,
    *,
    start: float = 100.0,
    step: float = 0.25,
) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=size)
    close = pd.Series(
        [start + position * step for position in range(size)],
        index=index,
        dtype=float,
    )
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


def core_market() -> dict[str, pd.DataFrame]:
    return {
        "SPY": history(start=100.0, step=.30),
        "QQQ": history(start=95.0, step=.34),
        "RSP": history(start=90.0, step=.28),
        "IWM": history(start=85.0, step=.24),
    }


def credit_market() -> dict[str, pd.DataFrame]:
    return {
        "HYG": history(start=75.0, step=.08),
        "IEF": history(start=92.0, step=.03),
        "TLT": history(start=88.0, step=.02),
    }
