"""Point-in-time Range Persistence features.

The legacy path intentionally mirrors the Pine script's zero-input behavior
during rolling warmup and flat ranges.  The improved path treats flat ranges as
uninformative and never turns missing information into a neutral score.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Sequence

import numpy as np
import pandas as pd


RANGE_PERSISTENCE_VERSION = "range-persistence-v1"
_VALUE_FIELDS = (
    "range_position",
    "range_persistence_fast",
    "range_persistence_slow",
    "range_persistence",
    "range_persistence_slope_5d",
    "range_persistence_ratio_10d",
    "range_persistence_self_percentile",
    "range_persistence_global_percentile",
    "range_persistence_sector_percentile",
    "range_persistence_global_robust_z",
    "range_persistence_sector_robust_z",
    "range_persistence_sector_shrunk_z",
    "range_persistence_normalized_score",
    "legacy_control",
)


def _finite(value: Any, ndigits: int = 10) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, ndigits)


def _aware_cutoff(cutoff: datetime | pd.Timestamp | None) -> pd.Timestamp | None:
    if cutoff is None:
        return None
    value = pd.Timestamp(cutoff)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cutoff must include a timezone")
    return value


def _column_map(frame: pd.DataFrame) -> dict[str, Any]:
    lowered = {str(column).strip().lower(): column for column in frame.columns}
    missing = [name for name in ("high", "low", "close") if name not in lowered]
    if missing:
        raise ValueError(f"missing OHLC columns: {', '.join(missing)}")
    return lowered


def _cut_frame(
    frame: pd.DataFrame,
    cutoff: datetime | pd.Timestamp | None,
) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    bounded = frame.copy()
    cutoff_ts = _aware_cutoff(cutoff)
    if isinstance(bounded.index, pd.DatetimeIndex):
        bounded = bounded[~bounded.index.duplicated(keep="last")].sort_index()
        if cutoff_ts is not None:
            if bounded.index.tz is None:
                bounded = bounded[pd.Index(bounded.index.date) <= cutoff_ts.date()]
            else:
                bounded = bounded[bounded.index <= cutoff_ts.tz_convert(bounded.index.tz)]
    elif cutoff_ts is not None:
        raise ValueError("cutoff requires a DatetimeIndex")
    return bounded, cutoff_ts


def _clean_ohlc(
    frame: pd.DataFrame,
    cutoff: datetime | pd.Timestamp | None,
) -> tuple[pd.DataFrame, pd.Timestamp | None, int]:
    bounded, cutoff_ts = _cut_frame(frame, cutoff)
    total_rows = len(bounded)
    columns = _column_map(bounded)
    clean = pd.DataFrame(index=bounded.index)
    for target in ("high", "low", "close"):
        clean[target] = pd.to_numeric(
            bounded[columns[target]], errors="coerce"
        ).replace([np.inf, -np.inf], np.nan)
    valid = (
        clean[["high", "low", "close"]].notna().all(axis=1)
        & (clean["high"] >= clean["low"])
        & (clean["close"] <= clean["high"])
        & (clean["close"] >= clean["low"])
    )
    clean = clean.loc[valid]
    return clean, cutoff_ts, total_rows


def _resolved_cutoff(
    frame: pd.DataFrame,
    cutoff: pd.Timestamp | None,
) -> str:
    if cutoff is not None:
        return cutoff.tz_convert(timezone.utc).isoformat()
    if isinstance(frame.index, pd.DatetimeIndex) and len(frame.index):
        last = pd.Timestamp(frame.index[-1])
        if last.tzinfo is None:
            last = last.tz_localize(timezone.utc)
        else:
            last = last.tz_convert(timezone.utc)
        return last.isoformat()
    return datetime.now(timezone.utc).isoformat()


def _rma(values: pd.Series, length: int, *, preserve_missing: bool) -> pd.Series:
    if length < 1:
        raise ValueError("RMA length must be at least 1")
    result = pd.Series(np.nan, index=values.index, dtype=float)
    state: float | None = None
    alpha = 1.0 / float(length)
    for index, raw in values.items():
        value = _finite(raw, ndigits=15)
        if value is None:
            if not preserve_missing:
                value = 0.0
            else:
                continue
        state = value if state is None else alpha * value + (1.0 - alpha) * state
        result.at[index] = state
    return result


def _legacy_series(
    clean: pd.DataFrame,
    *,
    length: int,
    m: float,
    n1: float,
    fast_length: int,
) -> pd.DataFrame:
    highest = clean["high"].rolling(length, min_periods=length).max()
    lowest = clean["low"].rolling(length, min_periods=length).min()
    spread = highest - lowest
    gate = spread.notna() & np.isfinite(spread) & (spread != 0)
    position = pd.Series(0.0, index=clean.index, dtype=float)
    position.loc[gate] = (
        (clean.loc[gate, "close"] - lowest.loc[gate]) / spread.loc[gate] * 100.0
    )
    b1 = pd.Series(0.0, index=clean.index, dtype=float)
    b1.loc[gate] = (
        (highest.loc[gate] - clean.loc[gate, "close"]) / spread.loc[gate] * 100.0
        - float(m)
    )
    b2 = _rma(b1, length, preserve_missing=False) + 100.0
    b4 = _rma(position, fast_length, preserve_missing=False)
    b5 = _rma(b4, fast_length, preserve_missing=False) + 100.0
    exact = (b5 - b2 - float(n1)).clip(lower=0.0) * 2.5
    slow = _rma(position, length, preserve_missing=False)
    smooth_gate = _rma(gate.astype(float), length, preserve_missing=False)
    generalized = (
        b4.pipe(lambda _: _rma(_rma(position, fast_length, preserve_missing=False), fast_length, preserve_missing=False))
        + slow
        - (100.0 - float(m)) * smooth_gate
        - float(n1)
    ).clip(lower=0.0) * 2.5
    return pd.DataFrame(
        {
            "range_position": position,
            "range_persistence_fast": b5 - 100.0,
            "range_persistence_slow": slow,
            "legacy_gate": smooth_gate,
            "legacy_control": exact,
            "legacy_control_generalized": generalized,
        },
        index=clean.index,
    )


def _empty_result(
    *,
    status: str,
    sample_count: int,
    quality: float,
    cutoff: str,
    version: str,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    result: dict[str, Any] = {field: None for field in _VALUE_FIELDS}
    result.update(
        {
            "status": status,
            "sample_count": int(sample_count),
            "quality": round(max(0.0, min(1.0, quality)), 6),
            "calculation_cutoff_at": cutoff,
            "version": version,
            "warnings": list(warnings),
        }
    )
    return result


def compute_legacy_control_exact(
    frame: pd.DataFrame,
    *,
    length: int = 35,
    m: float = 35.0,
    n1: float = 3.0,
    fast_length: int = 3,
    cutoff: datetime | pd.Timestamp | None = None,
    version: str = RANGE_PERSISTENCE_VERSION,
) -> dict[str, Any]:
    """Reproduce the supplied Pine calculation step by step.

    The public summary exposes both the stepwise result and the generalized
    identity.  Their equality is a regression guard for warmup and flat-range
    behavior; the simplified steady-state identity is deliberately not used.
    """

    if length < 1 or fast_length < 1:
        raise ValueError("lengths must be positive")
    clean, cutoff_ts, total_rows = _clean_ohlc(frame, cutoff)
    resolved = _resolved_cutoff(clean, cutoff_ts)
    if clean.empty:
        return _empty_result(
            status="insufficient_history",
            sample_count=0,
            quality=0.0,
            cutoff=resolved,
            version=version,
            warnings=("no_valid_ohlc",),
        )
    series = _legacy_series(
        clean,
        length=length,
        m=m,
        n1=n1,
        fast_length=fast_length,
    )
    latest = series.iloc[-1]
    coverage = len(clean) / max(total_rows, 1)
    return {
        "legacy_control": _finite(latest["legacy_control"]),
        "legacy_control_generalized": _finite(latest["legacy_control_generalized"]),
        "range_position": _finite(latest["range_position"]),
        "range_persistence_fast": _finite(latest["range_persistence_fast"]),
        "range_persistence_slow": _finite(latest["range_persistence_slow"]),
        "legacy_gate": _finite(latest["legacy_gate"]),
        "status": "active" if len(clean) >= length else "warming_up",
        "sample_count": len(clean),
        "quality": round(max(0.0, min(1.0, coverage)), 6),
        "calculation_cutoff_at": resolved,
        "version": version,
        "warnings": [],
    }


def _percentile(value: float | None, distribution: Iterable[Any] | None) -> float | None:
    if value is None or distribution is None:
        return None
    finite = sorted(
        number
        for raw in distribution
        if (number := _finite(raw, ndigits=15)) is not None
    )
    if not finite:
        return None
    below = sum(item < value for item in finite)
    tied = sum(item == value for item in finite)
    return _finite((below + 0.5 * tied) / len(finite) * 100.0)


def _finite_distribution(distribution: Iterable[Any] | None) -> list[float]:
    if distribution is None:
        return []
    return [
        number
        for raw in distribution
        if (number := _finite(raw, ndigits=15)) is not None
    ]


def _robust_z(value: float | None, distribution: Iterable[Any] | None) -> float | None:
    values = _finite_distribution(distribution)
    if value is None or len(values) < 3:
        return None
    median = float(np.median(values))
    mad = float(np.median(np.abs(np.asarray(values) - median)))
    denominator = 1.4826 * mad + 1e-9
    return _finite(max(-3.0, min(3.0, (value - median) / denominator)))


def _logistic_score(z_value: float | None) -> float | None:
    if z_value is None:
        return None
    return _finite(100.0 / (1.0 + math.exp(-1.2 * z_value)))


def _ols_slope(values: pd.Series, lookback: int) -> float | None:
    tail = values.tail(lookback)
    if len(tail) != lookback or tail.isna().any():
        return None
    x = np.arange(lookback, dtype=float)
    y = tail.to_numpy(dtype=float)
    centered = x - x.mean()
    denominator = float(np.dot(centered, centered))
    if denominator <= 0:
        return None
    return _finite(float(np.dot(centered, y - y.mean()) / denominator))


def compute_range_persistence(
    frame: pd.DataFrame,
    *,
    length: int = 35,
    fast_length: int = 3,
    slope_lookback: int = 5,
    ratio_window: int = 10,
    ratio_threshold: float = 60.0,
    min_history_multiplier: int = 5,
    self_percentile_window: int = 252,
    cutoff: datetime | pd.Timestamp | None = None,
    global_distribution: Iterable[Any] | None = None,
    sector_distribution: Iterable[Any] | None = None,
    version: str = RANGE_PERSISTENCE_VERSION,
) -> dict[str, Any]:
    """Compute the improved continuous feature from complete point-in-time bars."""

    if min(length, fast_length, slope_lookback, ratio_window, min_history_multiplier) < 1:
        raise ValueError("lengths and windows must be positive")
    clean, cutoff_ts, total_rows = _clean_ohlc(frame, cutoff)
    resolved = _resolved_cutoff(clean, cutoff_ts)
    minimum = max(
        min_history_multiplier * length,
        length + slope_lookback,
        length + ratio_window,
    )
    coverage = len(clean) / max(total_rows, 1)
    if len(clean) < minimum:
        return _empty_result(
            status="insufficient_history",
            sample_count=len(clean),
            quality=coverage * min(len(clean) / minimum, 1.0),
            cutoff=resolved,
            version=version,
            warnings=(f"requires_{minimum}_valid_bars",),
        )

    highest = clean["high"].rolling(length, min_periods=length).max()
    lowest = clean["low"].rolling(length, min_periods=length).min()
    spread = highest - lowest
    valid_range = spread.notna() & np.isfinite(spread) & (spread != 0)
    position = pd.Series(np.nan, index=clean.index, dtype=float)
    position.loc[valid_range] = (
        (clean.loc[valid_range, "close"] - lowest.loc[valid_range])
        / spread.loc[valid_range]
        * 100.0
    ).clip(lower=0.0, upper=100.0)

    first_fast = _rma(position, fast_length, preserve_missing=True)
    fast = _rma(first_fast.where(position.notna()), fast_length, preserve_missing=True)
    slow = _rma(position, length, preserve_missing=True)
    persistence = ((fast + slow) / 2.0).where(position.notna()).clip(0.0, 100.0)

    if not bool(valid_range.iloc[-1]):
        result = _empty_result(
            status="uninformative",
            sample_count=len(clean),
            quality=0.0,
            cutoff=resolved,
            version=version,
            warnings=("flat_current_range",),
        )
        exact = compute_legacy_control_exact(
            frame,
            length=length,
            fast_length=fast_length,
            cutoff=cutoff,
            version=version,
        )
        result["legacy_control"] = exact.get("legacy_control")
        return result

    current = _finite(persistence.iloc[-1])
    current_position = _finite(position.iloc[-1])
    current_fast = _finite(fast.iloc[-1])
    current_slow = _finite(slow.iloc[-1])
    if None in (current, current_position, current_fast, current_slow):
        return _empty_result(
            status="uninformative",
            sample_count=len(clean),
            quality=0.0,
            cutoff=resolved,
            version=version,
            warnings=("non_finite_current_feature",),
        )

    ratio_tail = persistence.tail(ratio_window)
    ratio = None
    if len(ratio_tail) == ratio_window and not ratio_tail.isna().any():
        ratio = _finite((ratio_tail >= float(ratio_threshold)).mean() * 100.0)

    prior = persistence.iloc[:-1].dropna().tail(self_percentile_window)
    self_percentile = (
        _percentile(current, prior.tolist())
        if len(prior) == self_percentile_window
        else None
    )
    exact = compute_legacy_control_exact(
        frame,
        length=length,
        fast_length=fast_length,
        cutoff=cutoff,
        version=version,
    )
    global_values = _finite_distribution(global_distribution)
    sector_values = _finite_distribution(sector_distribution)
    global_z = _robust_z(current, global_values)
    sector_z = _robust_z(current, sector_values)
    shrunk_z = None
    if sector_z is not None and global_z is not None:
        alpha = len(sector_values) / (len(sector_values) + 20.0)
        shrunk_z = _finite(alpha * sector_z + (1.0 - alpha) * global_z)
    normalized_score = _logistic_score(shrunk_z if shrunk_z is not None else global_z)
    if normalized_score is None:
        normalized_score = self_percentile
    result = {
        "range_position": current_position,
        "range_persistence_fast": current_fast,
        "range_persistence_slow": current_slow,
        "range_persistence": current,
        "range_persistence_slope_5d": _ols_slope(persistence, slope_lookback),
        "range_persistence_ratio_10d": ratio,
        "range_persistence_self_percentile": self_percentile,
        "range_persistence_global_percentile": _percentile(current, global_values),
        "range_persistence_sector_percentile": _percentile(current, sector_values),
        "range_persistence_global_robust_z": global_z,
        "range_persistence_sector_robust_z": sector_z,
        "range_persistence_sector_shrunk_z": shrunk_z,
        "range_persistence_normalized_score": normalized_score,
        "range_persistence_global_sample_count": len(global_values),
        "range_persistence_sector_sample_count": len(sector_values),
        "legacy_control": exact.get("legacy_control"),
        "status": "active",
        "sample_count": len(clean),
        "quality": round(max(0.0, min(1.0, coverage)), 6),
        "calculation_cutoff_at": resolved,
        "version": version,
        "warnings": [],
    }
    for field in _VALUE_FIELDS:
        result[field] = _finite(result.get(field))
    return result


def build_range_persistence_shadow(
    *,
    mode: Literal["disabled", "shadow", "enabled"] = "shadow",
    production_score: float | None,
    hypothetical_score: float | None,
    production_rank: int | None = None,
    hypothetical_rank: int | None = None,
    effective_weight: float = 0.0,
    final_weight_cap: float = 0.04,
    version: str = RANGE_PERSISTENCE_VERSION,
) -> dict[str, Any]:
    """Build an auditable shadow record without silently changing production."""

    if not 0.0 <= final_weight_cap <= 1.0:
        raise ValueError("final_weight_cap must be between 0 and 1")
    bounded_weight = max(0.0, min(float(effective_weight), float(final_weight_cap)))
    production = _finite(production_score)
    hypothetical = _finite(hypothetical_score)
    selected = hypothetical if mode == "enabled" and hypothetical is not None else production
    score_delta = (
        _finite(hypothetical - production)
        if production is not None and hypothetical is not None
        else None
    )
    rank_delta = (
        int(hypothetical_rank - production_rank)
        if production_rank is not None and hypothetical_rank is not None
        else None
    )
    return {
        "mode": mode,
        "production_score": production,
        "hypothetical_score": hypothetical,
        "selected_score": selected,
        "score_delta": score_delta,
        "production_rank": production_rank,
        "hypothetical_rank": hypothetical_rank,
        "rank_delta": rank_delta,
        "effective_weight": round(bounded_weight, 6),
        "contribution_cap": round(float(final_weight_cap), 6),
        "production_unchanged": mode != "enabled",
        "version": version,
    }
