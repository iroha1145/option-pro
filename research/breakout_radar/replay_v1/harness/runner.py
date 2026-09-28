"""Drive the production worker over a segment of trading days on frozen data.

One segment = ``warmup_days`` trading days (state rebuild, outputs flagged) followed by
the evaluated days. Every variant gets its own settings, SQLite repository, service and
worker; all variants run in lockstep per scan time and share the memo caches. The worker,
service and repository are production code; the harness only moves the clock, injects
adapters, captures each publication and prunes the repository once a day.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from app.services.breakouts import worker as worker_module
from app.services.breakouts.adapters import ExistingMarketShapeAdapter, ThemeCanonicalUniverseAdapter
from app.services.breakouts.repository import DEFAULT_LOCK_NAME, BreakoutRepository
from app.services.breakouts.research import _load_completed_events_connection, _load_completed_shadows_connection
from app.services.breakouts.service import BreakoutRadarService
from app.services.breakouts.worker import BreakoutWorker
from app.services.market_calendar import early_close_minutes, is_trading_day, market_datetime, previous_trading_day

from .adapters import (
    MarketShapeReplay,
    MemoStrengthAdapter,
    ReplayClock,
    ReplayPriceDataAdapter,
    WorkerSettingsView,
)
from .discovery import DayContext, ReplayDiscoveryProvider
from .memo import Memo
from .settings import assert_production_hash, build_settings, full_hash, production_field_hash, variant_spec
from .stores import DailyStore, DirectoryMetadata, FredStore, MinuteStore, ProductionCandidateMetadata, SharesStore

NY = ZoneInfo("America/New_York")
T1_CYCLE_MINUTES_AFTER_CLOSE = 30


@dataclass
class RunConfig:
    daily_db: Path
    minute_store: Path
    fred: Path
    out: Path
    db_dir: Path
    start: date
    end: date
    variants: list[str] = field(default_factory=lambda: ["baseline"])
    warmup_days: int = 1
    grid: str = "settings"  # or "production"
    export: Path | None = None  # production export: metadata and/or production grid
    directory: Path | None = None
    sic: Path | None = None
    shares: Path | None = None
    metadata_mode: str = "directory"  # or "production"
    market_cap_source: str = "shares"  # shares | production | none
    relvol_scale: float = 1.0
    memo: bool = True
    trim_sessions: bool = True
    on_degraded: str = "raise"  # or "continue"
    full_snapshots: bool = False
    label: str = ""
    # Test knobs only; the production cadence is 5 and 10 minutes.
    regular_step_minutes: int = 5
    premarket_step_minutes: int = 10
    universe: list[str] | None = None


def trading_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    day = start
    while day <= end:
        if is_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def warmup_days_before(start: date, count: int) -> list[date]:
    days: list[date] = []
    day = start
    for _ in range(count):
        day = previous_trading_day(day, max_lookback_days=None)
        days.append(day)
    return sorted(days)


def settings_grid(day: date, *, regular_step: int = 5, premarket_step: int = 10) -> list[tuple[datetime, str]]:
    """The replay cadence (DATA_SPEC section 2): fixed grid, T1 cycle after the close."""

    close = early_close_minutes(day) or 16 * 60
    times: list[tuple[datetime, str]] = []
    minute = 4 * 60 + premarket_step
    while minute < 9 * 60 + 30:
        times.append((market_datetime(day, minute), "premarket"))
        minute += premarket_step
    minute = 9 * 60 + 30 + regular_step
    while minute < close:
        times.append((market_datetime(day, minute), "regular"))
        minute += regular_step
    times.append((market_datetime(day, close + T1_CYCLE_MINUTES_AFTER_CLOSE), "t1"))
    return times


def production_grid(export_path: Path) -> dict[date, list[tuple[datetime, str]]]:
    """Completed production scans by ET day, plus the same after-close T1 cycle."""

    by_day: dict[date, list[tuple[datetime, str]]] = {}
    columns: list[str] = []
    current = None
    with gzip.open(export_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if isinstance(obj, dict) and "table" in obj:
                current = obj["table"]
                columns = obj["columns"]
                continue
            if isinstance(obj, dict) and obj.get("end"):
                break
            if current != "breakout_scan_runs":
                continue
            row = dict(zip(columns, obj))
            if row.get("status") != "completed" or row.get("session") not in {"regular", "premarket"}:
                continue
            stamp = datetime.fromisoformat(str(row["scheduled_at"]).replace("Z", "+00:00"))
            by_day.setdefault(stamp.astimezone(NY).date(), []).append((stamp, str(row["session"])))
    for day, items in by_day.items():
        items.sort()
        close = early_close_minutes(day) or 16 * 60
        items.append((market_datetime(day, close + T1_CYCLE_MINUTES_AFTER_CLOSE), "t1"))
    return by_day


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    features = event.get("features") or {}
    scores = event.get("scores") or {}
    quality = event.get("data_quality") or {}
    provenance = (features.get("price_data_provenance") or {}).get("intraday") or {}
    hard_filter = features.get("current_dollar_volume_hard_filter") or {}
    return {
        "event_id": event.get("event_id"),
        "ticker": event.get("ticker"),
        "setup_type": _value(event.get("setup_type")),
        "origin_setup_type": _value(event.get("origin_setup_type")),
        "lifecycle_state": _value(event.get("lifecycle_state")),
        "previous_state": _value(event.get("previous_state")),
        "transition_reason": event.get("transition_reason"),
        "trading_date": str(event.get("trading_date")),
        "event_at": _iso(event.get("event_at")),
        "first_seen_at": _iso(event.get("first_seen_at")),
        "triggered_at": _iso(event.get("triggered_at")),
        "state_changed_at": _iso(event.get("state_changed_at")),
        "last_seen_at": _iso(event.get("last_seen_at")),
        "event_price": event.get("event_price"),
        "pivot_id": event.get("pivot_id"),
        "carryover": bool(quality.get("carryover")),
        "discovery_source": quality.get("discovery_source"),
        "scores": {
            key: scores.get(key)
            for key in (
                "alert_priority_score", "breakout_quality_score", "intrinsic_strength_score",
                "base_quality_score", "breakout_confirmation_score", "liquidity_quality_score",
                "chase_risk_score", "sector_fit_score", "market_fit_score", "data_confidence_score",
            )
        },
        "features": {
            key: features.get(key)
            for key in (
                "atr20", "vwap", "rvol_time_of_day", "comparison_sessions", "opening_range_high",
                "opening_range_low", "opening_range_complete", "cumulative_dollar_volume",
                "average_dollar_volume", "market_shape_state", "market_eligibility",
                "hold_bars_above_pivot", "hold_bars_above_opening_range", "close_location_value",
                "event_freshness_score", "range_persistence_status", "status",
            )
        },
        "intraday_source": provenance.get("source"),
        "current_dollar_volume_filter": hard_filter.get("status"),
        "warnings": list(event.get("warnings") or []),
    }


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


def _iso(item: Any) -> Any:
    if isinstance(item, datetime):
        return item.astimezone(timezone.utc).isoformat()
    return item


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(type(value).__name__)


def _dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"), allow_nan=False)


class _VariantRun:
    """Everything one variant owns: settings, repository, provider, service, worker, lease."""

    def __init__(self, name: str, config: RunConfig, clock: ReplayClock, shared: dict[str, Any]) -> None:
        self.name = name
        self.config = config
        self.db_path = config.db_dir / f"{name.replace('+', '_')}.sqlite"
        self.settings = build_settings(name, self.db_path)
        if name == "baseline":
            assert_production_hash(self.settings)
        self.production_hash = production_field_hash(self.settings)
        self.full_hash = full_hash(self.settings)
        self.research_overrides = self.settings.research_overrides
        self.repository = BreakoutRepository(self.db_path, clock=lambda: clock.now)
        self.repository.initialize()
        inject = "hybrid_otc" in variant_spec_parts(name)
        self.provider = ReplayDiscoveryProvider(
            self.settings,
            minute_store=shared["minute_store"],
            daily_store=shared["daily_store"],
            metadata=shared["metadata"],
            shares=shared["shares"],
            market_cap_source=config.market_cap_source,
            relvol_scale=config.relvol_scale,
            inject_production_otc=inject,
            day_contexts=shared["day_contexts"],
            universe=set(config.universe) if config.universe else None,
        )
        self.strength = MemoStrengthAdapter(cache=shared["strength_cache"])
        self.service = BreakoutRadarService(
            self.settings,
            price_data=ReplayPriceDataAdapter(
                shared["daily_store"], shared["minute_store"], trim_sessions=config.trim_sessions
            ),
            strength=self.strength,
            market_shape=ExistingMarketShapeAdapter(),
            universe=ThemeCanonicalUniverseAdapter(),
        )
        self.worker = BreakoutWorker(
            WorkerSettingsView(self.settings),
            self.repository,
            provider=self.provider,
            scan_service=self.service,
            clock=clock.market_clock(),
            owner_id=f"replay-{name}",
        )
        self.lease = self.repository.acquire_lock(
            DEFAULT_LOCK_NAME, self.worker.owner_id, self.worker.lease_ttl_seconds, clock.now
        )
        if self.lease is None:
            raise RuntimeError(f"{name}: could not acquire the repository lease")
        self.captured: dict[str, Any] | None = None
        original_publish = self.repository.publish_scan

        def publish_scan(scan_id, snapshot, *args, **kwargs):
            self.captured = dict(snapshot) if isinstance(snapshot, dict) else snapshot
            return original_publish(scan_id, snapshot, *args, **kwargs)

        self.repository.publish_scan = publish_scan  # type: ignore[assignment]
        self.ledger_dir = config.out / name.replace("+", "_") / "ledger"
        self.snapshot_dir = config.out / name.replace("+", "_") / "snapshots"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        if config.full_snapshots:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.day_records: list[dict[str, Any]] = []
        self.day_snapshots: list[dict[str, Any]] = []
        self.degraded: list[dict[str, Any]] = []
        self.truncated_days: set[str] = set()
        self.scan_count = 0

    def heartbeat(self, now: datetime) -> None:
        """Renew the 90-second lease at every clock move.

        The replay clock jumps by whole scan intervals, so the lease has usually
        expired between two cycles; production would treat that as a lost lease,
        the harness re-acquires it (same owner, next fencing token) and continues.
        """

        renewed = self.repository.heartbeat_lock(
            DEFAULT_LOCK_NAME, self.worker.owner_id, self.lease, self.worker.lease_ttl_seconds, now
        )
        if renewed:
            return
        token = self.repository.acquire_lock(
            DEFAULT_LOCK_NAME, self.worker.owner_id, self.worker.lease_ttl_seconds, now
        )
        if token is None:
            raise RuntimeError(f"{self.name}: repository lease held by another owner")
        self.lease = token

    def record(self, as_of: datetime, kind: str, result: dict[str, Any], elapsed_ms: float, warmup: bool) -> None:
        publication = self.captured if kind != "t1" else None
        self.captured = None
        source_status = (publication or {}).get("source_status") or {}
        truncated = str(source_status.get("carryover")) == "truncated" or "carryover_truncated" in list(
            (publication or {}).get("warnings") or []
        )
        if truncated:
            self.truncated_days.add(as_of.astimezone(NY).date().isoformat())
        record = {
            "variant": self.name,
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
            "session": result.get("session"),
            "kind": kind,
            "warmup": warmup,
            "status": result.get("status"),
            "error_code": result.get("error_code"),
            "scan_run_id": result.get("scan_run_id"),
            "elapsed_ms": round(elapsed_ms, 1),
            "prefilter_count": self.provider.last_prefilter_count,
            "truncated": truncated,
        }
        if kind == "t1":
            record["t1_completion"] = result.get("t1_completion")
        if publication is not None:
            events = [e if isinstance(e, dict) else e.model_dump(mode="python") for e in publication.get("events") or []]
            record.update(
                {
                    "candidate_count": len(publication.get("candidates") or []),
                    "event_count": len(events),
                    "source_status": {str(k): _value(v) for k, v in source_status.items()},
                    "source_errors": dict(publication.get("source_errors") or {}),
                    "warnings": list(publication.get("warnings") or []),
                    "per_event_errors": list(publication.get("per_event_errors") or []),
                    "candidates": [
                        {
                            "ticker": c.ticker, "exchange": c.exchange, "asset_type": _value(c.asset_type),
                            "price": c.price, "change": c.provider_change_pct, "volume": c.provider_volume,
                            "relvol": c.provider_relative_volume, "market_cap": c.provider_market_cap,
                            "sector": c.sector, "source": c.source,
                        }
                        for c in publication.get("candidates") or []
                    ],
                    "structures": [
                        {
                            "ticker": s.ticker, "pivot_id": s.pivot_id, "base_start": s.base_start.isoformat(),
                            "base_end": s.base_end.isoformat(), "resistance_low": s.resistance_zone.low,
                            "resistance_high": s.resistance_zone.high, "pivot_price": s.pivot_price,
                            "quality": s.quality, "touches": s.pivot_touch_count,
                        }
                        for s in publication.get("structures") or []
                    ],
                    "events": [_compact_event(e) for e in events],
                    "transitions": [
                        {
                            "event_id": t.get("event_id"), "from_state": _value(t.get("from_state")),
                            "to_state": _value(t.get("to_state")), "reason": t.get("reason"),
                            "evidence_at": _iso(t.get("evidence_at")),
                        }
                        for t in publication.get("transitions") or []
                    ],
                    "liquidity_filter_results": list(publication.get("liquidity_filter_results") or []),
                }
            )
            if self.config.full_snapshots:
                self.day_snapshots.append(
                    {"as_of": record["as_of"], "scan_run_id": record["scan_run_id"], "events": events}
                )
        self.day_records.append(record)
        self.scan_count += 1

    def flush_day(self, day: date) -> None:
        with gzip.open(self.ledger_dir / f"{day.isoformat()}.jsonl.gz", "wt", encoding="utf-8") as handle:
            for record in self.day_records:
                handle.write(_dumps(record) + "\n")
        self.day_records = []
        if self.config.full_snapshots:
            with gzip.open(self.snapshot_dir / f"{day.isoformat()}.jsonl.gz", "wt", encoding="utf-8") as handle:
                for record in self.day_snapshots:
                    handle.write(_dumps(record) + "\n")
            self.day_snapshots = []

    def prune(self, now: datetime) -> dict[str, int]:
        return dict(
            self.repository.prune_retention(
                owner_id=self.worker.owner_id, lease_token=self.lease,
                raw_payload_hours=1, scan_days=1, batch_size=5000, now=now,
            )
        )

    def export_research(self) -> dict[str, int]:
        """Rows the evaluation reads: production's research loaders plus the raw tables."""

        connection = self.repository.open_read_connection()
        try:
            events = _load_completed_events_connection(connection)
            shadows = _load_completed_shadows_connection(connection)
            transitions = [dict(row) for row in connection.execute("SELECT * FROM breakout_transitions ORDER BY evidence_at, rowid")]
            heads = [dict(row) for row in connection.execute("SELECT * FROM breakout_events ORDER BY first_seen_at, event_id")]
            t1_rows = [dict(row) for row in connection.execute("SELECT * FROM breakout_t1_current")] if _has_table(connection, "breakout_t1_current") else []
        finally:
            connection.close()
        bundle = {"events": events, "shadows": shadows, "transitions": transitions, "heads": heads, "t1_current": t1_rows}
        with gzip.open(self.config.out / self.name.replace("+", "_") / "research_bundle.json.gz", "wt", encoding="utf-8") as handle:
            handle.write(_dumps(bundle))
        return {key: len(value) for key, value in bundle.items()}

    def release(self) -> None:
        self.repository.release_lock(DEFAULT_LOCK_NAME, self.worker.owner_id, self.lease, self.repository._now())


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def variant_spec_parts(name: str) -> set[str]:
    return {part.strip() for part in name.split("+")}


