"""Midrank math; callers retain their own filtering and rounding policies."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from typing import Any


def midrank_percentile(values: Sequence[Any], value: Any) -> float:
    """Rank against sorted observations after the caller checks sample size."""
    below = bisect_left(values, value)
    tied = bisect_right(values, value) - below
    midrank = below + (tied - 1) / 2
    return round(midrank / max(len(values) - 1, 1) * 100, 1)
