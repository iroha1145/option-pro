"""Preregistered circular date-block bootstrap. Block length is not chosen after seeing IC."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

BOOTSTRAP_SEED = 174
BOOTSTRAP_REPEATS = 2000
BOOTSTRAP_METHOD = "CIRCULAR_DATE_BLOCK_BOOTSTRAP"
STATISTICS_NOTE = (
    "Year/two-year tables are descriptive. They are not this block bootstrap interval."
)


def preregistered_block_lengths(label_horizon: int) -> dict[str, int]:
    horizon = int(label_horizon)
    return {"H": horizon, "2H": 2 * horizon}


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def normal_approx_ci(values: Sequence[float]) -> dict[str, Any]:
    """Naive i.i.d. interval. Do not use this on overlapping or copied days."""

    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "ci95": None, "method": "PREREGISTERED_NORMAL_APPROX", "reason": "EMPTY"}
    mean = float(sum(values) / n)
    if n < 5:
        return {
            "n": n,
            "mean": mean,
            "ci95": None,
            "method": "PREREGISTERED_NORMAL_APPROX",
            "reason": "BLOCK_TOO_SHORT",
        }
    var = sum((item - mean) ** 2 for item in values) / (n - 1)
    half = 1.96 * (var ** 0.5) / (n ** 0.5)
    return {
        "n": n,
        "mean": mean,
        "ci95": [mean - half, mean + half],
        "method": "PREREGISTERED_NORMAL_APPROX",
        "reason": None,
    }


def circular_block_bootstrap(
    aligned_values: Sequence[float | None],
    *,
    block_len: int,
    n_boot: int = BOOTSTRAP_REPEATS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Resample contiguous blocks on the full timeline. Missing stays missing.

    Dropping missing dates and treating leftover points as adjacent is forbidden.
    Completely correlated copies of 20 blocks must not be treated as 400 i.i.d. days.
    A truly independent series is not widened just to look conservative.
    """

    values = list(aligned_values)
    n = len(values)
    observed = [float(item) for item in values if item is not None]
    mean = _mean(observed)
    if n == 0 or not observed:
        return {
            "n_timeline": n,
            "n_observed": 0,
            "n_blocks": 0,
            "block_len": int(block_len),
            "mean": None,
            "ci95": None,
            "method": BOOTSTRAP_METHOD,
            "seed": seed,
            "repeats": n_boot,
            "reason": "EMPTY",
        }
    length = int(block_len)
    if length < 1:
        raise ValueError("block_len must be >= 1")
    n_blocks = int(np.ceil(n / length))
    if n_blocks < 5:
        return {
            "n_timeline": n,
            "n_observed": len(observed),
            "n_blocks": n_blocks,
            "block_len": length,
            "mean": mean,
            "ci95": None,
            "method": BOOTSTRAP_METHOD,
            "seed": seed,
            "repeats": n_boot,
            "reason": "INSUFFICIENT_BLOCKS",
        }
    rng = np.random.default_rng(int(seed))
    arr = np.array([np.nan if item is None else float(item) for item in values], dtype=float)
    starts = rng.integers(0, n, size=(int(n_boot), n_blocks))
    offsets = np.arange(length)
    idx = (starts[..., None] + offsets) % n
    sampled = arr[idx].reshape(int(n_boot), -1)
    with np.errstate(all="ignore"):
        means = np.nanmean(sampled, axis=1)
    means = [float(item) for item in means if np.isfinite(item)]
    if len(means) < 20:
        return {
            "n_timeline": n,
            "n_observed": len(observed),
            "n_blocks": n_blocks,
            "block_len": length,
            "mean": mean,
            "ci95": None,
            "method": BOOTSTRAP_METHOD,
            "seed": seed,
            "repeats": n_boot,
            "reason": "INSUFFICIENT_BOOTSTRAP_MEANS",
        }
    lo, hi = np.quantile(np.asarray(means, dtype=float), [0.025, 0.975])
    return {
        "n_timeline": n,
        "n_observed": len(observed),
        "n_blocks": n_blocks,
        "block_len": length,
        "mean": mean,
        "ci95": [float(lo), float(hi)],
        "method": BOOTSTRAP_METHOD,
        "seed": seed,
        "repeats": n_boot,
        "reason": None,
        "note": STATISTICS_NOTE,
    }


def paired_diff_intervals(
    aligned_diffs: Sequence[float | None],
    *,
    label_horizon: int,
    n_boot: int = BOOTSTRAP_REPEATS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    lengths = preregistered_block_lengths(label_horizon)
    return {
        "H": circular_block_bootstrap(aligned_diffs, block_len=lengths["H"], n_boot=n_boot, seed=seed),
        "2H": circular_block_bootstrap(aligned_diffs, block_len=lengths["2H"], n_boot=n_boot, seed=seed),
        "descriptive_normal_on_observed_only": normal_approx_ci(
            [float(item) for item in aligned_diffs if item is not None]
        ),
        "block_lengths_preregistered": lengths,
        "seed": seed,
        "repeats": n_boot,
    }
