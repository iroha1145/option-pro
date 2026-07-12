"""Three-stage Breakout Radar enrichment and deterministic event assembly."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from typing import Any, Mapping

import pandas as pd

from app.services.breakouts.adapters import (
    ExistingMarketShapeAdapter,
    ExistingStrengthAdapter,
    ThemeCanonicalUniverseAdapter,
    YahooPriceDataAdapter,
)
from app.services.breakouts.base_detector import detect_base
from app.services.breakouts.breakout_detector import detect_breakout
from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.feature_engine import (
    completed_daily_session,
    compute_atr,
    compute_feature_snapshot,
    trim_daily_bars,
    trim_intraday_bars,
)
from app.services.breakouts.lifecycle import event_identity, transition_state
from app.services.breakouts.models import (
    BreakoutCandidate,
    BreakoutEvent,
    BreakoutLifecycleState,
    BreakoutSetupType,
    MarketSession,
    MarketShapeSnapshot,
    TemporalCutoff,
)
from app.services.breakouts.scoring import score_breakout
from app.services.technical.range_persistence import compute_range_persistence


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quality(value: Any, *, low: float = 0.0, high: float = 1.0) -> float | None:
    number = _finite(value)
    if number is None or high <= low:
        return None
    return max(0.0, min(100.0, (number - low) / (high - low) * 100.0))


def _aware_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return fallback
    return parsed if parsed.tzinfo is not None else fallback


class BreakoutRadarService:
    """Revalidate discovery candidates with Option Pro's own data."""

    def __init__(
        self,
        settings: BreakoutSettings | None = None,
        *,
        price_data: Any = None,
        strength: Any = None,
        market_shape: Any = None,
        universe: Any = None,
    ) -> None:
        self.settings = settings or get_breakout_settings()
        self.price_data = price_data or YahooPriceDataAdapter()
        self.strength = strength or ExistingStrengthAdapter()
        self.market_shape = market_shape or ExistingMarketShapeAdapter()
        self.universe = universe or ThemeCanonicalUniverseAdapter()
        self._range_distribution_cache: dict[str, dict[str, Any]] = {}

    def _cutoff(self, as_of: datetime, session: MarketSession) -> TemporalCutoff:
        return TemporalCutoff(
            event_at=as_of,
            session=session,
            include_current_bar=False,
        )

    @staticmethod
    def _base_features(structure: Any) -> dict[str, Any]:
        if structure is None:
            return {}
        metrics = dict(structure.metrics)
        metrics.setdefault("tightness_quality", _quality(12.0 - (structure.base_width_atr or 12), high=12))
        metrics.setdefault("duration_quality", _quality(structure.base_duration_days, low=10, high=50))
        metrics.setdefault(
            "resistance_touch_quality",
            _quality(structure.pivot_touch_count, low=1, high=4),
        )
        return metrics

    @staticmethod
    def _confirmation_features(
        features: Mapping[str, Any],
        structure: Any,
        daily: pd.DataFrame,
    ) -> dict[str, Any]:
        close = _finite(features.get("event_price"))
        atr = _finite(features.get("atr20"))
        resistance = structure.resistance_zone.high if structure is not None else None
        distance = (
            (close - resistance) / atr
            if close is not None and resistance is not None and atr and atr > 0
            else None
        )
        clv = _finite(features.get("close_location_value"))
        rvol = _finite(features.get("rvol_time_of_day"))
        body = _finite(features.get("candle_body_ratio"))
        upper = _finite(features.get("upper_wick_ratio"))
        vwap_distance = _finite(features.get("distance_from_vwap_atr"))
        gap_atr = _finite(features.get("gap_atr"))
        avg_dollar = None
        if not daily.empty and "Volume" in daily.columns:
            average_volume = pd.to_numeric(
                daily["Volume"].tail(20), errors="coerce"
            ).replace([float("inf"), float("-inf")], float("nan")).mean()
            daily_close = _finite(daily["Close"].iloc[-1])
            if daily_close is not None and math.isfinite(float(average_volume)):
                avg_dollar = daily_close * float(average_volume)
        current_dollar = _finite(features.get("cumulative_dollar_volume"))
        return {
            "close_above_zone_quality": _quality(distance, low=0, high=1.0),
            "rvol_time_of_day_quality": _quality(rvol, low=0.5, high=3.0),
            "close_location_quality": _quality(clv, low=-1.0, high=1.0),
            "hold_quality": _quality(features.get("hold_bars_above_pivot"), low=0, high=3),
            "candle_body_quality": _quality(body, low=0, high=0.8),
            "upper_wick_quality": (
                100.0 - _quality(upper, low=0, high=0.5)
                if upper is not None
                else None
            ),
            "relative_strength_confirmation": None,
            "average_dollar_volume_quality": _quality(
                avg_dollar, low=1_000_000, high=50_000_000
            ),
            "current_dollar_volume_quality": _quality(
                current_dollar, low=500_000, high=20_000_000
            ),
            "dollar_volume_percentile": None,
            "spread_quality": None,
            "intraday_completeness_quality": (
                100.0 if features.get("status") == "active" else None
            ),
            "distance_from_pivot_risk": _quality(distance, low=0.5, high=3.0),
            "distance_from_vwap_risk": (
                _quality(abs(vwap_distance), low=0.5, high=3.0)
                if vwap_distance is not None
                else None
            ),
            "gap_atr_risk": (
                _quality(abs(gap_atr), low=0, high=3)
                if gap_atr is not None
                else None
            ),
            "upper_wick_risk": _quality(upper, low=0, high=0.5),
            "short_term_acceleration_risk": _quality(
                features.get("short_term_acceleration"), low=0, high=3
            ),
            "liquidity_risk": (
                100.0 - _quality(avg_dollar, low=1_000_000, high=50_000_000)
                if avg_dollar is not None
                else None
            ),
            "event_freshness_score": 100.0,
            "breakout_distance_atr": distance,
        }

    def _structure_intraday_features(
        self,
        structure: Any,
        intraday: pd.DataFrame,
        cutoff: TemporalCutoff,
        atr: float | None,
    ) -> dict[str, Any]:
        if structure is None or not isinstance(intraday, pd.DataFrame):
            return {"hold_bars_above_pivot": None}
        visible = trim_intraday_bars(intraday, cutoff)
        if visible.empty:
            return {"hold_bars_above_pivot": None}
        local = visible.index.tz_convert("America/New_York")
        event_date = cutoff.event_at.astimezone(local.tz).date()
        current = visible[pd.Index(local.date) == event_date]
        if current.empty:
            return {"hold_bars_above_pivot": None}
        resistance = float(structure.resistance_zone.high)
        latest_close = _finite(current["Close"].iloc[-1])
        buffer = max(
            (latest_close or resistance) * self.settings.break_buffer_pct,
            (atr or 0.0) * self.settings.break_buffer_atr,
        )
        threshold = resistance + buffer
        hold_bars = 0
        for raw in reversed(list(pd.to_numeric(current["Close"], errors="coerce"))):
            close = _finite(raw)
            if close is None or close <= threshold:
                break
            hold_bars += 1
        return {
            "hold_bars_above_pivot": hold_bars,
            "resistance_price": structure.pivot_price,
            "support_zone": structure.support_zone.model_dump(mode="json")
            if structure.support_zone is not None
            else None,
            "resistance_zone": structure.resistance_zone.model_dump(mode="json"),
            "invalidation_price": structure.invalidation_price,
            "close_above_resistance_atr": (
                (latest_close - resistance) / atr
                if latest_close is not None and atr is not None and atr > 0
                else None
            ),
        }

    async def build_snapshot(
        self,
        discovery: Any,
        clock_snapshot: Any = None,
        *,
        as_of: datetime | None = None,
        session: MarketSession | None = None,
        previous_events: Mapping[str, list[Mapping[str, Any]]] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        observed_at = as_of or getattr(discovery, "as_of", None)
        observed_session = session or getattr(discovery, "session", None)
        if observed_at is None or observed_session is None:
            raise ValueError("scan requires as_of and session")
        cutoff = self._cutoff(observed_at, observed_session)
        candidates = [
            item if isinstance(item, BreakoutCandidate) else BreakoutCandidate.model_validate(item)
            for item in list(getattr(discovery, "candidates", ()) or ())[
                : self.settings.provider_result_limit
            ]
        ]
        if not candidates:
            return {
                "events": [],
                "structures": [],
                "transitions": [],
                "range_persistence_shadow": [],
                "source_status": {"discovery": getattr(discovery, "status", "unavailable")},
            }
        stage_two = candidates[: self.settings.daily_enrich_limit]
        tickers = [item.ticker for item in stage_two]
        distribution_key = "|".join(
            [
                completed_daily_session(cutoff).isoformat(),
                str(getattr(self.universe, "version", "unknown")),
                self.settings.range_persistence_version,
            ]
        )
        cached_distribution = self._range_distribution_cache.get(distribution_key)
        if cached_distribution is not None:
            canonical_tickers = []
        else:
            try:
                canonical_tickers = list(
                    await self.universe.tickers(as_of=observed_at)
                )
            except Exception:
                canonical_tickers = []
        daily_symbols = list(dict.fromkeys([*tickers, *canonical_tickers, "SPY"]))
        daily_task = self.price_data.daily(daily_symbols, cutoff=cutoff, period="2y")
        market_task = self.market_shape.snapshot(as_of=observed_at)
        daily_result, market_result = await asyncio.gather(
            daily_task,
            market_task,
            return_exceptions=True,
        )
        daily_map = {} if isinstance(daily_result, Exception) else dict(daily_result)
        market = (
            MarketShapeSnapshot(
                status="unavailable",
                state=None,
                confidence=0.0,
                as_of=observed_at,
                warnings=["market_shape_adapter_failed"],
                version=getattr(self.market_shape, "version", "unknown"),
            )
            if isinstance(market_result, Exception)
            else market_result
        )
        try:
            from_daily = getattr(self.strength, "score_from_daily_snapshots", None)
            if callable(from_daily):
                strength_map = await from_daily(
                    tickers,
                    snapshots=daily_map,
                    as_of=observed_at,
                    include_options=False,
                    range_mode=self.settings.range_persistence_mode,
                    range_trend_weight=self.settings.range_persistence_trend_family_weight,
                    range_final_cap=self.settings.range_persistence_final_weight_cap,
                )
            else:
                strength_map = await self.strength.score_ticker_set(
                    tickers,
                    as_of=observed_at,
                    include_options=False,
                )
        except Exception:
            strength_map = {}

        if cached_distribution is None:
            global_values: list[float] = []
            sector_values: dict[str, list[float]] = {}
            member_values: dict[str, float] = {}
            for ticker in canonical_tickers:
                snapshot = daily_map.get(ticker)
                if snapshot is None:
                    continue
                feature = compute_range_persistence(
                    trim_daily_bars(snapshot.frame, cutoff),
                    cutoff=snapshot.cutoff.event_at,
                    length=self.settings.range_persistence_length,
                    fast_length=self.settings.range_persistence_fast_length,
                    slope_lookback=self.settings.range_persistence_slope_days,
                    ratio_window=self.settings.range_persistence_ratio_window,
                    ratio_threshold=self.settings.range_persistence_ratio_threshold,
                    min_history_multiplier=self.settings.range_persistence_min_history_multiplier,
                    version=self.settings.range_persistence_version,
                )
                value = _finite(feature.get("range_persistence"))
                if feature.get("status") != "active" or value is None:
                    continue
                member_values[ticker] = value
                global_values.append(value)
                sector = self.universe.primary_sector(ticker)
                if sector is not None:
                    sector_values.setdefault(sector, []).append(value)
            minimum_global = max(30, int(len(canonical_tickers) * 0.60))
            coverage_ratio = len(member_values) / max(len(canonical_tickers), 1)
            global_active = len(global_values) >= minimum_global
            usable_sectors = {
                key: tuple(value)
                for key, value in sector_values.items()
                if len(value) >= 5
            }
            cached_distribution = {
                "as_of": completed_daily_session(cutoff).isoformat(),
                "universe_version": getattr(self.universe, "version", "unknown"),
                "members": frozenset(canonical_tickers),
                "member_values": member_values,
                "global": tuple(global_values) if global_active else (),
                "sectors": usable_sectors if global_active else {},
                "coverage_ratio": coverage_ratio,
                "status": (
                    "active"
                    if global_active
                    else "degraded"
                    if global_values
                    else "unavailable"
                ),
            }
            self._range_distribution_cache[distribution_key] = cached_distribution
            while len(self._range_distribution_cache) > 5:
                self._range_distribution_cache.pop(next(iter(self._range_distribution_cache)))

        prepared: list[tuple[BreakoutCandidate, Any, Any, dict[str, Any]]] = []
        structures = []
        for candidate in stage_two:
            daily_snapshot = daily_map.get(candidate.ticker)
            if daily_snapshot is None:
                continue
            daily = trim_daily_bars(daily_snapshot.frame, cutoff)
            structure = detect_base(candidate.ticker, daily, cutoff, self.settings)
            if structure is not None:
                structures.append(structure)
            range_feature = compute_range_persistence(
                daily,
                cutoff=daily_snapshot.cutoff.event_at,
                length=self.settings.range_persistence_length,
                fast_length=self.settings.range_persistence_fast_length,
                slope_lookback=self.settings.range_persistence_slope_days,
                ratio_window=self.settings.range_persistence_ratio_window,
                ratio_threshold=self.settings.range_persistence_ratio_threshold,
                min_history_multiplier=self.settings.range_persistence_min_history_multiplier,
                global_distribution=(
                    cached_distribution["global"]
                    if candidate.ticker in cached_distribution["members"]
                    else None
                ),
                sector_distribution=(
                    cached_distribution["sectors"].get(
                        self.universe.primary_sector(candidate.ticker)
                    )
                    if candidate.ticker in cached_distribution["members"]
                    else None
                ),
                version=self.settings.range_persistence_version,
            )
            range_feature["canonical_universe_as_of"] = cached_distribution["as_of"]
            range_feature["canonical_universe_version"] = cached_distribution[
                "universe_version"
            ]
            range_feature["canonical_universe_status"] = cached_distribution["status"]
            range_feature["canonical_universe_member"] = (
                candidate.ticker in cached_distribution["members"]
            )
            prepared.append((candidate, daily_snapshot, structure, range_feature))
        prepared.sort(
            key=lambda item: (
                item[2].quality if item[2] is not None else 0.0,
                item[0].provider_change_pct or 0.0,
                item[0].ticker,
            ),
            reverse=True,
        )
        refined = prepared[: self.settings.intraday_enrich_limit]
        try:
            intraday_map = await self.price_data.intraday(
                [item[0].ticker for item in refined],
                cutoff=cutoff,
                interval="5m",
            )
        except Exception:
            intraday_map = {}

        events = []
        transitions = []
        shadows = []
        versions = {
            "feature_version": self.settings.feature_version,
            "detector_version": self.settings.detector_version,
            "scoring_version": self.settings.scoring_version,
            "range_persistence_version": self.settings.range_persistence_version,
            "strength_score_version": getattr(self.strength, "version", "unknown"),
            "market_shape_version": getattr(market, "version", "unknown"),
            "universe_version": getattr(self.universe, "version", "unknown"),
        }
        source_snapshot_id = str(getattr(discovery, "cache_key", None) or "discovery")
        prior_by_ticker = previous_events or {}
        for candidate, daily_snapshot, structure, range_feature in refined:
            intraday_snapshot = intraday_map.get(candidate.ticker)
            if intraday_snapshot is None:
                features = {
                    "status": "insufficient_data",
                    "event_price": None,
                    "atr20": compute_atr(daily_snapshot.frame),
                    "warnings": ["intraday_snapshot_unavailable"],
                }
            else:
                features = compute_feature_snapshot(
                    daily=daily_snapshot.frame,
                    intraday=intraday_snapshot.frame,
                    cutoff=cutoff,
                )
                features.update(
                    self._structure_intraday_features(
                        structure,
                        intraday_snapshot.frame,
                        cutoff,
                        _finite(features.get("atr20")),
                    )
                )
            features.update(self._base_features(structure))
            features.update(
                self._confirmation_features(
                    features,
                    structure,
                    daily_snapshot.frame,
                )
            )
            features.update(
                {
                    "current_price": _finite(features.get("event_price"))
                    or candidate.price,
                    "gap_pct": (
                        (
                            float(features["event_price"])
                            / candidate.previous_regular_close
                            - 1.0
                        )
                        * 100.0
                        if _finite(features.get("event_price")) is not None
                        and candidate.previous_regular_close is not None
                        and candidate.previous_regular_close > 0
                        else None
                    ),
                    "gap_atr": (
                        (
                            float(features["event_price"])
                            - candidate.previous_regular_close
                        )
                        / float(features["atr20"])
                        if _finite(features.get("event_price")) is not None
                        and candidate.previous_regular_close is not None
                        and _finite(features.get("atr20")) is not None
                        and float(features["atr20"]) > 0
                        else None
                    ),
                    "event_freshness_score": max(
                        0.0,
                        min(
                            100.0,
                            100.0
                            * (
                                1.0
                                - max(
                                    0.0,
                                    (
                                        observed_at.astimezone(candidate.provider_timestamp.tzinfo)
                                        - candidate.provider_timestamp
                                    ).total_seconds(),
                                )
                                / self.settings.event_ttl_seconds
                            ),
                        ),
                    ),
                    "range_persistence": range_feature.get("range_persistence"),
                    "range_persistence_slope_5d": range_feature.get(
                        "range_persistence_slope_5d"
                    ),
                    "range_persistence_ratio_10d": range_feature.get(
                        "range_persistence_ratio_10d"
                    ),
                    "range_persistence_self_percentile": range_feature.get(
                        "range_persistence_self_percentile"
                    ),
                    "range_persistence_global_percentile": range_feature.get(
                        "range_persistence_global_percentile"
                    ),
                    "range_persistence_sector_percentile": range_feature.get(
                        "range_persistence_sector_percentile"
                    ),
                    "range_persistence_normalized_score": range_feature.get(
                        "range_persistence_normalized_score"
                    ),
                    "range_persistence_status": range_feature.get("status"),
                    "canonical_universe_as_of": range_feature.get(
                        "canonical_universe_as_of"
                    ),
                    "canonical_universe_version": range_feature.get(
                        "canonical_universe_version"
                    ),
                    "canonical_universe_status": range_feature.get(
                        "canonical_universe_status"
                    ),
                    "canonical_universe_member": range_feature.get(
                        "canonical_universe_member"
                    ),
                }
            )
            detection = detect_breakout(
                candidate,
                structure,
                features,
                cutoff,
                self.settings,
            )
            strength_snapshot = strength_map.get(candidate.ticker)
            included = (
                strength_snapshot.included_features if strength_snapshot is not None else []
            )
            intrinsic = strength_snapshot.score if strength_snapshot is not None else None
            range_adjustment = 0.0
            if (
                self.settings.range_persistence_breakout_interaction_enabled
                and range_feature.get("status") == "active"
            ):
                slope = _finite(range_feature.get("range_persistence_slope_5d"))
                range_adjustment = max(
                    -self.settings.range_persistence_breakout_interaction_cap,
                    min(
                        self.settings.range_persistence_breakout_interaction_cap,
                        (slope or 0.0) * 0.25,
                    ),
                )
            production_scores = score_breakout(
                features,
                intrinsic_strength=intrinsic,
                market_fit=None,
                sector_fit=None,
                strength_included_features=included,
                range_persistence_adjustment=0.0,
                score_version=self.settings.scoring_version,
            )
            hypothetical_scores = score_breakout(
                features,
                intrinsic_strength=intrinsic,
                market_fit=None,
                sector_fit=None,
                strength_included_features=included,
                range_persistence_adjustment=range_adjustment,
                score_version=self.settings.scoring_version,
            )
            scores = (
                hypothetical_scores
                if self.settings.range_persistence_mode == "enabled"
                else production_scores
            )
            setup = detection["setup_type"]
            pivot_id = (
                structure.pivot_id
                if structure is not None
                else f"{setup.value.lower()}-{candidate.ticker}-{observed_at.date().isoformat()}"
            )
            event_id = event_identity(
                trading_date=observed_at.date(),
                ticker=candidate.ticker,
                setup_type=setup,
                pivot_id=pivot_id,
            )
            prior = next(
                (
                    dict(item)
                    for item in prior_by_ticker.get(candidate.ticker, [])
                    if str(item.get("event_id")) == event_id
                ),
                None,
            )
            initial_state = (
                BreakoutLifecycleState(str(prior.get("lifecycle_state")))
                if prior is not None
                else BreakoutLifecycleState.DISCOVERED
            )
            first_seen_at = (
                _aware_datetime(prior.get("first_seen_at"), observed_at)
                if prior is not None
                else observed_at
            )
            prior_features = dict((prior or {}).get("features") or {})
            price = _finite(features.get("event_price")) or candidate.price
            resistance_high = (
                structure.resistance_zone.high if structure is not None else None
            )
            invalidation = structure.invalidation_price if structure is not None else None
            buffer = max(
                (price or 0.0) * self.settings.break_buffer_pct,
                (_finite(features.get("atr20")) or 0.0)
                * self.settings.break_buffer_atr,
            )
            retesting = bool(
                prior is not None
                and initial_state
                in {
                    BreakoutLifecycleState.CONFIRMED,
                    BreakoutLifecycleState.HOLDING,
                    BreakoutLifecycleState.RETEST_HELD,
                    BreakoutLifecycleState.REACCELERATING,
                    BreakoutLifecycleState.EXTENDED,
                }
                and price is not None
                and resistance_high is not None
                and price <= resistance_high + buffer
                and (invalidation is None or price > invalidation)
            )
            observation = {
                "triggered": bool(detection.get("triggered")),
                "confirmed": bool(detection.get("confirmed")),
                "extended": bool(detection.get("extended")) and not retesting,
                "failed": bool(
                    price is not None
                    and invalidation is not None
                    and price < invalidation
                ),
                "failure_reason": "complete_bar_below_invalidation",
                "holding": bool(
                    initial_state
                    in {
                        BreakoutLifecycleState.CONFIRMED,
                        BreakoutLifecycleState.REACCELERATING,
                    }
                    and detection.get("confirmed")
                    and not retesting
                ),
                "retesting": retesting,
                "retest_held": bool(
                    initial_state is BreakoutLifecycleState.RETESTING
                    and price is not None
                    and resistance_high is not None
                    and price > resistance_high + buffer
                ),
                "reaccelerating": bool(
                    initial_state
                    in {
                        BreakoutLifecycleState.HOLDING,
                        BreakoutLifecycleState.RETEST_HELD,
                    }
                    and _finite(features.get("high")) is not None
                    and _finite(prior_features.get("high")) is not None
                    and float(features["high"]) > float(prior_features["high"])
                    and (_finite(features.get("rvol_time_of_day")) or 0.0) >= 1.0
                ),
                "expired": bool(
                    (observed_at - first_seen_at).total_seconds()
                    > self.settings.event_ttl_seconds
                ),
            }
            state = initial_state
            event_transitions: list[dict[str, Any]] = []
            for _step in range(4):
                step_observation = dict(observation)
                if state is BreakoutLifecycleState.DISCOVERED:
                    step_observation = {}
                elif state is BreakoutLifecycleState.WATCHING:
                    step_observation["confirmed"] = False
                    step_observation["extended"] = False
                result = transition_state(state, step_observation)
                if not result.changed:
                    break
                event_transitions.append(
                    {
                        "event_id": event_id,
                        "from_state": result.previous_state,
                        "to_state": result.state,
                        "reason": result.reason,
                        "evidence_at": observed_at,
                        "evidence": {"source_snapshot_id": source_snapshot_id},
                    }
                )
                state = result.state
            transitions.extend(event_transitions)
            event = BreakoutEvent(
                event_id=event_id,
                trading_date=observed_at.date(),
                ticker=candidate.ticker,
                name=candidate.name,
                exchange=candidate.exchange,
                asset_type=candidate.asset_type,
                sector=candidate.sector,
                session=observed_session,
                setup_type=setup,
                lifecycle_state=state,
                event_at=observed_at,
                first_seen_at=first_seen_at,
                last_seen_at=observed_at,
                pivot_id=pivot_id,
                event_price=_finite(features.get("event_price")) or candidate.price,
                event_bar_interval="5m",
                source_snapshot_id=source_snapshot_id,
                previous_state=initial_state,
                transition_reason=(
                    event_transitions[-1]["reason"]
                    if event_transitions
                    else detection.get("transition_reason")
                ),
                structure=structure,
                scores=scores,
                data_quality={
                    "discovery_source": candidate.source,
                    "daily_price_source": daily_snapshot.source,
                    "intraday_price_source": (
                        intraday_snapshot.source
                        if intraday_snapshot is not None
                        else None
                    ),
                    "strength_status": (
                        "active" if strength_snapshot is not None else "unavailable"
                    ),
                    "provider_quality": candidate.quality,
                    "range_persistence_quality": range_feature.get("quality"),
                    "market_shape_status": market.status,
                    "market_shape_state": market.state,
                    "market_shape_version": market.version,
                },
                versions=versions,
                features={**features, "detection": detection},
                warnings=list(
                    dict.fromkeys(
                        [
                            *candidate.warnings,
                            *detection.get("warnings", []),
                            *market.warnings,
                        ]
                    )
                ),
            )
            events.append(event)
            strength_shadow = (
                strength_snapshot.factor_breakdown.get("range_persistence_shadow")
                if strength_snapshot is not None
                else None
            )
            shadows.append(
                {
                    "trading_date": observed_at.date(),
                    "ticker": candidate.ticker,
                    "event_id": event_id,
                    "feature": range_feature,
                    "production_score": intrinsic,
                    "hypothetical_score": (
                        strength_shadow.get("hypothetical_score")
                        if isinstance(strength_shadow, Mapping)
                        else None
                    ),
                    "score_version": getattr(
                        strength_snapshot, "score_version", "unavailable"
                    ),
                    "feature_version": self.settings.range_persistence_version,
                    "breakout_production_priority": production_scores.alert_priority_score,
                    "breakout_hypothetical_priority": hypothetical_scores.alert_priority_score,
                }
            )
        events.sort(
            key=lambda item: (
                item.scores.alert_priority_score is not None,
                item.scores.alert_priority_score or -1,
                item.event_at,
                item.event_id,
            ),
            reverse=True,
        )
        production_rank = {
            item["event_id"]: rank
            for rank, item in enumerate(
                sorted(
                    [item for item in shadows if item.get("production_score") is not None],
                    key=lambda item: (-float(item["production_score"]), item["ticker"]),
                ),
                1,
            )
        }
        hypothetical_rank = {
            item["event_id"]: rank
            for rank, item in enumerate(
                sorted(
                    [item for item in shadows if item.get("hypothetical_score") is not None],
                    key=lambda item: (-float(item["hypothetical_score"]), item["ticker"]),
                ),
                1,
            )
        }
        for item in shadows:
            item["production_rank"] = production_rank.get(item["event_id"])
            item["hypothetical_rank"] = hypothetical_rank.get(item["event_id"])
            item["rank_delta"] = (
                item["hypothetical_rank"] - item["production_rank"]
                if item["production_rank"] is not None
                and item["hypothetical_rank"] is not None
                else None
            )
        return {
            "events": events,
            "structures": structures,
            "transitions": transitions,
            "range_persistence_shadow": shadows,
            "source_status": {
                "discovery": str(getattr(discovery, "status", "unknown")),
                "prices": "active" if daily_map else "unavailable",
                "strength": "active" if strength_map else "unavailable",
                "market_shape": market.status,
            },
            "versions": versions,
        }
