"""Memoize the service's pure daily and intraday computations without editing them.

The service calls these functions through module globals, so the harness rebinds the
names to caching wrappers. Keys are content fingerprints of the input frames plus every
scalar argument that can change the result; outputs are copies because the service
mutates the dictionaries it gets back. A run with the memo off must be byte-identical.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts import feature_engine, service
from app.services.breakouts.config import get_breakout_settings
from app.services.breakouts.feature_engine import completed_daily_session

NY = ZoneInfo("America/New_York")


def frame_fingerprint(frame: pd.DataFrame | None) -> tuple:
    if frame is None:
        return ("none",)
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return ("empty", tuple(map(str, getattr(frame, "columns", ()))))
    values = np.ascontiguousarray(frame.to_numpy(dtype=float, na_value=np.nan))
    digest = hashlib.blake2b(values.tobytes(), digest_size=16)
    index = frame.index
    digest.update(np.ascontiguousarray(index.asi8 if hasattr(index, "asi8") else np.asarray(index)).tobytes())
    return (tuple(map(str, frame.columns)), len(frame), digest.hexdigest())


def _detector_key(settings: Any) -> tuple:
    config = settings or get_breakout_settings()
    return tuple(
        getattr(config, name)
        for name in (
            "base_min_days", "base_max_days", "pivot_tolerance_atr",
            "break_buffer_atr", "break_buffer_pct", "detector_version",
        )
    )


def _hashable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(NY).date().isoformat()
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(k), _hashable(v)) for k, v in value.items()))
    return value


class Memo:
    """Installs and removes the wrappers; ``stats`` reports hits and misses per function."""

    def __init__(self) -> None:
        self._daily_scope: dict[tuple, Any] = {}
        self._scan_scope: dict[tuple, Any] = {}
        self.stats: dict[str, dict[str, int]] = {}
        self._originals: dict[str, Callable[..., Any]] = {}
        self.installed = False

    def _count(self, name: str, hit: bool) -> None:
        bucket = self.stats.setdefault(name, {"hits": 0, "misses": 0})
        bucket["hits" if hit else "misses"] += 1

    def clear_scan_scope(self) -> None:
        self._scan_scope.clear()

    def clear_day_scope(self) -> None:
        self._daily_scope.clear()
        self._scan_scope.clear()

    def install(self) -> None:
        if self.installed:
            return
        originals = {
            "detect_base": service.detect_base,
            "_scan_range_feature": service._scan_range_feature,
            "compute_feature_snapshot": service.compute_feature_snapshot,
            "relative_strength_features": service.relative_strength_features,
            "compute_time_of_day_rvol": feature_engine.compute_time_of_day_rvol,
        }
        self._originals = originals
        daily = self._daily_scope
        scan = self._scan_scope
        count = self._count

        def detect_base(ticker, daily_frame, cutoff, settings=None):
            key = ("base", str(ticker), frame_fingerprint(daily_frame), completed_daily_session(cutoff), _detector_key(settings))
            hit = daily.get(key)
            if hit is not None:
                count("detect_base", True)
                return hit[0].model_copy(deep=True) if hit[0] is not None else None
            count("detect_base", False)
            result = originals["detect_base"](ticker, daily_frame, cutoff, settings)
            daily[key] = (result,)
            return result.model_copy(deep=True) if result is not None else None

        def _scan_range_feature(frame, *, version, **kwargs):
            key = ("range", frame_fingerprint(frame), version, _hashable(kwargs))
            hit = daily.get(key)
            if hit is not None:
                count("_scan_range_feature", True)
                return copy.deepcopy(hit)
            count("_scan_range_feature", False)
            result = originals["_scan_range_feature"](frame, version=version, **kwargs)
            daily[key] = result
            return copy.deepcopy(result)

        def compute_feature_snapshot(*, daily: pd.DataFrame, intraday: pd.DataFrame, cutoff, opening_range_minutes=30, rvol_lookback_sessions=20):
            key = (
                "snapshot", frame_fingerprint(daily), frame_fingerprint(intraday),
                cutoff.event_at.astimezone(NY).isoformat(), cutoff.session.value,
                bool(cutoff.include_current_bar), completed_daily_session(cutoff),
                int(opening_range_minutes), int(rvol_lookback_sessions),
            )
            hit = scan.get(key)
            if hit is not None:
                count("compute_feature_snapshot", True)
                return copy.deepcopy(hit)
            count("compute_feature_snapshot", False)
            result = originals["compute_feature_snapshot"](
                daily=daily, intraday=intraday, cutoff=cutoff,
                opening_range_minutes=opening_range_minutes, rvol_lookback_sessions=rvol_lookback_sessions,
            )
            scan[key] = result
            return copy.deepcopy(result)

        def relative_strength_features(stock_frame, market_frame, sector_frame=None, *, market_symbol="SPY", sector_symbol=None, sector_breadth_score=None):
            key = (
                "relative", frame_fingerprint(stock_frame), frame_fingerprint(market_frame),
                frame_fingerprint(sector_frame), market_symbol, sector_symbol, sector_breadth_score,
            )
            hit = daily.get(key)
            if hit is not None:
                count("relative_strength_features", True)
                return dict(hit)
            count("relative_strength_features", False)
            result = originals["relative_strength_features"](
                stock_frame, market_frame, sector_frame,
                market_symbol=market_symbol, sector_symbol=sector_symbol, sector_breadth_score=sector_breadth_score,
            )
            daily[key] = result
            return dict(result)

        def compute_time_of_day_rvol(frame, cutoff, lookback_sessions=20, min_sessions=5):
            key = (
                "rvol", frame_fingerprint(frame), cutoff.event_at.astimezone(NY).isoformat(),
                cutoff.session.value, bool(cutoff.include_current_bar), int(lookback_sessions), int(min_sessions),
            )
            hit = scan.get(key)
            if hit is not None:
                count("compute_time_of_day_rvol", True)
                return dict(hit)
            count("compute_time_of_day_rvol", False)
            result = originals["compute_time_of_day_rvol"](frame, cutoff, lookback_sessions, min_sessions)
            scan[key] = result
            return dict(result)

        service.detect_base = detect_base  # type: ignore[assignment]
        service._scan_range_feature = _scan_range_feature  # type: ignore[assignment]
        service.compute_feature_snapshot = compute_feature_snapshot  # type: ignore[assignment]
        service.relative_strength_features = relative_strength_features  # type: ignore[assignment]
        feature_engine.compute_time_of_day_rvol = compute_time_of_day_rvol  # type: ignore[assignment]
        self.installed = True

    def uninstall(self) -> None:
        if not self.installed:
            return
        service.detect_base = self._originals["detect_base"]  # type: ignore[assignment]
        service._scan_range_feature = self._originals["_scan_range_feature"]  # type: ignore[assignment]
        service.compute_feature_snapshot = self._originals["compute_feature_snapshot"]  # type: ignore[assignment]
        service.relative_strength_features = self._originals["relative_strength_features"]  # type: ignore[assignment]
        feature_engine.compute_time_of_day_rvol = self._originals["compute_time_of_day_rvol"]  # type: ignore[assignment]
        self.installed = False
        self.clear_day_scope()