def build_shared(config: RunConfig) -> dict[str, Any]:
    daily_store = DailyStore(config.daily_db)
    minute_store = MinuteStore(config.minute_store)
    fred_store = FredStore(config.fred)
    if config.metadata_mode == "production":
        if config.export is None:
            raise ValueError("metadata_mode=production needs the production export")
        metadata: Any = ProductionCandidateMetadata(config.export)
    else:
        if config.directory is None:
            raise ValueError("metadata_mode=directory needs the directory folder")
        metadata = DirectoryMetadata(config.directory, config.sic)
    shares = SharesStore(config.shares, daily_store) if config.shares else SharesStore(None, daily_store)
    return {
        "daily_store": daily_store,
        "minute_store": minute_store,
        "fred_store": fred_store,
        "metadata": metadata,
        "shares": shares,
        "day_contexts": {},
        "strength_cache": {},
    }


class _FailureRecorder:
    """Capture the exception the worker swallows so a degraded scan can be raised."""

    def __init__(self) -> None:
        self.last: BaseException | None = None
        self._original = worker_module.record_fallback_failure

    def install(self) -> None:
        def record(name: str, exc: BaseException, **kwargs: Any) -> None:
            if name == "breakouts_scan_failed":
                self.last = exc
            return self._original(name, exc, **kwargs)

        worker_module.record_fallback_failure = record  # type: ignore[assignment]

    def uninstall(self) -> None:
        worker_module.record_fallback_failure = self._original  # type: ignore[assignment]


