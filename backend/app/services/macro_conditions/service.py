"""Refresh orchestration and API queries for Optix Macro Conditions.

The refresh path is the only thing that touches the network, and it only ever
runs inside the unified worker. Every HTTP read goes through the query methods
here, which read the local SQLite snapshot and nothing else.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .alignment import AsOfSeries, build_grid, etf_from_rows, series_from_rows
from .calculations import compute_factor_points
from .formatting import (
    format_change,
    format_value,
    round_confidence,
    round_score,
    unit_descriptor,
)
from .market_proxy import (
    BACKFILL_PERIOD,
    INCREMENTAL_PERIOD,
    MarketProxyReader,
    last_completed_trading_day,
)
from .models import (
    CompositeSnapshot,
    FactorSnapshot,
    MacroError,
    ModuleSnapshot,
    SnapshotBundle,
    finite,
    iso_instant,
)
from .registry import (
    ETF_PROXIES,
    FACTORS,
    FACTORS_BY_ID,
    FRED_SERIES,
    MODULES,
    MODULES_BY_ID,
    SCORING_VERSION,
    SERIES_BY_ID,
    SOURCE_ATTRIBUTIONS,
    validate_registry,
)
from .repository import (
    HISTORY_BASIS_BACKFILL,
    HISTORY_BASIS_LOCAL,
    MacroRepository,
)
from .scoring import aggregate_composite, aggregate_modules, score_factor_series


logger = logging.getLogger("optix.macro.service")

#: An incremental refresh re-reads this much recent history so a revision to a
#: recently published observation is captured without replaying eight years.
INCREMENTAL_REVISION_WINDOW_DAYS = 180
#: Overall freshness ceiling for the published composite, set by the weakest
#: registered link (weekly series at 14 calendar days).
OVERALL_STALE_CALENDAR_DAYS = 14
#: How far the newest snapshot date may lag today before the snapshot is stale.
SNAPSHOT_STALE_CALENDAR_DAYS = 7
MAX_DRIVERS = 3
DEFAULT_HISTORY_DAYS = 365
MIN_HISTORY_DAYS = 30
MAX_HISTORY_DAYS = 3650
#: Hard ceiling on the compact block handed to the Market Focus analysis.
MAX_AI_CONTEXT_BYTES = 4096


@dataclass(frozen=True, slots=True)
class MacroServiceConfig:
    enabled: bool
    history_years: int
    score_window_years: int
    funding_ema_days: int
    refresh_times_et: tuple[str, ...]
    manual_refresh_cooldown_seconds: int
    scoring_version: str

    @classmethod
    def from_personal_config(cls, config: Any) -> "MacroServiceConfig":
        macro = config.macro
        return cls(
            enabled=bool(macro.enabled),
            history_years=int(macro.history_years),
            score_window_years=int(macro.score_window_years),
            funding_ema_days=int(macro.funding_ema_days),
            refresh_times_et=tuple(macro.refresh_times_et),
            manual_refresh_cooldown_seconds=int(macro.manual_refresh_cooldown_seconds),
            scoring_version=str(macro.scoring_version),
        )


DEFAULT_CONFIG = MacroServiceConfig(
    enabled=True,
    history_years=8,
    score_window_years=5,
    funding_ema_days=5,
    refresh_times_et=("08:30", "18:30"),
    manual_refresh_cooldown_seconds=300,
    scoring_version=SCORING_VERSION,
)


class MacroConditionsService:
    def __init__(
        self,
        repository: MacroRepository,
        *,
        config: MacroServiceConfig = DEFAULT_CONFIG,
        fred_factory: Callable[[], Any] | None = None,
        proxy: MarketProxyReader | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        validate_registry()
        if config.scoring_version != SCORING_VERSION:
            raise MacroError(
                "macro_store_unavailable",
                "configured scoring version does not match the code",
            )
        self.repository = repository
        self.config = config
        self._fred_factory = fred_factory
        self._proxy = proxy or MarketProxyReader()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self, *, trigger: str = "scheduled") -> dict[str, Any]:
        """Fetch, recompute and atomically publish. Never raises for one bad series."""

        observed = self._clock()
        as_of = iso_instant(observed)
        run_id = f"mcr_{uuid.uuid4().hex}"
        self.repository.initialize()
        coverage = self.repository.series_coverage()
        is_backfill = self._needs_backfill(coverage)
        self.repository.start_sync_run(
            run_id,
            "initial_backfill" if is_backfill else trigger,
            started_at=as_of,
        )
        warnings: list[str] = []
        error_codes: list[str] = []
        series_failed: dict[str, str] = {}
        etf_failed: dict[str, str] = {}
        try:
            client = self._client()
        except MacroError as exc:
            self.repository.finish_sync_run(
                run_id,
                status="failed",
                data_through=None,
                series_succeeded=0,
                series_failed=len(FRED_SERIES),
                error_codes=[exc.code],
                details={"reason": exc.code},
            )
            return {
                "run_id": run_id,
                "status": "failed",
                "error_code": exc.code,
                "published": False,
            }

        history_start = self._history_start(observed)
        cutoff = last_completed_trading_day(observed)
        fetch_start = (
            history_start
            if is_backfill
            else self._incremental_start(coverage, floor=history_start)
        )
        # Assigned up front: an upstream failure must still be able to summarise
        # the run rather than raise on an unbound name.
        fetched: dict[str, Any] = {}
        try:
            with client:
                fetched, series_failed = client.fetch_many(
                    FRED_SERIES,
                    start=fetch_start,
                    end=cutoff,
                )
                for series_id, fetch in fetched.items():
                    self.repository.record_series_revisions(
                        fetch.metadata,
                        fetch.observations,
                        history_basis=(
                            HISTORY_BASIS_BACKFILL if is_backfill else HISTORY_BASIS_LOCAL
                        ),
                        observed_at=as_of,
                    )
        except MacroError as exc:
            error_codes.append(exc.code)
            warnings.append(exc.code)

        etf_rows, etf_failed = self._proxy.read(
            period=BACKFILL_PERIOD if is_backfill else INCREMENTAL_PERIOD,
        )
        for symbol, observations in etf_rows.items():
            self.repository.record_etf_observations(
                observations,
                data_through=cutoff,
                history_basis=(
                    HISTORY_BASIS_BACKFILL if is_backfill else HISTORY_BASIS_LOCAL
                ),
                observed_at=as_of,
            )

        for series_id, code in sorted(series_failed.items()):
            # A series failure is reported as a series failure, never as a
            # database error, and the previously stored rows stay untouched.
            warnings.append(f"series:{series_id}:{code}")
            if code not in error_codes:
                error_codes.append(code)
        for symbol, code in sorted(etf_failed.items()):
            warnings.append(f"etf:{symbol}:{code}")
            if code not in error_codes:
                error_codes.append(code)

        bundle, summary = self.build_snapshot(as_of=as_of, warnings=tuple(warnings))
        published = False
        if bundle is not None and summary["valid_module_count"] >= 0:
            if summary["composite_score"] is None:
                # Without enough valid modules there is no official composite to
                # publish; the previous snapshot stays readable and current.
                if "macro_insufficient_modules" not in error_codes:
                    error_codes.append("macro_insufficient_modules")
            else:
                self.repository.publish(bundle)
                published = True

        status = "succeeded"
        if not published:
            status = "failed" if not fetched else "degraded"
        elif series_failed or etf_failed:
            status = "degraded"

        self.repository.finish_sync_run(
            run_id,
            status=status,
            data_through=summary.get("data_through"),
            series_succeeded=len(fetched),
            series_failed=len(series_failed),
            error_codes=error_codes,
            details={
                "trigger": trigger,
                "backfill": is_backfill,
                "warnings": warnings[:50],
                "factor_rows": summary.get("factor_rows", 0),
                "snapshot_dates": summary.get("snapshot_dates", 0),
            },
        )
        invalidate_read_cache()
        return {
            "run_id": run_id,
            "status": status,
            "published": published,
            "series_succeeded": len(fetched),
            "series_failed": len(series_failed),
            "etf_failed": sorted(etf_failed),
            "composite_score": summary.get("composite_score"),
            "valid_module_count": summary.get("valid_module_count"),
            "data_through": (
                summary["data_through"].isoformat()
                if isinstance(summary.get("data_through"), date)
                else None
            ),
            "warnings": warnings[:50],
            "error_codes": error_codes,
        }

    def _client(self) -> Any:
        if self._fred_factory is not None:
            return self._fred_factory()
        from app.config import get_settings

        from .fred_client import FredClient

        settings = get_settings()
        return FredClient(settings.fred_api_key.get_secret_value())

    def _history_start(self, observed: datetime) -> date:
        return (observed.date() - timedelta(days=366 * self.config.history_years))

    def _incremental_start(
        self,
        coverage: Mapping[str, Mapping[str, Any]],
        *,
        floor: date,
    ) -> date:
        """Only re-read the recent tail plus a bounded revision window.

        The window starts at the *oldest* per-series latest observation, so a
        weekly series that publishes with a lag is still extended.
        """

        latest: list[date] = []
        for entry in coverage.values():
            value = _as_date(entry.get("latest"))
            if value is not None:
                latest.append(value)
        if not latest:
            return floor
        anchor = min(latest) - timedelta(days=INCREMENTAL_REVISION_WINDOW_DAYS)
        return max(floor, anchor)

    def _needs_backfill(self, coverage: Mapping[str, Mapping[str, Any]]) -> bool:
        if not coverage:
            return True
        registered = {spec.series_id for spec in FRED_SERIES if spec.enabled}
        return not registered.issubset(set(coverage))

    # ------------------------------------------------------------------
    # Snapshot construction
    # ------------------------------------------------------------------

    def build_snapshot(
        self,
        *,
        as_of: str,
        warnings: Sequence[str] = (),
    ) -> tuple[Optional[SnapshotBundle], dict[str, Any]]:
        """Recompute every factor, module and the composite from stored rows."""

        observed = self._clock()
        cutoff = last_completed_trading_day(observed)
        start = self._history_start(observed)
        grid = build_grid(start, cutoff)
        empty = {
            "composite_score": None,
            "valid_module_count": 0,
            "data_through": None,
            "factor_rows": 0,
            "snapshot_dates": 0,
        }
        if not grid:
            return None, empty

        series: dict[str, AsOfSeries] = {}
        for spec in FRED_SERIES:
            if not spec.enabled:
                continue
            rows = self.repository.active_series(spec.series_id)
            if not rows:
                continue
            series[spec.series_id] = series_from_rows(
                spec.series_id,
                rows,
                max_stale_calendar_days=spec.max_stale_calendar_days,
            )
        etfs: dict[str, AsOfSeries] = {}
        for spec in ETF_PROXIES:
            rows = self.repository.active_etf(spec.symbol)
            if not rows:
                continue
            etfs[spec.symbol] = etf_from_rows(spec.symbol, rows, grid=grid)
        if not series and not etfs:
            return None, empty

        points = compute_factor_points(grid, series, etfs)
        scored_factors = {
            factor.factor_id: score_factor_series(
                factor.factor_id,
                points[factor.factor_id],
                window_years=self.config.score_window_years,
            )
            for factor in FACTORS
        }
        scored_modules = aggregate_modules(grid, scored_factors)
        composites = aggregate_composite(grid, scored_modules)

        factor_rows: list[FactorSnapshot] = []
        for factor in FACTORS:
            spec = FACTORS_BY_ID[factor.factor_id]
            required = max(1, len(spec.required_series) + len(spec.required_etfs))
            for item in scored_factors[factor.factor_id]:
                unavailable = len(set(item.missing_inputs) | set(item.stale_inputs))
                factor_rows.append(
                    FactorSnapshot(
                        snapshot_date=item.snapshot_date,
                        as_of=as_of,
                        factor_id=item.factor_id,
                        module_id=item.module_id,
                        raw_value=item.raw_value,
                        raw_unit=spec.display_unit,
                        score=round_score(item.score),
                        score_method=item.score_method,
                        score_change_7d=round_score(item.score_change_7d),
                        raw_change_7d=item.raw_change_7d,
                        confidence=round_confidence(
                            max(0.0, (required - unavailable) / required)
                        ),
                        valid_observations=item.valid_observations,
                        history_basis=item.history_basis,
                        data_through=(
                            item.data_through.isoformat() if item.data_through else None
                        ),
                        available_at=item.available_at,
                        status=item.status,
                        scoring_version=SCORING_VERSION,
                        signed_value=item.signed_value,
                        missing_inputs=item.missing_inputs,
                        stale_inputs=item.stale_inputs,
                    )
                )
        module_rows = [
            ModuleSnapshot(
                snapshot_date=item.snapshot_date,
                as_of=as_of,
                module_id=item.module_id,
                score=round_score(item.score),
                score_change_7d=round_score(item.score_change_7d),
                confidence=round_confidence(item.confidence),
                valid_factor_count=item.valid_factor_count,
                total_factor_count=item.total_factor_count,
                data_through=(
                    item.data_through.isoformat() if item.data_through else None
                ),
                available_at=item.available_at,
                status=item.status,
                scoring_version=SCORING_VERSION,
            )
            for module in MODULES
            for item in scored_modules[module.module_id]
        ]
        factor_basis_by_date: dict[date, list[str]] = {}
        for row in factor_rows:
            if row.history_basis:
                factor_basis_by_date.setdefault(row.snapshot_date, []).append(
                    row.history_basis
                )
        from .alignment import combine_history_basis

        composite_rows = [
            CompositeSnapshot(
                snapshot_date=item.snapshot_date,
                as_of=as_of,
                score=round_score(item.score),
                score_change_7d=round_score(item.score_change_7d),
                confidence=round_confidence(item.confidence),
                regime=item.regime,
                valid_module_count=item.valid_module_count,
                data_through=(
                    item.data_through.isoformat() if item.data_through else None
                ),
                available_at=item.available_at,
                history_basis=combine_history_basis(
                    factor_basis_by_date.get(item.snapshot_date, [])
                ),
                status=item.status,
                scoring_version=SCORING_VERSION,
            )
            for item in composites
        ]

        latest = composites[-1]
        self._assert_no_future_reference(as_of, composite_rows)
        summary = {
            "composite_score": round_score(latest.score),
            "valid_module_count": latest.valid_module_count,
            "data_through": latest.data_through,
            "factor_rows": len(factor_rows),
            "snapshot_dates": len(grid),
        }
        bundle = SnapshotBundle(
            as_of=as_of,
            scoring_version=SCORING_VERSION,
            factors=tuple(factor_rows),
            modules=tuple(module_rows),
            composites=tuple(composite_rows),
            warnings=tuple(warnings),
        )
        return bundle, summary

    @staticmethod
    def _assert_no_future_reference(
        as_of: str,
        rows: Sequence[CompositeSnapshot],
    ) -> None:
        """A snapshot may never claim visibility it did not have."""

        for row in rows:
            if row.available_at and row.available_at > as_of:
                raise MacroError(
                    "macro_store_unavailable",
                    "snapshot available_at is after as_of",
                )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _disabled_payload(self, reason: str) -> dict[str, Any]:
        return {
            "status": "disabled",
            "reason": reason,
            "as_of": None,
            "data_through": None,
            "scoring_version": SCORING_VERSION,
            "history_basis": None,
            "composite": None,
            "modules": [],
            "drivers": {"improving": [], "deteriorating": []},
            "warnings": [reason],
            "sources": list(SOURCE_ATTRIBUTIONS),
        }

    def current(self, *, key_configured: bool = True) -> dict[str, Any]:
        if not self.config.enabled:
            return self._disabled_payload("macro_disabled")
        if not key_configured:
            return self._disabled_payload("fred_api_key_missing")
        try:
            composite = self.repository.latest_composite()
        except MacroError:
            payload = self._disabled_payload("macro_store_unavailable")
            payload["status"] = "unavailable"
            return payload
        if composite is None:
            payload = self._disabled_payload("macro_snapshot_unavailable")
            payload["status"] = "unavailable"
            return payload

        snapshot_date = _as_date(composite["snapshot_date"])
        assert snapshot_date is not None
        modules = self.repository.modules_at(snapshot_date)
        factors = self.repository.factors_at(snapshot_date)
        run = self.repository.latest_sync_run()
        warnings = self._warnings_from_run(run)
        status = self._resolve_status(composite, run, factors)
        drivers = self._drivers(factors)
        return {
            "status": status,
            "as_of": composite.get("as_of"),
            "data_through": composite.get("data_through"),
            "scoring_version": SCORING_VERSION,
            "history_basis": composite.get("history_basis"),
            "composite": {
                "score": composite.get("score"),
                "score_change_7d": composite.get("score_change_7d"),
                "confidence": composite.get("confidence"),
                "regime": composite.get("regime"),
                "valid_module_count": composite.get("valid_module_count"),
                "total_module_count": len(MODULES),
                "snapshot_date": composite.get("snapshot_date"),
                "formatted_score": format_value(composite.get("score"), "score"),
            },
            "modules": [self._module_payload(row) for row in modules],
            "drivers": drivers,
            "warnings": warnings,
            "sources": list(SOURCE_ATTRIBUTIONS),
        }

    def _warnings_from_run(self, run: Optional[Mapping[str, Any]]) -> list[str]:
        if not run:
            return []
        codes = run.get("error_codes")
        return [str(code) for code in codes][:20] if isinstance(codes, list) else []

    def _resolve_status(
        self,
        composite: Mapping[str, Any],
        run: Optional[Mapping[str, Any]],
        factors: Sequence[Mapping[str, Any]],
    ) -> str:
        today = self._clock().date()
        snapshot_date = _as_date(composite.get("snapshot_date"))
        data_through = _as_date(composite.get("data_through"))
        if snapshot_date is None:
            return "unavailable"
        if (today - snapshot_date).days > SNAPSHOT_STALE_CALENDAR_DAYS:
            return "stale"
        if data_through is None or (today - data_through).days > OVERALL_STALE_CALENDAR_DAYS:
            return "stale"
        run_status = str((run or {}).get("status") or "")
        if run_status in {"failed"}:
            return "stale"
        if run_status == "degraded":
            return "degraded"
        if any(
            str(row.get("status") or "") in {"missing", "stale"} for row in factors
        ):
            return "degraded"
        if all(row.get("score") is None for row in factors):
            return "insufficient_history"
        return "active"

    def _module_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        spec = MODULES_BY_ID.get(str(row.get("module_id") or ""))
        return {
            "module_id": row.get("module_id"),
            "display_name_zh": spec.display_name_zh if spec else row.get("module_id"),
            "display_name_en": spec.display_name_en if spec else "",
            "score": row.get("score"),
            "score_change_7d": row.get("score_change_7d"),
            "confidence": row.get("confidence"),
            "valid_factor_count": row.get("valid_factor_count"),
            "total_factor_count": row.get("total_factor_count"),
            "minimum_valid_factors": spec.minimum_valid_factors if spec else None,
            "data_through": row.get("data_through"),
            "status": row.get("status"),
            "formatted_score": format_value(row.get("score"), "score"),
        }

    def _drivers(self, factors: Sequence[Mapping[str, Any]]) -> dict[str, list[dict]]:
        ranked = [
            row
            for row in factors
            if finite(row.get("score_change_7d")) is not None
            and finite(row.get("score")) is not None
        ]
        improving = sorted(
            (row for row in ranked if float(row["score_change_7d"]) > 0),
            key=lambda row: (-float(row["score_change_7d"]), str(row["factor_id"])),
        )[:MAX_DRIVERS]
        deteriorating = sorted(
            (row for row in ranked if float(row["score_change_7d"]) < 0),
            key=lambda row: (float(row["score_change_7d"]), str(row["factor_id"])),
        )[:MAX_DRIVERS]
        return {
            "improving": [self._driver_payload(row) for row in improving],
            "deteriorating": [self._driver_payload(row) for row in deteriorating],
        }

    def _driver_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        spec = FACTORS_BY_ID.get(str(row.get("factor_id") or ""))
        return {
            "factor_id": row.get("factor_id"),
            "module_id": row.get("module_id"),
            "display_name_zh": spec.display_name_zh if spec else row.get("factor_id"),
            "score": row.get("score"),
            "score_change_7d": row.get("score_change_7d"),
        }

    def history(self, *, days: int = DEFAULT_HISTORY_DAYS) -> dict[str, Any]:
        bounded = max(MIN_HISTORY_DAYS, min(int(days), MAX_HISTORY_DAYS))
        if not self.config.enabled:
            return {"status": "disabled", "points": [], "scoring_version": SCORING_VERSION}
        today = self._clock().date()
        start = today - timedelta(days=bounded)
        try:
            rows = self.repository.composite_history(start=start, end=today)
        except MacroError:
            return {
                "status": "unavailable",
                "points": [],
                "scoring_version": SCORING_VERSION,
            }
        dates = [_as_date(row["snapshot_date"]) for row in rows]
        module_scores = self.repository.modules_for_dates(
            [value for value in dates if value is not None]
        )
        return {
            "status": "active" if rows else "unavailable",
            "days": bounded,
            "scoring_version": SCORING_VERSION,
            "points": [
                {
                    "date": row["snapshot_date"],
                    "score": row["score"],
                    "confidence": row["confidence"],
                    "regime": row["regime"],
                    "history_basis": row["history_basis"],
                    "module_scores": module_scores.get(str(row["snapshot_date"]), {}),
                }
                for row in rows
            ],
        }

    def module_detail(self, module_id: str) -> dict[str, Any]:
        if module_id not in MODULES_BY_ID:
            raise MacroError("macro_snapshot_unavailable", "unknown module")
        try:
            composite = self.repository.latest_composite()
        except MacroError:
            composite = None
        if composite is None:
            return {
                "status": "unavailable",
                "module_id": module_id,
                "factors": [],
                "scoring_version": SCORING_VERSION,
            }
        snapshot_date = _as_date(composite["snapshot_date"])
        assert snapshot_date is not None
        modules = {
            str(row["module_id"]): row
            for row in self.repository.modules_at(snapshot_date)
        }
        rows = self.repository.factors_at(snapshot_date, module_id=module_id)
        spec = MODULES_BY_ID[module_id]
        return {
            "status": "active",
            "module_id": module_id,
            "display_name_zh": spec.display_name_zh,
            "display_name_en": spec.display_name_en,
            "as_of": composite.get("as_of"),
            "snapshot_date": composite.get("snapshot_date"),
            "scoring_version": SCORING_VERSION,
            "module": (
                self._module_payload(modules[module_id])
                if module_id in modules
                else None
            ),
            "factors": [self._factor_payload(row) for row in rows],
        }

    def _factor_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        factor_id = str(row.get("factor_id") or "")
        spec = FACTORS_BY_ID.get(factor_id)
        unit = str(row.get("raw_unit") or (spec.display_unit if spec else "ratio"))
        return {
            "factor_id": factor_id,
            "module_id": row.get("module_id"),
            "display_name_zh": spec.display_name_zh if spec else factor_id,
            "description_zh": spec.description_zh if spec else "",
            "formula_version": spec.formula_version if spec else "",
            "raw_value": row.get("raw_value"),
            "formatted_value": format_value(row.get("raw_value"), unit),
            "signed_value": row.get("signed_value"),
            "formatted_signed_value": format_value(row.get("signed_value"), unit),
            "unit": unit_descriptor(unit),
            "score": row.get("score"),
            "score_method": row.get("score_method"),
            "direction": spec.direction if spec else None,
            "raw_change_7d": row.get("raw_change_7d"),
            "formatted_raw_change_7d": format_change(row.get("raw_change_7d"), unit),
            "score_change_7d": row.get("score_change_7d"),
            "confidence": row.get("confidence"),
            "valid_observations": row.get("valid_observations"),
            "minimum_history": spec.minimum_history if spec else None,
            "status": row.get("status"),
            "data_through": row.get("data_through"),
            "history_basis": row.get("history_basis"),
            "missing_inputs": row.get("missing_inputs") or [],
            "stale_inputs": row.get("stale_inputs") or [],
            "source": self._factor_sources(spec),
        }

    @staticmethod
    def _factor_sources(spec: Any) -> list[str]:
        if spec is None:
            return []
        sources = [
            SERIES_BY_ID[series_id].source_name
            for series_id in spec.required_series
            if series_id in SERIES_BY_ID
        ]
        if spec.required_etfs:
            sources.append("Option Pro 股票日线数据源")
        return sorted(set(sources))

    def factor_history(
        self,
        factor_id: str,
        *,
        days: int = DEFAULT_HISTORY_DAYS,
    ) -> dict[str, Any]:
        if factor_id not in FACTORS_BY_ID:
            raise MacroError("macro_snapshot_unavailable", "unknown factor")
        bounded = max(MIN_HISTORY_DAYS, min(int(days), MAX_HISTORY_DAYS))
        today = self._clock().date()
        try:
            rows = self.repository.factor_history(
                factor_id,
                start=today - timedelta(days=bounded),
                end=today,
            )
        except MacroError:
            rows = []
        spec = FACTORS_BY_ID[factor_id]
        return {
            "status": "active" if rows else "unavailable",
            "factor_id": factor_id,
            "module_id": spec.module_id,
            "display_name_zh": spec.display_name_zh,
            "unit": unit_descriptor(spec.display_unit),
            "days": bounded,
            "scoring_version": SCORING_VERSION,
            "points": [
                {
                    "date": row["snapshot_date"],
                    "raw_value": row["raw_value"],
                    "signed_value": row["signed_value"],
                    "score": row["score"],
                    "status": row["status"],
                    "data_through": row["data_through"],
                    "history_basis": row["history_basis"],
                }
                for row in rows
            ],
        }

    # ------------------------------------------------------------------
    # AI context
    # ------------------------------------------------------------------

    def ai_context(self, *, key_configured: bool = True) -> Optional[dict[str, Any]]:
        """Compact macro block for the existing Market Focus analysis input.

        Bounded on purpose: seven module scores, at most three drivers per side,
        and no raw series, no eight-year history, and no secret of any kind.
        Returns ``None`` when there is nothing honest to send.
        """

        if not self.config.enabled or not key_configured:
            return None
        payload = self.current(key_configured=key_configured)
        status = str(payload.get("status") or "")
        if status not in {"active", "degraded", "stale"}:
            return None
        composite = payload.get("composite") or {}
        if composite.get("score") is None:
            return None
        module_scores = {
            str(item["module_id"]): item.get("score")
            for item in payload.get("modules") or []
            if item.get("module_id") in MODULES_BY_ID
        }
        block = {
            "status": status,
            "as_of": payload.get("as_of"),
            "data_through": payload.get("data_through"),
            "scoring_version": SCORING_VERSION,
            "history_basis": payload.get("history_basis"),
            "composite_score": composite.get("score"),
            "score_change_7d": composite.get("score_change_7d"),
            "regime": composite.get("regime"),
            "confidence": composite.get("confidence"),
            "module_scores": dict(sorted(module_scores.items())[: len(MODULES)]),
            "top_improving": [
                {
                    "factor_id": item["factor_id"],
                    "display_name_zh": item["display_name_zh"],
                    "score": item["score"],
                    "score_change_7d": item["score_change_7d"],
                }
                for item in (payload.get("drivers") or {}).get("improving", [])[
                    :MAX_DRIVERS
                ]
            ],
            "top_deteriorating": [
                {
                    "factor_id": item["factor_id"],
                    "display_name_zh": item["display_name_zh"],
                    "score": item["score"],
                    "score_change_7d": item["score_change_7d"],
                }
                for item in (payload.get("drivers") or {}).get("deteriorating", [])[
                    :MAX_DRIVERS
                ]
            ],
            "warnings": list(payload.get("warnings") or [])[:5],
        }
        import json

        encoded = json.dumps(block, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_AI_CONTEXT_BYTES:
            block["warnings"] = []
            block["top_improving"] = block["top_improving"][:1]
            block["top_deteriorating"] = block["top_deteriorating"][:1]
        return block


def _as_date(value: object) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            year, month, day = (int(part) for part in value.split("-"))
            return date(year, month, day)
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Cross-process read cache
# ---------------------------------------------------------------------------

_READ_CACHE: dict[str, tuple[tuple[int, int], float, Any]] = {}
_READ_CACHE_TTL_SECONDS = 60.0


def invalidate_read_cache() -> None:
    _READ_CACHE.clear()


def cached_read(
    path: Path,
    key: str,
    producer: Callable[[], Any],
    *,
    now: Callable[[], float] | None = None,
) -> Any:
    """Cache a read keyed on the database file's identity.

    The worker publishes in a different process from the API, so an in-process
    flag cannot be the invalidation signal. The file's ``mtime_ns``/size pair
    changes when a snapshot is published; a short TTL bounds the rest.

    The database runs in WAL mode, so a fresh commit may live only in the
    ``-wal`` sidecar while the main file is untouched (audit P2-25). Stamping
    only the main file meant a manual refresh could be invisible for up to the
    full TTL: the writer had committed, and the reader kept serving the previous
    snapshot. The sidecars are part of the database's identity and are stamped
    with it.
    """

    import time

    clock = now or time.monotonic
    stamps: list[tuple] = []
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            stat = candidate.stat()
        except OSError:
            # A checkpointed database has no sidecar; absence is a real state
            # and has to be distinguishable from any particular size.
            stamps.append((candidate.name, None))
        else:
            stamps.append((candidate.name, stat.st_mtime_ns, stat.st_size))
    stamp = tuple(stamps)
    moment = clock()
    cached = _READ_CACHE.get(key)
    if cached is not None:
        cached_stamp, cached_at, value = cached
        if cached_stamp == stamp and moment - cached_at < _READ_CACHE_TTL_SECONDS:
            return value
    value = producer()
    _READ_CACHE[key] = (stamp, moment, value)
    return value


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_HISTORY_DAYS",
    "INCREMENTAL_REVISION_WINDOW_DAYS",
    "MAX_AI_CONTEXT_BYTES",
    "MAX_HISTORY_DAYS",
    "MIN_HISTORY_DAYS",
    "OVERALL_STALE_CALENDAR_DAYS",
    "SNAPSHOT_STALE_CALENDAR_DAYS",
    "MacroConditionsService",
    "MacroServiceConfig",
    "cached_read",
    "invalidate_read_cache",
]
