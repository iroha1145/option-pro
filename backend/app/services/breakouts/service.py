"""Three-stage Breakout Radar enrichment and deterministic event assembly."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

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
    compute_average_dollar_volume,
    compute_atr,
    compute_feature_snapshot,
    trim_daily_bars,
    trim_intraday_bars,
)
from app.services.breakouts.lifecycle import (
    classify_continuation_setup,
    event_identity,
    transition_state,
)
from app.services.breakouts.models import (
    BreakoutCandidate,
    BreakoutEvent,
    BreakoutLifecycleState,
    BreakoutSetupType,
    BreakoutStructure,
    MarketSession,
    MarketShapeSnapshot,
    TemporalCutoff,
)
from app.services.breakouts.relative_strength import (
    percentile_rank,
    relative_strength_features,
)
from app.services.breakouts.range_interactions import (
    range_persistence_interactions,
)
from app.services.breakouts.scoring import score_breakout
from app.services.strength.market_shape import (
    apply_confirmation_rules,
    eligibility_for_setup,
    market_fit_for_setup,
)
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


_RANGE_BACKGROUND_KEYS = (
    "range_position",
    "range_persistence_fast",
    "range_persistence_slow",
    "range_persistence",
    "range_persistence_slope_5d",
    "range_persistence_ratio_10d",
    "range_persistence_self_percentile",
    "range_persistence_global_percentile",
    "range_persistence_sector_percentile",
    "range_persistence_normalized_score",
    "range_persistence_status",
    "canonical_universe_as_of",
    "canonical_universe_version",
    "canonical_universe_status",
    "canonical_universe_member",
)


def _unavailable_range_feature(version: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "range_persistence": None,
        "range_persistence_slope_5d": None,
        "range_persistence_ratio_10d": None,
        "range_persistence_self_percentile": None,
        "range_persistence_global_percentile": None,
        "range_persistence_sector_percentile": None,
        "range_persistence_normalized_score": None,
        "quality": 0.0,
        "version": version,
        "warnings": ["range_persistence_calculation_failed"],
    }


def _safe_range_feature(
    frame: pd.DataFrame,
    *,
    version: str,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        return compute_range_persistence(frame, version=version, **kwargs)
    except Exception:
        return _unavailable_range_feature(version)


def _aware_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return fallback
    return parsed if parsed.tzinfo is not None else fallback


def _price_provenance(snapshot: Any) -> dict[str, Any]:
    """Serialize one PriceDataSnapshot without changing its market-data clock."""

    if snapshot is None:
        return {}

    def iso(name: str) -> str | None:
        value = getattr(snapshot, name, None)
        return value.isoformat() if isinstance(value, datetime) else None

    data_through = iso("data_through") or iso("raw_as_of")
    return {
        "requested_at": iso("requested_at"),
        "received_at": iso("received_at"),
        "data_through": data_through,
        "raw_as_of": data_through,
        "feature_cutoff_at": iso("feature_cutoff_at"),
        "source": getattr(snapshot, "source", None),
        "adjustment": getattr(snapshot, "adjustment", None),
        "completeness": getattr(snapshot, "completeness", None),
        "quality": getattr(snapshot, "quality", None),
        "warnings": list(getattr(snapshot, "warnings", ()) or ()),
    }


def _attach_price_provenance(
    features: dict[str, Any],
    *,
    daily_snapshot: Any,
    intraday_snapshot: Any,
) -> None:
    daily = _price_provenance(daily_snapshot)
    intraday = _price_provenance(intraday_snapshot)
    features["price_data_provenance"] = {"daily": daily, "intraday": intraday}
    if intraday:
        for field in (
            "requested_at",
            "received_at",
            "data_through",
            "raw_as_of",
            "feature_cutoff_at",
            "source",
            "adjustment",
            "completeness",
        ):
            if intraday.get(field) is not None:
                features[field] = intraday[field]
        features["price_data_quality"] = intraday.get("quality")
        features["price_data_warnings"] = intraday.get("warnings") or []


def _event_time_semantics(
    prior: BreakoutEvent | Mapping[str, Any] | None,
    *,
    first_seen_at: datetime,
    observed_at: datetime,
    transitions: Sequence[Mapping[str, Any]],
) -> tuple[datetime | None, datetime, datetime]:
    """Return triggered, state-change and stable compatibility timestamps."""

    def prior_value(name: str) -> Any:
        if prior is None:
            return None
        if isinstance(prior, Mapping):
            return prior.get(name)
        return getattr(prior, name, None)

    triggered_at = None
    prior_triggered = prior_value("triggered_at")
    if prior_triggered is not None:
        triggered_at = _aware_datetime(prior_triggered, first_seen_at)
    elif prior is not None:
        prior_state = str(
            getattr(prior_value("lifecycle_state"), "value", prior_value("lifecycle_state"))
            or ""
        )
        if prior_state not in {
            BreakoutLifecycleState.DISCOVERED.value,
            BreakoutLifecycleState.WATCHING.value,
            BreakoutLifecycleState.EXPIRED.value,
        }:
            triggered_at = _aware_datetime(prior_value("event_at"), first_seen_at)

    if triggered_at is None:
        for transition in transitions:
            to_state = str(
                getattr(transition.get("to_state"), "value", transition.get("to_state"))
                or ""
            )
            if to_state == BreakoutLifecycleState.TRIGGERED.value:
                triggered_at = _aware_datetime(
                    transition.get("evidence_at"), observed_at
                )
                break

    if transitions:
        state_changed_at = observed_at
    else:
        prior_state_changed = prior_value("state_changed_at")
        state_changed_at = (
            _aware_datetime(prior_state_changed, first_seen_at)
            if prior_state_changed is not None
            else _aware_datetime(prior_value("event_at"), first_seen_at)
            if prior is not None and prior_value("event_at") is not None
            else first_seen_at
        )
    return triggered_at, state_changed_at, triggered_at or first_seen_at


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
        self._liquidity_distribution_cache: dict[str, dict[str, Any]] = {}

    def _sector_benchmark(
        self,
        ticker: str,
        provider_sector: str | None,
    ) -> str | None:
        resolver = getattr(self.universe, "sector_benchmark", None)
        if not callable(resolver):
            return None
        try:
            return resolver(ticker, provider_sector)
        except TypeError:
            return resolver(ticker)
        except Exception:
            return None

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
    def _average_dollar_volume(
        daily: pd.DataFrame,
        lookback: int = 20,
    ) -> float | None:
        """Return point-in-time average dollar volume from completed daily bars."""
        return compute_average_dollar_volume(daily, lookback=lookback)["value"]

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
        avg_dollar = BreakoutRadarService._average_dollar_volume(daily)
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
            "relative_strength_confirmation": _finite(
                features.get("relative_strength_confirmation")
            ),
            "average_dollar_volume_quality": _quality(
                avg_dollar, low=1_000_000, high=50_000_000
            ),
            "current_dollar_volume_quality": _quality(
                current_dollar, low=500_000, high=20_000_000
            ),
            "dollar_volume_percentile": _finite(
                features.get("dollar_volume_percentile")
            ),
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

    def _opening_range_confirmation_features(
        self,
        intraday: pd.DataFrame,
        cutoff: TemporalCutoff,
        features: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Count only completed regular-session bars above the opening range."""

        opening_high = _finite(features.get("opening_range_high"))
        atr = _finite(features.get("atr20"))
        if (
            not features.get("opening_range_complete")
            or opening_high is None
            or not isinstance(intraday, pd.DataFrame)
        ):
            return {"hold_bars_above_opening_range": 0}
        visible = trim_intraday_bars(intraday, cutoff)
        if visible.empty:
            return {"hold_bars_above_opening_range": 0}
        local_index = visible.index.tz_convert("America/New_York")
        event_date = cutoff.event_at.astimezone(local_index.tz).date()
        local_minutes = local_index.hour * 60 + local_index.minute
        opening_end = 9 * 60 + 30 + self.settings.opening_range_minutes
        after_opening = visible[
            (pd.Index(local_index.date) == event_date)
            & (local_minutes >= opening_end)
        ]
        threshold = opening_high + max(
            opening_high * self.settings.break_buffer_pct,
            (atr or 0.0) * self.settings.break_buffer_atr,
        )
        hold_bars = 0
        for raw in reversed(
            list(pd.to_numeric(after_opening.get("Close"), errors="coerce"))
        ):
            close = _finite(raw)
            if close is None or close <= threshold:
                break
            hold_bars += 1
        return {"hold_bars_above_opening_range": hold_bars}

    @staticmethod
    def _completed_session_bars(
        intraday: pd.DataFrame,
        cutoff: TemporalCutoff,
    ) -> pd.DataFrame:
        """Return only complete bars from the cutoff's local trading date."""

        if not isinstance(intraday, pd.DataFrame):
            return pd.DataFrame()
        visible = trim_intraday_bars(intraday, cutoff)
        if visible.empty:
            return visible
        local_index = visible.index.tz_convert("America/New_York")
        event_date = cutoff.event_at.astimezone(local_index.tz).date()
        return visible[pd.Index(local_index.date) == event_date]

    def _continuation_bar_evidence(
        self,
        intraday: pd.DataFrame,
        cutoff: TemporalCutoff,
        *,
        event_started_at: datetime,
        prior_last_seen_at: datetime,
        previous_close: float | None,
        invalidation_price: float | None,
        atr: float | None,
    ) -> dict[str, Any]:
        """Aggregate complete bars so a missed scan cannot erase evidence."""

        current = self._completed_session_bars(intraday, cutoff)
        empty = {
            "session_high": None,
            "new_high_since_prior": None,
            "gap_faded_on_complete_bar": False,
            "invalidation_broken_on_complete_bar": False,
            "gap_hold_bars": 0,
        }
        if current.empty:
            return empty

        local_index = current.index.tz_convert("America/New_York")
        bar_ends = local_index + pd.Timedelta(minutes=5)
        started_local = pd.Timestamp(event_started_at).tz_convert(local_index.tz)
        prior_local = pd.Timestamp(prior_last_seen_at).tz_convert(local_index.tz)
        event_bars = current[bar_ends > started_local]
        event_closes = pd.to_numeric(event_bars.get("Close"), errors="coerce")
        completed_after_prior = current[bar_ends > prior_local]
        new_high = _finite(
            pd.to_numeric(completed_after_prior.get("High"), errors="coerce").max()
        )
        new_closes = pd.to_numeric(
            completed_after_prior.get("Close"),
            errors="coerce",
        )
        gap_faded = bool(
            previous_close is not None
            and (new_closes <= previous_close).fillna(False).any()
        )
        invalidation_broken = bool(
            invalidation_price is not None
            and (new_closes < invalidation_price).fillna(False).any()
        )
        gap_hold_bars = 0
        if previous_close is not None:
            hold_threshold = previous_close + max(
                previous_close * self.settings.break_buffer_pct,
                (atr or 0.0) * self.settings.break_buffer_atr,
            )
            for raw in reversed(list(event_closes)):
                close = _finite(raw)
                if close is None or close <= hold_threshold:
                    break
                gap_hold_bars += 1
        return {
            "session_high": new_high,
            "new_high_since_prior": new_high,
            "gap_faded_on_complete_bar": gap_faded,
            "invalidation_broken_on_complete_bar": invalidation_broken,
            "gap_hold_bars": gap_hold_bars,
        }

    async def _intraday_batches(
        self,
        tickers: Sequence[str],
        *,
        cutoff: TemporalCutoff,
    ) -> dict[str, Any]:
        """Load a bounded ticker union without exceeding adapter batch limits."""

        symbols = list(dict.fromkeys(str(item).upper() for item in tickers if item))
        snapshots: dict[str, Any] = {}
        batch_size = min(40, max(1, self.settings.intraday_enrich_limit))
        for start in range(0, len(symbols), batch_size):
            try:
                result = await self.price_data.intraday(
                    symbols[start : start + batch_size],
                    cutoff=cutoff,
                    interval="5m",
                )
            except Exception:
                continue
            snapshots.update(dict(result))
        return snapshots

    def _continue_event(
        self,
        prior_value: Mapping[str, Any],
        *,
        daily_snapshot: Any,
        intraday_snapshot: Any,
        market_daily_snapshot: Any,
        sector_daily_snapshot: Any,
        sector_symbol: str | None,
        liquidity_distribution: Mapping[str, Any],
        cutoff: TemporalCutoff,
        observed_at: datetime,
        observed_session: MarketSession,
        market: MarketShapeSnapshot,
        source_snapshot_id: str,
        versions: Mapping[str, str],
        expired_due: bool,
    ) -> tuple[BreakoutEvent, list[dict[str, Any]], dict[str, Any]]:
        """Re-evaluate one already-published event with completed local bars."""

        prior = BreakoutEvent.model_validate(prior_value)
        prior_features = dict(prior.features or {})
        structure = (
            BreakoutStructure.model_validate(prior.structure)
            if prior.structure is not None
            else None
        )
        origin_setup = (
            prior.origin_setup_type
            or BreakoutSetupType(
                str(prior_features.get("origin_setup_type") or prior.setup_type.value)
            )
        )
        daily = (
            trim_daily_bars(daily_snapshot.frame, cutoff)
            if daily_snapshot is not None
            else pd.DataFrame()
        )
        if expired_due:
            features: dict[str, Any] = {
                "status": "ttl_expired",
                "event_price": None,
                "warnings": ["carryover_ttl_expired"],
            }
        elif intraday_snapshot is None:
            features = {
                "status": "insufficient_data",
                "event_price": None,
                "atr20": compute_atr(daily) if not daily.empty else None,
                "warnings": ["intraday_snapshot_unavailable"],
            }
        else:
            features = compute_feature_snapshot(
                daily=daily_snapshot.frame,
                intraday=intraday_snapshot.frame,
                cutoff=cutoff,
                opening_range_minutes=self.settings.opening_range_minutes,
            )
            features.update(
                self._structure_intraday_features(
                    structure,
                    intraday_snapshot.frame,
                    cutoff,
                    _finite(features.get("atr20")),
                )
            )
            features.update(
                self._opening_range_confirmation_features(
                    intraday_snapshot.frame,
                    cutoff,
                    features,
                )
            )
        _attach_price_provenance(
            features,
            daily_snapshot=daily_snapshot,
            intraday_snapshot=intraday_snapshot,
        )
        features.update(self._base_features(structure))

        if self.settings.range_persistence_mode == "disabled":
            features.update(
                {
                    key: "disabled" if key == "range_persistence_status" else None
                    for key in _RANGE_BACKGROUND_KEYS
                }
            )
        else:
            features.update(
                {
                    key: prior_features.get(key)
                    for key in _RANGE_BACKGROUND_KEYS
                    if key in prior_features
                }
            )

        previous_close = (
            _finite(daily["Close"].iloc[-1])
            if not daily.empty and "Close" in daily.columns
            else _finite(prior_features.get("previous_regular_close"))
        )
        price = _finite(features.get("event_price"))
        atr = _finite(features.get("atr20"))
        resistance_high = (
            structure.resistance_zone.high if structure is not None else None
        )
        invalidation = (
            structure.invalidation_price if structure is not None else None
        )
        bar_evidence = (
            self._continuation_bar_evidence(
                intraday_snapshot.frame,
                cutoff,
                event_started_at=prior.first_seen_at,
                prior_last_seen_at=prior.last_seen_at,
                previous_close=previous_close,
                invalidation_price=invalidation,
                atr=atr,
            )
            if intraday_snapshot is not None and not expired_due
            else {
                "session_high": None,
                "new_high_since_prior": None,
                "gap_faded_on_complete_bar": False,
                "invalidation_broken_on_complete_bar": False,
                "gap_hold_bars": 0,
            }
        )
        prior_high = _finite(prior_features.get("event_high_watermark"))
        session_high = _finite(bar_evidence.get("session_high"))
        high_watermark = max(
            value for value in (session_high, prior_high) if value is not None
        ) if session_high is not None or prior_high is not None else None
        freshness = max(
            0.0,
            min(
                100.0,
                100.0
                * (
                    1.0
                    - max(0.0, (observed_at - prior.first_seen_at).total_seconds())
                    / self.settings.event_ttl_seconds
                ),
            ),
        )
        features.update(
            {
                "origin_setup_type": origin_setup.value,
                "previous_regular_close": previous_close,
                "event_high_watermark": high_watermark,
                "current_price": price,
                "gap_pct": (
                    (price / previous_close - 1.0) * 100.0
                    if price is not None
                    and previous_close is not None
                    and previous_close > 0
                    else None
                ),
                "gap_atr": (
                    (price - previous_close) / atr
                    if price is not None
                    and previous_close is not None
                    and atr is not None
                    and atr > 0
                    else None
                ),
                "event_freshness_score": freshness,
                "carryover_recheck": True,
            }
        )
        relative_features = relative_strength_features(
            daily,
            (
                trim_daily_bars(market_daily_snapshot.frame, cutoff)
                if market_daily_snapshot is not None
                else None
            ),
            (
                trim_daily_bars(sector_daily_snapshot.frame, cutoff)
                if sector_daily_snapshot is not None
                else None
            ),
            sector_symbol=sector_symbol,
            sector_breadth_score=(
                (liquidity_distribution.get("sector_breadth") or {}).get(
                    sector_symbol
                )
                if sector_symbol
                else None
            ),
        )
        features.update(relative_features)
        average_dollar_volume_metric = compute_average_dollar_volume(daily)
        average_dollar_volume = average_dollar_volume_metric["value"]
        features.update(
            {
                "average_dollar_volume": average_dollar_volume,
                "average_dollar_volume_calculation_method": (
                    average_dollar_volume_metric["calculation_method"]
                ),
                "average_dollar_volume_sample_count": average_dollar_volume_metric[
                    "sample_count"
                ],
                "dollar_volume_percentile": percentile_rank(
                    average_dollar_volume,
                    liquidity_distribution.get("values") or {},
                ),
                "liquidity_universe_as_of": liquidity_distribution.get("as_of"),
                "liquidity_universe_version": liquidity_distribution.get(
                    "universe_version"
                ),
                "liquidity_universe_status": liquidity_distribution.get("status"),
                "liquidity_universe_coverage": liquidity_distribution.get(
                    "coverage_ratio"
                ),
                "market_cap_quality": prior_features.get("market_cap_quality"),
                "asset_type_quality": prior_features.get("asset_type_quality"),
            }
        )
        if not daily.empty:
            features.update(self._confirmation_features(features, structure, daily))
        candidate = BreakoutCandidate(
            ticker=prior.ticker,
            exchange=prior.exchange,
            asset_type=prior.asset_type,
            name=prior.name,
            sector=prior.sector,
            price=None,
            previous_regular_close=previous_close,
            provider_timestamp=observed_at,
            source="carryover",
            session=observed_session,
            warnings=["continued_after_discovery_dropout"],
        )
        detection = detect_breakout(
            candidate,
            structure,
            features,
            cutoff,
            self.settings,
        )
        market_payload = market.model_dump(mode="python")
        detection = apply_confirmation_rules(
            detection,
            features,
            market_payload,
            base_confirmation_bars=self.settings.confirmation_bars,
        )
        state = prior.lifecycle_state
        first_seen_at = prior.first_seen_at
        pivot_buffer = max(
            (resistance_high or price or 0.0) * self.settings.break_buffer_pct,
            (atr or 0.0) * self.settings.break_buffer_atr,
        )
        gap_origin = origin_setup in {
            BreakoutSetupType.PREMARKET_GAP,
            BreakoutSetupType.GAP_AND_GO,
            BreakoutSetupType.GAP_HOLD,
            BreakoutSetupType.GAP_FADE,
        }
        gap_faded = bool(
            gap_origin
            and observed_session is MarketSession.REGULAR
            and bar_evidence["gap_faded_on_complete_bar"]
        )
        gap_and_go = bool(
            gap_origin
            and observed_session is MarketSession.REGULAR
            and features.get("opening_range_complete")
            and detection.get("setup_type")
            is BreakoutSetupType.OPENING_RANGE_BREAKOUT
            and detection.get("confirmed")
        )
        gap_holding = bool(
            gap_origin
            and observed_session is MarketSession.REGULAR
            and features.get("opening_range_complete")
            and price is not None
            and previous_close is not None
            and price
            > previous_close
            + max(
                previous_close * self.settings.break_buffer_pct,
                (atr or 0.0) * self.settings.break_buffer_atr,
            )
            and not gap_faded
            and not gap_and_go
        )
        gap_hold_confirmed = bool(
            gap_holding
            and int(bar_evidence["gap_hold_bars"])
            >= self.settings.confirmation_bars
        )
        gap_hold_after_confirmation = bool(
            gap_holding
            and int(bar_evidence["gap_hold_bars"])
            > self.settings.confirmation_bars
        )
        retesting = bool(
            state
            in {
                BreakoutLifecycleState.CONFIRMED,
                BreakoutLifecycleState.HOLDING,
                BreakoutLifecycleState.RETEST_HELD,
                BreakoutLifecycleState.REACCELERATING,
                BreakoutLifecycleState.EXTENDED,
            }
            and price is not None
            and resistance_high is not None
            and price <= resistance_high + pivot_buffer
            and (invalidation is None or price > invalidation)
        )
        retest_held = bool(
            state is BreakoutLifecycleState.RETESTING
            and price is not None
            and resistance_high is not None
            and price > resistance_high + pivot_buffer
        )
        reaccelerating = bool(
            state
            in {
                BreakoutLifecycleState.HOLDING,
                BreakoutLifecycleState.RETEST_HELD,
            }
            and _finite(bar_evidence.get("new_high_since_prior")) is not None
            and prior_high is not None
            and float(bar_evidence["new_high_since_prior"])
            > prior_high + pivot_buffer
            and (_finite(features.get("rvol_time_of_day")) or 0.0) >= 1.0
        )
        failed_below_invalidation = bool(
            bar_evidence["invalidation_broken_on_complete_bar"]
        )
        observation = {
            "origin_setup_type": origin_setup.value,
            "triggered": bool(detection.get("triggered")) or gap_holding,
            "confirmed": bool(detection.get("confirmed")) or gap_hold_confirmed,
            "holding": gap_hold_after_confirmation
            or bool(
                state
                in {
                    BreakoutLifecycleState.CONFIRMED,
                    BreakoutLifecycleState.REACCELERATING,
                }
                and detection.get("confirmed")
                and not retesting
            ),
            "extended": bool(detection.get("extended")) and not retesting,
            "failed": gap_faded or failed_below_invalidation,
            "failure_reason": (
                "gap_filled_on_complete_bar"
                if gap_faded
                else "complete_bar_below_invalidation"
            ),
            "gap_faded": gap_faded,
            "gap_and_go": gap_and_go,
            "gap_holding": gap_holding,
            "retesting": retesting,
            "retest_held": retest_held,
            "reaccelerating": reaccelerating,
            "recovery_reclaimed": bool(
                reaccelerating
                or (
                    str(market.state or "").upper() == "CAPITULATION_RECOVERY"
                    and price is not None
                    and resistance_high is not None
                    and price > resistance_high + pivot_buffer
                )
            ),
            "expired": bool(
                expired_due
                or (observed_at - first_seen_at).total_seconds()
                > self.settings.event_ttl_seconds
            ),
        }
        setup, setup_reason = classify_continuation_setup(
            prior.setup_type,
            state,
            observation,
            detection_setup=detection.get("setup_type"),
            market_state=market.state,
        )
        market_fit = market_fit_for_setup(market_payload, setup)
        market_eligibility = eligibility_for_setup(market_payload, setup)
        features.update(
            {
                "market_shape_state": market.state,
                "market_shape_confidence": market.confidence,
                "market_transition_risk": market.transition_risk,
                "market_fit_score": market_fit,
                "market_eligibility": market_eligibility,
                "market_confirmation_bars_required": detection.get(
                    "market_confirmation_bars_required"
                ),
                "market_single_bar_confirmation_allowed": detection.get(
                    "market_single_bar_confirmation_allowed"
                ),
            }
        )
        event_transitions: list[dict[str, Any]] = []
        for _step in range(4):
            step_observation = dict(observation)
            if state is BreakoutLifecycleState.DISCOVERED:
                step_observation = {"expired": observation["expired"]}
            elif state is BreakoutLifecycleState.WATCHING:
                step_observation["confirmed"] = False
                step_observation["extended"] = False
            result = transition_state(state, step_observation)
            if not result.changed:
                break
            event_transitions.append(
                {
                    "event_id": prior.event_id,
                    "from_state": result.previous_state,
                    "to_state": result.state,
                    "reason": result.reason,
                    "evidence_at": observed_at,
                    "evidence": {
                        "source_snapshot_id": source_snapshot_id,
                        "carryover": True,
                    },
                }
            )
            state = result.state
        range_interaction = range_persistence_interactions(
            {
                **features,
                "status": features.get("range_persistence_status"),
            },
            detection,
            cap=(
                self.settings.range_persistence_breakout_interaction_cap
                if self.settings.range_persistence_breakout_interaction_enabled
                else 0.0
            ),
            ratio_threshold=self.settings.range_persistence_ratio_threshold,
        )
        range_adjustment = float(range_interaction["confirmation_adjustment"])
        range_chase_adjustment = float(range_interaction["chase_adjustment"])
        features["range_persistence_interaction"] = range_interaction
        features.setdefault("warnings", []).extend(
            range_interaction.get("warnings") or []
        )
        sector_fit = (
            _finite(relative_features.get("sector_fit_score"))
            if relative_features.get("sector_fit_score") is not None
            else prior.scores.sector_fit_score
        )
        prior_included = [
            str(item)
            for item in list(
                prior_features.get("strength_included_features") or []
            )
        ]
        if (
            not prior_included
            and prior_features.get("range_persistence_mode_at_score") is None
            and prior.scores.intrinsic_strength_score is not None
            and features.get("range_persistence_status") == "active"
        ):
            # Events written before score provenance was stored are treated
            # conservatively so an existing trend factor cannot be added twice.
            prior_included = ["range_persistence"]
        production_scores = score_breakout(
            features,
            intrinsic_strength=prior.scores.intrinsic_strength_score,
            market_fit=market_fit,
            market_confidence=(
                market.confidence
                if market.status in {"active", "degraded"}
                else None
            ),
            sector_fit=sector_fit,
            strength_included_features=prior_included,
            range_persistence_adjustment=0.0,
            range_persistence_chase_adjustment=0.0,
            score_version=self.settings.scoring_version,
        )
        hypothetical_scores = score_breakout(
            features,
            intrinsic_strength=prior.scores.intrinsic_strength_score,
            market_fit=market_fit,
            market_confidence=(
                market.confidence
                if market.status in {"active", "degraded"}
                else None
            ),
            sector_fit=sector_fit,
            strength_included_features=prior_included,
            range_persistence_adjustment=range_adjustment,
            range_persistence_chase_adjustment=range_chase_adjustment,
            score_version=self.settings.scoring_version,
        )
        scores = (
            hypothetical_scores
            if self.settings.range_persistence_mode == "enabled"
            else production_scores
        )
        features["strength_included_features"] = prior_included
        features["range_persistence_mode_at_score"] = (
            self.settings.range_persistence_mode
        )
        triggered_at, state_changed_at, event_at = _event_time_semantics(
            prior,
            first_seen_at=first_seen_at,
            observed_at=observed_at,
            transitions=event_transitions,
        )
        event = BreakoutEvent(
            event_id=prior.event_id,
            trading_date=prior.trading_date,
            ticker=prior.ticker,
            name=prior.name,
            exchange=prior.exchange,
            asset_type=prior.asset_type,
            sector=prior.sector,
            session=observed_session,
            setup_type=setup,
            origin_setup_type=origin_setup,
            lifecycle_state=state,
            event_at=event_at,
            first_seen_at=first_seen_at,
            triggered_at=triggered_at,
            state_changed_at=state_changed_at,
            last_seen_at=observed_at,
            pivot_id=prior.pivot_id,
            event_price=price,
            event_bar_interval="5m",
            source_snapshot_id=source_snapshot_id,
            previous_state=prior.lifecycle_state,
            transition_reason=(
                event_transitions[-1]["reason"]
                if event_transitions
                else setup_reason
            ),
            structure=structure,
            scores=scores,
            data_quality={
                **prior.data_quality,
                "carryover": True,
                "daily_price_source": (
                    daily_snapshot.source if daily_snapshot is not None else None
                ),
                "intraday_price_source": (
                    intraday_snapshot.source
                    if intraday_snapshot is not None
                    else None
                ),
                "market_shape_status": market.status,
                "market_shape_state": market.state,
                "market_shape_confidence": market.confidence,
                "market_transition_risk": market.transition_risk,
                "market_eligibility": market_eligibility,
                "market_shape_version": market.version,
                "market_shape_rules": dict(market.rules),
                "relative_strength_status": relative_features.get(
                    "relative_strength_status"
                ),
                "relative_strength_market_symbol": relative_features.get(
                    "relative_strength_market_symbol"
                ),
                "relative_strength_sector_symbol": relative_features.get(
                    "relative_strength_sector_symbol"
                ),
                "liquidity_universe_status": liquidity_distribution.get("status"),
                "liquidity_universe_coverage": liquidity_distribution.get(
                    "coverage_ratio"
                ),
            },
            versions={**prior.versions, **dict(versions)},
            features={
                **features,
                "detection": detection,
                "continuation_setup_reason": setup_reason,
                "secondary_setup_type": (
                    detection["setup_type"].value
                    if gap_origin
                    and detection.get("setup_type")
                    is BreakoutSetupType.OPENING_RANGE_BREAKOUT
                    else None
                ),
            },
            warnings=list(
                dict.fromkeys(
                    [
                        *prior.warnings,
                        "carryover_recheck",
                        *list(features.get("warnings") or ()),
                        *list(detection.get("warnings") or ()),
                        *market.warnings,
                    ]
                )
            ),
        )
        range_feature = {
            key: features.get(key)
            for key in _RANGE_BACKGROUND_KEYS
            if key != "range_persistence_status"
        }
        range_feature.update(
            {
                "status": features.get("range_persistence_status") or "unavailable",
                "version": self.settings.range_persistence_version,
            }
        )
        shadow = {
            "trading_date": prior.trading_date,
            "ticker": prior.ticker,
            "event_id": prior.event_id,
            "feature": range_feature,
            "production_score": prior.scores.intrinsic_strength_score,
            # The previous event snapshot does not retain the strength engine's
            # counterfactual factor breakdown.  Keep that field unknown rather
            # than fabricating a score; breakout-level counterfactuals below
            # are still fully reproducible from this continuation scan.
            "hypothetical_score": None,
            "score_version": prior.versions.get(
                "strength_score_version", "unavailable"
            ),
            "feature_version": self.settings.range_persistence_version,
            "breakout_production_priority": production_scores.alert_priority_score,
            "breakout_hypothetical_priority": hypothetical_scores.alert_priority_score,
            "breakout_production_chase_risk": production_scores.chase_risk_score,
            "breakout_hypothetical_chase_risk": hypothetical_scores.chase_risk_score,
            "breakout_interaction": range_interaction,
            "carryover": True,
            "lifecycle_state": event.lifecycle_state,
            "triggered_at": event.triggered_at,
        }
        return event, event_transitions, shadow

    def _secondary_new_event(
        self,
        *,
        primary: BreakoutEvent,
        detection: Mapping[str, Any],
        features: Mapping[str, Any],
        market: MarketShapeSnapshot,
        prior_by_ticker: Mapping[str, Sequence[Mapping[str, Any]]],
        observed_at: datetime,
        observed_trading_date: date,
        source_snapshot_id: str,
        range_feature: Mapping[str, Any],
        strength_snapshot: Any,
        intrinsic: float | None,
        included: list[str],
    ) -> tuple[BreakoutEvent, list[dict[str, Any]], dict[str, Any]]:
        """Create the distinct daily-base event that can coexist with an ORB."""

        setup = detection["setup_type"]
        market_payload = market.model_dump(mode="python")
        market_fit = market_fit_for_setup(market_payload, setup)
        market_eligibility = eligibility_for_setup(market_payload, setup)
        range_interaction = range_persistence_interactions(
            range_feature,
            detection,
            cap=(
                self.settings.range_persistence_breakout_interaction_cap
                if self.settings.range_persistence_breakout_interaction_enabled
                else 0.0
            ),
            ratio_threshold=self.settings.range_persistence_ratio_threshold,
        )
        range_adjustment = float(range_interaction["confirmation_adjustment"])
        range_chase_adjustment = float(range_interaction["chase_adjustment"])
        event_features = {
            **dict(features),
            "origin_setup_type": setup.value,
            "market_shape_state": market.state,
            "market_shape_confidence": market.confidence,
            "market_transition_risk": market.transition_risk,
            "market_fit_score": market_fit,
            "market_eligibility": market_eligibility,
            "market_confirmation_bars_required": detection.get(
                "market_confirmation_bars_required"
            ),
            "market_single_bar_confirmation_allowed": detection.get(
                "market_single_bar_confirmation_allowed"
            ),
            "range_persistence_interaction": range_interaction,
            "detection": dict(detection),
        }
        production_scores = score_breakout(
            event_features,
            intrinsic_strength=intrinsic,
            market_fit=market_fit,
            market_confidence=(
                market.confidence
                if market.status in {"active", "degraded"}
                else None
            ),
            sector_fit=primary.scores.sector_fit_score,
            strength_included_features=included,
            range_persistence_adjustment=0.0,
            range_persistence_chase_adjustment=0.0,
            score_version=self.settings.scoring_version,
        )
        hypothetical_scores = score_breakout(
            event_features,
            intrinsic_strength=intrinsic,
            market_fit=market_fit,
            market_confidence=(
                market.confidence
                if market.status in {"active", "degraded"}
                else None
            ),
            sector_fit=primary.scores.sector_fit_score,
            strength_included_features=included,
            range_persistence_adjustment=range_adjustment,
            range_persistence_chase_adjustment=range_chase_adjustment,
            score_version=self.settings.scoring_version,
        )
        selected_scores = (
            hypothetical_scores
            if self.settings.range_persistence_mode == "enabled"
            else production_scores
        )
        secondary_pivot_id = (
            primary.structure.pivot_id
            if primary.structure is not None
            else f"daily-base-{primary.ticker}-{observed_trading_date.isoformat()}"
        )
        event_id = event_identity(
            trading_date=observed_trading_date,
            ticker=primary.ticker,
            setup_type=setup,
            pivot_id=secondary_pivot_id,
        )
        prior = next(
            (
                dict(item)
                for item in prior_by_ticker.get(primary.ticker, [])
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
        price = _finite(event_features.get("event_price"))
        structure = primary.structure
        invalidation = structure.invalidation_price if structure is not None else None
        observation = {
            "triggered": bool(detection.get("triggered")),
            "confirmed": bool(detection.get("confirmed")),
            "extended": bool(detection.get("extended")),
            "failed": bool(
                price is not None
                and invalidation is not None
                and price < invalidation
            ),
            "failure_reason": "complete_bar_below_invalidation",
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
                    "evidence": {
                        "source_snapshot_id": source_snapshot_id,
                        "parallel_setup": True,
                    },
                }
            )
            state = result.state
        triggered_at, state_changed_at, event_at = _event_time_semantics(
            prior,
            first_seen_at=first_seen_at,
            observed_at=observed_at,
            transitions=event_transitions,
        )
        quality = {
            **primary.data_quality,
            "market_eligibility": market_eligibility,
        }
        event = primary.model_copy(
            deep=True,
            update={
                "event_id": event_id,
                "setup_type": setup,
                "origin_setup_type": setup,
                "lifecycle_state": state,
                "event_at": event_at,
                "first_seen_at": first_seen_at,
                "triggered_at": triggered_at,
                "state_changed_at": state_changed_at,
                "last_seen_at": observed_at,
                "pivot_id": secondary_pivot_id,
                "previous_state": initial_state,
                "transition_reason": (
                    event_transitions[-1]["reason"]
                    if event_transitions
                    else detection.get("transition_reason")
                ),
                "scores": selected_scores,
                "data_quality": quality,
                "features": event_features,
                "warnings": list(
                    dict.fromkeys(
                        [
                            *primary.warnings,
                            *list(detection.get("warnings") or []),
                            *list(range_interaction.get("warnings") or []),
                        ]
                    )
                ),
            },
        )
        strength_shadow = (
            strength_snapshot.factor_breakdown.get("range_persistence_shadow")
            if strength_snapshot is not None
            else None
        )
        shadow = {
            "trading_date": observed_trading_date,
            "ticker": primary.ticker,
            "event_id": event_id,
            "feature": dict(range_feature),
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
            "breakout_production_chase_risk": production_scores.chase_risk_score,
            "breakout_hypothetical_chase_risk": hypothetical_scores.chase_risk_score,
            "breakout_interaction": range_interaction,
        }
        return event, event_transitions, shadow

    async def build_snapshot(
        self,
        discovery: Any,
        clock_snapshot: Any = None,
        *,
        as_of: datetime | None = None,
        session: MarketSession | None = None,
        trading_date: date | None = None,
        previous_events: Mapping[str, list[Mapping[str, Any]]] | None = None,
        carryover_events: Sequence[Mapping[str, Any]] | None = None,
        expired_due_event_ids: Sequence[str] | None = None,
        carryover_has_more: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        observed_at = as_of or getattr(discovery, "as_of", None)
        observed_session = session or getattr(discovery, "session", None)
        if observed_at is None or observed_session is None:
            raise ValueError("scan requires as_of and session")
        observed_trading_date = trading_date or observed_at.astimezone(
            ZoneInfo("America/New_York")
        ).date()
        cutoff = self._cutoff(observed_at, observed_session)
        candidates = [
            item if isinstance(item, BreakoutCandidate) else BreakoutCandidate.model_validate(item)
            for item in list(getattr(discovery, "candidates", ()) or ())[
                : self.settings.provider_result_limit
            ]
        ]
        raw_carryovers = list(carryover_events or ())
        if len(raw_carryovers) > 200:
            raise ValueError("at most 200 carryover events can be evaluated")
        carryovers: list[Mapping[str, Any]] = []
        for value in raw_carryovers:
            event = BreakoutEvent.model_validate(value)
            if (
                event.lifecycle_state
                in {
                    BreakoutLifecycleState.FAILED,
                    BreakoutLifecycleState.EXPIRED,
                }
                or event.first_seen_at > observed_at
                or event.last_seen_at > observed_at
            ):
                continue
            carryovers.append(event.model_dump(mode="python"))
        due_ids = {str(value) for value in (expired_due_event_ids or ())}
        gap_carryover_tickers = {
            str(item.get("ticker") or "").strip().upper()
            for item in carryovers
            if str(item.get("trading_date") or "")
            == observed_trading_date.isoformat()
            if str(
                item.get("origin_setup_type")
                or (item.get("features") or {}).get("origin_setup_type")
                or item.get("setup_type")
                or ""
            )
            in {
                BreakoutSetupType.PREMARKET_GAP.value,
                BreakoutSetupType.GAP_AND_GO.value,
                BreakoutSetupType.GAP_HOLD.value,
                BreakoutSetupType.GAP_FADE.value,
            }
        }
        live_carryover_tickers = {
            str(item.get("ticker") or "").strip().upper()
            for item in carryovers
            if str(item.get("event_id") or "") not in due_ids
        }
        if not candidates and not carryovers:
            return {
                "events": [],
                "structures": [],
                "transitions": [],
                "range_persistence_shadow": [],
                "liquidity_filter_results": [],
                "source_status": {"discovery": getattr(discovery, "status", "unavailable")},
                "warnings": ["carryover_truncated"] if carryover_has_more else [],
            }
        stage_two = [
            candidate
            for candidate in candidates
            if candidate.ticker not in gap_carryover_tickers
        ][: self.settings.daily_enrich_limit]
        discovery_tickers = [item.ticker for item in stage_two]
        distribution_key = "|".join(
            [
                completed_daily_session(cutoff).isoformat(),
                str(getattr(self.universe, "version", "unknown")),
                self.settings.range_persistence_version,
                str(self.settings.range_persistence_length),
                str(self.settings.range_persistence_fast_length),
                str(self.settings.range_persistence_slope_days),
                str(self.settings.range_persistence_ratio_window),
                str(self.settings.range_persistence_ratio_threshold),
                str(self.settings.range_persistence_min_history_multiplier),
            ]
        )
        liquidity_key = "|".join(
            [
                completed_daily_session(cutoff).isoformat(),
                str(getattr(self.universe, "version", "unknown")),
                "dollar-volume-v1",
            ]
        )
        cached_distribution = self._range_distribution_cache.get(distribution_key)
        cached_liquidity = self._liquidity_distribution_cache.get(liquidity_key)
        if self.settings.range_persistence_mode == "disabled":
            cached_distribution = {
                "as_of": completed_daily_session(cutoff).isoformat(),
                "universe_version": getattr(self.universe, "version", "unknown"),
                "members": frozenset(),
                "member_values": {},
                "global": (),
                "sectors": {},
                "coverage_ratio": 0.0,
                "status": "disabled",
            }
        needs_canonical_history = (
            cached_liquidity is None
            or (
                self.settings.range_persistence_mode != "disabled"
                and cached_distribution is None
            )
        )
        if needs_canonical_history:
            try:
                canonical_tickers = list(
                    await self.universe.tickers(as_of=observed_at)
                )
            except Exception:
                canonical_tickers = []
        else:
            canonical_tickers = []
        sector_benchmarks = {
            benchmark
            for benchmark in (
                self._sector_benchmark(candidate.ticker, candidate.sector)
                for candidate in stage_two
            )
            if benchmark
        }
        sector_benchmarks.update(
            benchmark
            for benchmark in (
                self._sector_benchmark(
                    str(item.get("ticker") or ""),
                    str(item.get("sector") or "") or None,
                )
                for item in carryovers
            )
            if benchmark
        )
        daily_symbols = list(
            dict.fromkeys(
                [
                    *discovery_tickers,
                    *sorted(live_carryover_tickers),
                    *canonical_tickers,
                    "SPY",
                    *sorted(sector_benchmarks),
                ]
            )
        )
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
        eligible_stage_two: list[BreakoutCandidate] = []
        visible_daily: dict[str, pd.DataFrame] = {}
        for candidate in stage_two:
            daily_snapshot = daily_map.get(candidate.ticker)
            if daily_snapshot is None:
                continue
            daily = trim_daily_bars(daily_snapshot.frame, cutoff)
            average_dollar_volume = self._average_dollar_volume(daily)
            if (
                average_dollar_volume is None
                or average_dollar_volume < self.settings.min_avg_dollar_volume
            ):
                continue
            eligible_stage_two.append(candidate)
            visible_daily[candidate.ticker] = daily

        eligible_tickers = [item.ticker for item in eligible_stage_two]
        strength_map = {}
        if eligible_tickers:
            try:
                from_daily = getattr(self.strength, "score_from_daily_snapshots", None)
                if callable(from_daily):
                    strength_snapshots = {
                        ticker: daily_map[ticker]
                        for ticker in [*eligible_tickers, "SPY"]
                        if ticker in daily_map
                    }
                    strength_map = await from_daily(
                        eligible_tickers,
                        snapshots=strength_snapshots,
                        as_of=observed_at,
                        include_options=False,
                        range_mode=self.settings.range_persistence_mode,
                        range_trend_weight=(
                            self.settings.range_persistence_trend_family_weight
                        ),
                        range_final_cap=self.settings.range_persistence_final_weight_cap,
                    )
                else:
                    strength_map = await self.strength.score_ticker_set(
                        eligible_tickers,
                        as_of=observed_at,
                        include_options=False,
                    )
            except Exception:
                strength_map = {}

        if cached_liquidity is None:
            liquidity_values: dict[str, float] = {}
            sector_breadth_observations: dict[str, list[bool]] = {}
            for ticker in canonical_tickers:
                snapshot = daily_map.get(ticker)
                if snapshot is None:
                    continue
                canonical_daily = trim_daily_bars(snapshot.frame, cutoff)
                value = self._average_dollar_volume(
                    canonical_daily
                )
                if value is not None:
                    liquidity_values[ticker] = value
                benchmark = self._sector_benchmark(ticker, None)
                if (
                    benchmark
                    and "Close" in canonical_daily
                    and len(canonical_daily) >= 50
                ):
                    closes = pd.to_numeric(
                        canonical_daily["Close"],
                        errors="coerce",
                    ).dropna()
                    if len(closes) >= 50:
                        sector_breadth_observations.setdefault(
                            benchmark,
                            [],
                        ).append(float(closes.iloc[-1]) > float(closes.tail(50).mean()))
            liquidity_minimum = max(30, int(len(canonical_tickers) * 0.60))
            liquidity_coverage = len(liquidity_values) / max(
                len(canonical_tickers), 1
            )
            cached_liquidity = {
                "as_of": completed_daily_session(cutoff).isoformat(),
                "universe_version": getattr(self.universe, "version", "unknown"),
                "members": frozenset(canonical_tickers),
                "values": liquidity_values,
                "sector_breadth": {
                    benchmark: round(
                        sum(observations) / len(observations) * 100.0,
                        4,
                    )
                    for benchmark, observations in sector_breadth_observations.items()
                    if len(observations) >= 5
                },
                "coverage_ratio": liquidity_coverage,
                "status": (
                    "active"
                    if len(liquidity_values) >= liquidity_minimum
                    else "degraded"
                    if liquidity_values
                    else "unavailable"
                ),
            }
            self._liquidity_distribution_cache[liquidity_key] = cached_liquidity
            while len(self._liquidity_distribution_cache) > 5:
                self._liquidity_distribution_cache.pop(
                    next(iter(self._liquidity_distribution_cache))
                )

        if cached_distribution is None:
            global_values: list[float] = []
            sector_values: dict[str, list[float]] = {}
            member_values: dict[str, float] = {}
            for ticker in canonical_tickers:
                snapshot = daily_map.get(ticker)
                if snapshot is None:
                    continue
                feature = _safe_range_feature(
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
        for candidate in eligible_stage_two:
            daily_snapshot = daily_map[candidate.ticker]
            daily = visible_daily[candidate.ticker]
            structure = detect_base(candidate.ticker, daily, cutoff, self.settings)
            if structure is not None:
                structures.append(structure)
            range_feature = (
                {
                    "status": "disabled",
                    "range_persistence": None,
                    "range_persistence_slope_5d": None,
                    "range_persistence_ratio_10d": None,
                    "range_persistence_self_percentile": None,
                    "range_persistence_global_percentile": None,
                    "range_persistence_sector_percentile": None,
                    "range_persistence_normalized_score": None,
                    "quality": 0.0,
                    "version": self.settings.range_persistence_version,
                }
                if self.settings.range_persistence_mode == "disabled"
                else _safe_range_feature(
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
        intraday_map = await self._intraday_batches(
            [
                *[item[0].ticker for item in refined],
                *sorted(live_carryover_tickers),
            ],
            cutoff=cutoff,
        )

        events = []
        transitions = []
        shadows = []
        liquidity_filter_results: list[dict[str, Any]] = []
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
                    opening_range_minutes=self.settings.opening_range_minutes,
                )
                features.update(
                    self._structure_intraday_features(
                        structure,
                        intraday_snapshot.frame,
                        cutoff,
                        _finite(features.get("atr20")),
                    )
                )
            _attach_price_provenance(
                features,
                daily_snapshot=daily_snapshot,
                intraday_snapshot=intraday_snapshot,
            )
            current_dollar_volume = _finite(
                features.get("cumulative_dollar_volume")
            )
            current_dollar_minimum = (
                self.settings.premarket_min_dollar_volume
                if observed_session is MarketSession.PREMARKET
                else self.settings.regular_min_dollar_volume
            )
            features["current_dollar_volume_hard_filter"] = {
                "minimum": current_dollar_minimum,
                "value": current_dollar_volume,
                "status": (
                    "unavailable"
                    if current_dollar_volume is None
                    else "passed"
                    if current_dollar_volume >= current_dollar_minimum
                    else "failed"
                ),
                "calculation_method": features.get(
                    "cumulative_dollar_volume_calculation_method"
                )
                or "unavailable",
            }
            liquidity_filter_results.append(
                {
                    "ticker": candidate.ticker,
                    **features["current_dollar_volume_hard_filter"],
                }
            )
            if (
                current_dollar_volume is None
                or current_dollar_volume < current_dollar_minimum
            ):
                continue
            features.update(self._base_features(structure))
            daily = visible_daily[candidate.ticker]
            previous_close = (
                _finite(daily["Close"].iloc[-1])
                if not daily.empty and "Close" in daily
                else None
            )
            sector_symbol = self._sector_benchmark(
                candidate.ticker,
                candidate.sector,
            )
            market_snapshot = daily_map.get("SPY")
            sector_snapshot = daily_map.get(sector_symbol) if sector_symbol else None
            relative_features = relative_strength_features(
                daily,
                (
                    trim_daily_bars(market_snapshot.frame, cutoff)
                    if market_snapshot is not None
                    else None
                ),
                (
                    trim_daily_bars(sector_snapshot.frame, cutoff)
                    if sector_snapshot is not None
                    else None
                ),
                sector_symbol=sector_symbol,
                sector_breadth_score=(
                    (cached_liquidity.get("sector_breadth") or {}).get(
                        sector_symbol
                    )
                    if sector_symbol
                    else None
                ),
            )
            average_dollar_volume_metric = compute_average_dollar_volume(daily)
            average_dollar_volume = average_dollar_volume_metric["value"]
            features.update(
                {
                    "current_price": _finite(features.get("event_price"))
                    or candidate.price,
                    "previous_regular_close": previous_close,
                    "gap_pct": (
                        (
                            float(features["event_price"])
                            / previous_close
                            - 1.0
                        )
                        * 100.0
                        if _finite(features.get("event_price")) is not None
                        and previous_close is not None
                        and previous_close > 0
                        else None
                    ),
                    "gap_atr": (
                        (
                            float(features["event_price"])
                            - previous_close
                        )
                        / float(features["atr20"])
                        if _finite(features.get("event_price")) is not None
                        and previous_close is not None
                        and _finite(features.get("atr20")) is not None
                        and float(features["atr20"]) > 0
                        else None
                    ),
                    "average_dollar_volume": average_dollar_volume,
                    "average_dollar_volume_calculation_method": (
                        average_dollar_volume_metric["calculation_method"]
                    ),
                    "average_dollar_volume_sample_count": (
                        average_dollar_volume_metric["sample_count"]
                    ),
                    "provider_candidate_liquidity": {
                        "current_dollar_volume": features.get(
                            "cumulative_dollar_volume"
                        ),
                        "calculation_method": features.get(
                            "cumulative_dollar_volume_calculation_method"
                        ),
                    },
                    "market_cap_quality": _quality(
                        candidate.provider_market_cap,
                        low=200_000_000,
                        high=10_000_000_000,
                    ),
                    "asset_type_quality": {
                        "common_stock": 100.0,
                        "adr": 95.0,
                        "etf": 90.0,
                    }.get(candidate.asset_type.value),
                    "dollar_volume_percentile": percentile_rank(
                        average_dollar_volume,
                        cached_liquidity["values"],
                    ),
                    "liquidity_universe_as_of": cached_liquidity["as_of"],
                    "liquidity_universe_version": cached_liquidity[
                        "universe_version"
                    ],
                    "liquidity_universe_status": cached_liquidity["status"],
                    "liquidity_universe_coverage": cached_liquidity[
                        "coverage_ratio"
                    ],
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
                    "range_position": range_feature.get("range_position"),
                    "range_persistence_fast": range_feature.get(
                        "range_persistence_fast"
                    ),
                    "range_persistence_slow": range_feature.get(
                        "range_persistence_slow"
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
            features.update(relative_features)
            features.update(
                self._confirmation_features(
                    features,
                    structure,
                    daily,
                )
            )
            detection = detect_breakout(
                candidate,
                structure,
                features,
                cutoff,
                self.settings,
            )
            market_payload = market.model_dump(mode="python")
            detection = apply_confirmation_rules(
                detection,
                features,
                market_payload,
                base_confirmation_bars=self.settings.confirmation_bars,
            )
            secondary_detection = detection.pop("secondary_detection", None)
            if isinstance(secondary_detection, Mapping):
                secondary_detection = apply_confirmation_rules(
                    secondary_detection,
                    features,
                    market_payload,
                    base_confirmation_bars=self.settings.confirmation_bars,
                )
            setup = detection["setup_type"]
            market_fit = market_fit_for_setup(market_payload, setup)
            market_eligibility = eligibility_for_setup(market_payload, setup)
            features.update(
                {
                    "market_shape_state": market.state,
                    "market_shape_confidence": market.confidence,
                    "market_transition_risk": market.transition_risk,
                    "market_fit_score": market_fit,
                    "market_eligibility": market_eligibility,
                    "market_confirmation_bars_required": detection.get(
                        "market_confirmation_bars_required"
                    ),
                    "market_single_bar_confirmation_allowed": detection.get(
                        "market_single_bar_confirmation_allowed"
                    ),
                }
            )
            strength_snapshot = strength_map.get(candidate.ticker)
            strength_scope_valid = bool(
                strength_snapshot is not None
                and strength_snapshot.score_scope == "intrinsic"
            )
            included = (
                strength_snapshot.included_features if strength_scope_valid else []
            )
            features["strength_included_features"] = list(included)
            features["range_persistence_mode_at_score"] = (
                self.settings.range_persistence_mode
            )
            intrinsic = strength_snapshot.score if strength_scope_valid else None
            if strength_snapshot is not None and not strength_scope_valid:
                features.setdefault("warnings", []).append(
                    "strength_score_scope_not_intrinsic"
                )
            sector_fit = _finite(relative_features.get("sector_fit_score"))
            range_interaction = range_persistence_interactions(
                range_feature,
                detection,
                cap=(
                    self.settings.range_persistence_breakout_interaction_cap
                    if self.settings.range_persistence_breakout_interaction_enabled
                    else 0.0
                ),
                ratio_threshold=self.settings.range_persistence_ratio_threshold,
            )
            range_adjustment = float(
                range_interaction["confirmation_adjustment"]
            )
            range_chase_adjustment = float(
                range_interaction["chase_adjustment"]
            )
            features["range_persistence_interaction"] = range_interaction
            features.setdefault("warnings", []).extend(
                range_interaction.get("warnings") or []
            )
            production_scores = score_breakout(
                features,
                intrinsic_strength=intrinsic,
                market_fit=market_fit,
                market_confidence=(
                    market.confidence
                    if market.status in {"active", "degraded"}
                    else None
                ),
                sector_fit=sector_fit,
                strength_included_features=included,
                range_persistence_adjustment=0.0,
                range_persistence_chase_adjustment=0.0,
                score_version=self.settings.scoring_version,
            )
            hypothetical_scores = score_breakout(
                features,
                intrinsic_strength=intrinsic,
                market_fit=market_fit,
                market_confidence=(
                    market.confidence
                    if market.status in {"active", "degraded"}
                    else None
                ),
                sector_fit=sector_fit,
                strength_included_features=included,
                range_persistence_adjustment=range_adjustment,
                range_persistence_chase_adjustment=range_chase_adjustment,
                score_version=self.settings.scoring_version,
            )
            scores = (
                hypothetical_scores
                if self.settings.range_persistence_mode == "enabled"
                else production_scores
            )
            features["origin_setup_type"] = setup.value
            features["event_high_watermark"] = _finite(features.get("high"))
            if setup is BreakoutSetupType.OPENING_RANGE_BREAKOUT:
                opening_high = _finite(features.get("opening_range_high"))
                pivot_id = "-".join(
                    [
                        "orb",
                        candidate.ticker,
                        observed_trading_date.isoformat(),
                        f"{opening_high:.6f}" if opening_high is not None else "unknown",
                    ]
                )
            elif structure is not None:
                pivot_id = structure.pivot_id
            else:
                pivot_id = (
                    f"{setup.value.lower()}-{candidate.ticker}-"
                    f"{observed_trading_date.isoformat()}"
                )
            event_id = event_identity(
                trading_date=observed_trading_date,
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
            price = _finite(features.get("event_price"))
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
            triggered_at, state_changed_at, event_at = _event_time_semantics(
                prior,
                first_seen_at=first_seen_at,
                observed_at=observed_at,
                transitions=event_transitions,
            )
            event = BreakoutEvent(
                event_id=event_id,
                trading_date=observed_trading_date,
                ticker=candidate.ticker,
                name=candidate.name,
                exchange=candidate.exchange,
                asset_type=candidate.asset_type,
                sector=candidate.sector,
                session=observed_session,
                setup_type=setup,
                origin_setup_type=setup,
                lifecycle_state=state,
                event_at=event_at,
                first_seen_at=first_seen_at,
                triggered_at=triggered_at,
                state_changed_at=state_changed_at,
                last_seen_at=observed_at,
                pivot_id=pivot_id,
                event_price=_finite(features.get("event_price")),
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
                        "active" if strength_scope_valid else "unavailable"
                    ),
                    "strength_score_scope": (
                        strength_snapshot.score_scope
                        if strength_snapshot is not None
                        else None
                    ),
                    "relative_strength_status": relative_features.get(
                        "relative_strength_status"
                    ),
                    "relative_strength_market_symbol": relative_features.get(
                        "relative_strength_market_symbol"
                    ),
                    "relative_strength_sector_symbol": relative_features.get(
                        "relative_strength_sector_symbol"
                    ),
                    "liquidity_universe_status": cached_liquidity["status"],
                    "liquidity_universe_coverage": cached_liquidity[
                        "coverage_ratio"
                    ],
                    "provider_quality": candidate.quality,
                    "range_persistence_quality": range_feature.get("quality"),
                    "market_shape_status": market.status,
                    "market_shape_state": market.state,
                    "market_shape_confidence": market.confidence,
                    "market_transition_risk": market.transition_risk,
                    "market_eligibility": market_eligibility,
                    "market_shape_version": market.version,
                    "market_shape_rules": dict(market.rules),
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
                    "trading_date": observed_trading_date,
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
                    "breakout_production_chase_risk": production_scores.chase_risk_score,
                    "breakout_hypothetical_chase_risk": hypothetical_scores.chase_risk_score,
                    "breakout_interaction": range_interaction,
                }
            )
            if isinstance(secondary_detection, Mapping):
                secondary_event, secondary_transitions, secondary_shadow = (
                    self._secondary_new_event(
                        primary=event,
                        detection=secondary_detection,
                        features=features,
                        market=market,
                        prior_by_ticker=prior_by_ticker,
                        observed_at=observed_at,
                        observed_trading_date=observed_trading_date,
                        source_snapshot_id=source_snapshot_id,
                        range_feature=range_feature,
                        strength_snapshot=strength_snapshot,
                        intrinsic=intrinsic,
                        included=included,
                    )
                )
                events.append(secondary_event)
                transitions.extend(secondary_transitions)
                shadows.append(secondary_shadow)
        for prior in carryovers:
            ticker = str(prior.get("ticker") or "").strip().upper()
            event_id = str(prior.get("event_id") or "")
            sector_symbol = self._sector_benchmark(
                ticker,
                str(prior.get("sector") or "") or None,
            )
            continued, continued_transitions, continued_shadow = self._continue_event(
                prior,
                daily_snapshot=daily_map.get(ticker),
                intraday_snapshot=intraday_map.get(ticker),
                market_daily_snapshot=daily_map.get("SPY"),
                sector_daily_snapshot=(
                    daily_map.get(sector_symbol) if sector_symbol else None
                ),
                sector_symbol=sector_symbol,
                liquidity_distribution=cached_liquidity,
                cutoff=cutoff,
                observed_at=observed_at,
                observed_session=observed_session,
                market=market,
                source_snapshot_id=source_snapshot_id,
                versions=versions,
                expired_due=event_id in due_ids,
            )
            events.append(continued)
            transitions.extend(continued_transitions)
            shadows.append(continued_shadow)

        # A carryover owns its stable event identity.  This also keeps replayed
        # scans idempotent if the same ticker was present in discovery input.
        events = list({item.event_id: item for item in events}.values())
        shadows = list(
            {
                str(item.get("event_id") or ""): item
                for item in shadows
                if str(item.get("event_id") or "")
            }.values()
        )
        transitions = list(
            {
                (
                    str(item.get("event_id") or ""),
                    str(getattr(item.get("from_state"), "value", item.get("from_state"))),
                    str(getattr(item.get("to_state"), "value", item.get("to_state"))),
                    str(item.get("reason") or ""),
                    str(item.get("evidence_at") or ""),
                ): item
                for item in transitions
            }.values()
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
            "liquidity_filter_results": liquidity_filter_results,
            "source_status": {
                "discovery": str(getattr(discovery, "status", "unknown")),
                "prices": "active" if daily_map else "unavailable",
                "strength": "active" if strength_map else "unavailable",
                "market_shape": market.status,
                "carryover": (
                    "truncated"
                    if carryover_has_more
                    else "active"
                    if carryovers
                    else "empty"
                ),
            },
            "versions": versions,
            "warnings": ["carryover_truncated"] if carryover_has_more else [],
        }