def run_segment(config: RunConfig) -> dict[str, Any]:
    started = time.time()
    config.out.mkdir(parents=True, exist_ok=True)
    config.db_dir.mkdir(parents=True, exist_ok=True)
    shared = build_shared(config)
    warmup = warmup_days_before(config.start, config.warmup_days) if config.warmup_days else []
    evaluated = trading_days(config.start, config.end)
    days = [*warmup, *evaluated]
    if config.grid == "production":
        if config.export is None:
            raise ValueError("grid=production needs the production export")
        grid_by_day = production_grid(config.export)
    else:
        grid_by_day = {
            day: settings_grid(day, regular_step=config.regular_step_minutes, premarket_step=config.premarket_step_minutes)
            for day in days
        }
    clock = ReplayClock(market_datetime(days[0], 4 * 60))
    market_shape = MarketShapeReplay(shared["daily_store"], shared["fred_store"])
    market_shape.install()
    memo = Memo()
    if config.memo:
        memo.install()
    failures = _FailureRecorder()
    failures.install()
    runs = [_VariantRun(name, config, clock, shared) for name in config.variants]
    summary: dict[str, Any] = {
        "label": config.label,
        "config": {key: (str(value) if isinstance(value, Path) else value) for key, value in asdict(config).items()},
        "days": [{"day": day.isoformat(), "warmup": day in warmup} for day in days],
        "variants": {
            run.name: {
                "production_field_hash": run.production_hash,
                "full_hash": run.full_hash,
                "research_overrides": run.research_overrides,
                "settings_diff": variant_spec(run.name),
                "db": str(run.db_path),
            }
            for run in runs
        },
        "scans": 0,
        "degraded": [],
        "truncated_days": {},
        "prune": {},
    }

    async def drive() -> None:
        for day in days:
            is_warmup = day in warmup
            for as_of, kind in grid_by_day.get(day, []):
                clock.set(as_of)
                for run in runs:
                    run.heartbeat(clock.now)
                    snapshot = run.worker.clock.snapshot()
                    tick = time.perf_counter()
                    result = dict(await run.worker._run_cycle(run.lease, snapshot))
                    elapsed = (time.perf_counter() - tick) * 1000.0
                    if result.get("status") == "degraded":
                        detail = {
                            "variant": run.name, "as_of": as_of.isoformat(), "error_code": result.get("error_code"),
                            "failure_domain": result.get("failure_domain"),
                            "exception": repr(failures.last) if failures.last is not None else None,
                        }
                        run.degraded.append(detail)
                        summary["degraded"].append(detail)
                        if config.on_degraded == "raise":
                            if failures.last is not None:
                                raise RuntimeError(f"degraded scan {detail}") from failures.last
                            raise RuntimeError(f"degraded scan {detail}")
                    run.record(as_of, kind, result, elapsed, is_warmup)
                memo.clear_scan_scope()
            for run in runs:
                run.flush_day(day)
                summary["prune"].setdefault(run.name, {})[day.isoformat()] = run.prune(clock.now)
            memo.clear_day_scope()
            shared["daily_store"].clear_cache()
            for stale in [key for key in shared["day_contexts"] if key < day - timedelta(days=1)]:
                shared["day_contexts"].pop(stale, None)

    try:
        asyncio.run(drive())
    finally:
        for run in runs:
            try:
                run.release()
            except Exception:
                pass
        failures.uninstall()
        memo.uninstall()
        market_shape.uninstall()
    summary["scans"] = sum(run.scan_count for run in runs)
    summary["truncated_days"] = {run.name: sorted(run.truncated_days) for run in runs}
    summary["research_rows"] = {run.name: run.export_research() for run in runs}
    summary["memo_stats"] = memo.stats
    summary["strength_cache"] = {"hits": sum(run.strength.hits for run in runs), "misses": sum(run.strength.misses for run in runs)}
    summary["elapsed_s"] = round(time.time() - started, 1)
    (config.out / "run.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary
