"""Regressions for the 2026-09-25 review of the Breakout Radar core.

Worker chains here use the real service, repository and SQLite store; only the
market-data adapters and the discovery transport are fixtures.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

import app.services.breakouts.providers as providers_package
import app.services.breakouts.service as service_module
from app import failure_diagnostics
from app.services import massive
from app.services.breakouts.adapters import price_data
from app.services.breakouts.adapters.price_data import YahooPriceDataAdapter
from app.services.breakouts.clock import MarketClock
from app.services.breakouts.config import BreakoutSettings, break_buffer
from app.services.breakouts.feature_engine import _clean_frame
from app.services.breakouts.lifecycle import TransitionResult
from app.services.breakouts.models import (
    AssetType,
    BreakoutCandidate,
    BreakoutEvent,
    BreakoutLifecycleState,
    DiscoverySnapshot,
    MarketSession,
    ProviderStatus,
    TemporalCutoff,
)
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider
from app.services.breakouts.repository import (
    DEFAULT_LOCK_NAME,
    BreakoutRepository,
    LeaseLostError,
)
from app.services.breakouts.research_validation import attach_forward_return_labels
from app.services.breakouts.service import BreakoutRadarService, _safe_range_feature
from app.services.breakouts.t1_priority import (
    T1_MET,
    T1_PENDING,
    T1_UNAVAILABLE,
    t1_needs_close_eval,
)
from app.services.breakouts.worker import BreakoutWorker
from tests.test_breakout_research_validation import _observation, _price_dataset
from tests.test_breakout_service import (
    AS_OF,
    Market,
    Prices,
    Provider as CandidateProvider,
    Strength,
    Universe,
    _daily,
    _discovery,
    _premarket_event,
)
from tests.test_breakout_worker import (
    NOW as WORKER_NOW,
    Provider as FixtureProvider,
    Settings as WorkerSettings,
)
from tests.test_t1_close_completion import SESSION, _daily_frame


HOLDING = BreakoutLifecycleState.HOLDING
REACCELERATING = BreakoutLifecycleState.REACCELERATING
T0 = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


@pytest.fixture
def diagnostics(monkeypatch, caplog):
    monkeypatch.setattr(failure_diagnostics, "_seen", {})

    def messages() -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == "app.failure_diagnostics"
        ]

    return messages


def _logged(messages: list[str], stage: str) -> bool:
    return any(f"stage={stage} " in message for message in messages)


def _radar_settings(tmp_path, **overrides) -> BreakoutSettings:
    return BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        BREAKOUT_DISCOVERY_PROVIDER="tradingview",
        db_path=tmp_path / "radar.db",
        **overrides,
    )


def _candidate(ticker: str = "TEST") -> BreakoutCandidate:
    return BreakoutCandidate(
        ticker=ticker,
        exchange="NASDAQ",
        asset_type=AssetType.COMMON_STOCK,
        price=104,
        previous_regular_close=100,
        provider_change_pct=8,
        provider_volume=2_000_000,
        provider_relative_volume=3,
        provider_market_cap=1_000_000_000,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )


def _service(settings, *, price_data_adapter=None, universe=None, market=None):
    return BreakoutRadarService(
        settings,
        price_data=price_data_adapter or Prices(),
        strength=Strength(),
        market_shape=market or Market(),
        universe=universe or Universe(),
    )


class RecordingService(BreakoutRadarService):
    """Real service that keeps each build result and can fail continuations."""

    def __init__(self, settings, *, fail_continuation: bool = False) -> None:
        super().__init__(
            settings,
            price_data=Prices(),
            strength=Strength(),
            market_shape=Market(),
            universe=Universe(),
        )
        self.fail_continuation = fail_continuation
        self.results: list[dict] = []

    def _continue_event(self, prior_value, **kwargs):
        if self.fail_continuation:
            raise ValueError("simulated carryover processing error")
        return super()._continue_event(prior_value, **kwargs)

    async def build_snapshot(self, discovery, **kwargs):
        result = await super().build_snapshot(discovery, **kwargs)
        self.results.append(result)
        return result


def _scan(settings, at, owner, *, service=None, candidate=None, repository=None):
    worker = BreakoutWorker(
        settings,
        repository or BreakoutRepository(settings.db_path),
        provider=CandidateProvider(candidate or _candidate()),
        scan_service=service or _service(settings),
        clock=MarketClock(now=lambda: at),
        owner_id=owner,
    )
    return asyncio.run(worker.run_once())


def _worker_status(path, owner: str) -> dict:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status, error_code, details_json FROM breakout_worker_status "
            "WHERE worker_id=?",
            (owner,),
        ).fetchone()
    return {"status": row[0], "error_code": row[1], "details": json.loads(row[2])}


def _watching_event(event_id: str, seen_at: datetime, **updates) -> dict:
    event = {
        "event_id": event_id,
        "trading_date": "2026-07-10",
        "ticker": "AAPL",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "WATCHING",
        "previous_state": "WATCHING",
        "event_at": T0,
        "first_seen_at": T0,
        "triggered_at": None,
        "state_changed_at": T0,
        "last_seen_at": seen_at,
        "pivot_id": "pivot-aapl",
        "scores": {"alert_priority_score": 50.0},
    }
    event.update(updates)
    return event


def _publish(repository, at, events, transitions=()):
    scan_id = repository.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=at,
        config_hash="config",
        versions_hash="versions",
        now=at,
    )
    repository.publish_scan(
        scan_id,
        {
            "provider_snapshot": {
                "provider": "fixture",
                "status": "active",
                "as_of": at,
                "session": "regular",
                "schema_version": "fixture-v1",
                "candidates": [],
            },
            "events": list(events),
            "transitions": list(transitions),
        },
        now=at,
    )
    return scan_id


def _retry_state(key: str, **values) -> dict:
    return {
        "retry_key": key,
        "event_id": key.split("|")[0],
        "session_date": "2026-09-14",
        "algorithm": "t1_daily_priority",
        "attempt": 1,
        "max_attempts": 8,
        "next_eligible_at": None,
        "exhausted": False,
        "last_reason": "daily_fetch_failed",
        **values,
    }


# M-1: post-publication T1 persistence


def test_m1_real_scan_persists_t1_and_repeated_pending_adds_no_version(tmp_path):
    settings = _radar_settings(tmp_path)
    repository = BreakoutRepository(settings.db_path)
    calls = []
    persist = repository.persist_t1_evaluations

    def recording(events):
        events = list(events)
        written = persist(events)
        calls.append(({type(item).__name__ for item in events}, len(events), written))
        return written

    repository.persist_t1_evaluations = recording
    service = RecordingService(settings)

    first = _scan(settings, AS_OF, "t1-first", service=service, repository=repository)
    second = _scan(
        settings,
        AS_OF + timedelta(minutes=5),
        "t1-second",
        service=service,
        repository=repository,
    )

    assert first["status"] == second["status"] == "completed"
    assert first["t1_persistence"] == "completed"
    assert [item[0] for item in calls] == [{"BreakoutEvent"}, {"BreakoutEvent"}]
    assert all(count > 0 and written == count for _, count, written in calls)
    published = {
        event.event_id: event.features["t1_priority"]["status"]
        for event in service.results[-1]["events"]
    }
    with sqlite3.connect(settings.db_path) as connection:
        versions = dict(
            connection.execute(
                "SELECT event_id, COUNT(*) FROM breakout_t1_evaluations GROUP BY event_id"
            ).fetchall()
        )
    assert versions == {event_id: 1 for event_id in published}
    stored = repository.overlay_t1_evaluations(
        [{"event_id": event_id} for event_id in published]
    )
    for item in stored:
        assert item["t1_priority"]["status"] == published[item["event_id"]]
        assert item["t1_priority"]["latest_attempt"]["computed_at"] == (
            "2026-07-10T14:35:00Z"
        )


def test_m1_same_pending_attempt_written_five_times_keeps_one_version(tmp_path):
    repository = BreakoutRepository(
        tmp_path / "t1.db",
        clock=lambda: datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )
    repository.initialize()
    pending = {
        "status": T1_PENDING,
        "reason": "session_incomplete",
        "version": "t1-daily-priority-v1",
        "variant": "t1_daily_priority",
    }
    for minute in range(5):
        written = repository.persist_t1_evaluations(
            [
                {
                    "event_id": "evt-pending",
                    "t1_priority": {
                        **pending,
                        "computed_at": f"2026-07-10T15:0{minute}:00Z",
                    },
                }
            ]
        )
        assert written == 1

    with sqlite3.connect(repository.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*), MAX(eval_version) FROM breakout_t1_evaluations"
        ).fetchone() == (1, 1)
    stored = repository.overlay_t1_evaluations([{"event_id": "evt-pending"}])[0]
    assert stored["t1_priority"]["computed_at"] == "2026-07-10T15:00:00Z"
    assert stored["t1_priority"]["latest_attempt"] == {
        "status": T1_PENDING,
        "reason": "session_incomplete",
        "computed_at": "2026-07-10T15:04:00Z",
        "identity_complete": False,
    }


def test_m1_changed_incomplete_status_or_reason_still_creates_a_version(tmp_path):
    repository = BreakoutRepository(tmp_path / "t1-reasons.db")
    repository.initialize()
    attempts = [
        (T1_PENDING, "session_incomplete"),
        (T1_UNAVAILABLE, "daily_unavailable"),
        (T1_UNAVAILABLE, "daily_unavailable"),
        (T1_UNAVAILABLE, "missing_prior_session"),
    ]
    for status, reason in attempts:
        repository.persist_t1_evaluations(
            [{"event_id": "evt-reasons", "t1_priority": {"status": status, "reason": reason}}]
        )

    with sqlite3.connect(repository.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*), MAX(eval_version) FROM breakout_t1_evaluations"
        ).fetchone() == (3, 3)
    stored = repository.overlay_t1_evaluations(
        [{"event_id": "evt-reasons", "setup_type": "DAILY_BASE_BREAKOUT"}]
    )[0]
    assert stored["t1_priority"]["reason"] == "missing_prior_session"
    # The non-retryable reason must be current, or close completion would
    # re-dispatch this event forever.
    assert t1_needs_close_eval(stored) is False


def test_m1_retention_prunes_superseded_t1_versions_and_stale_retry_rows(tmp_path):
    now = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    clock = {"at": now - timedelta(days=40)}
    repository = BreakoutRepository(tmp_path / "t1-retention.db", clock=lambda: clock["at"])
    repository.initialize()

    def attempt(event_id: str, status: str, reason: str) -> None:
        repository.persist_t1_evaluations(
            [{"event_id": event_id, "t1_priority": {"status": status, "reason": reason}}]
        )

    attempt("evt-old", T1_PENDING, "session_incomplete")
    attempt("evt-old", T1_UNAVAILABLE, "daily_unavailable")
    attempt("evt-mixed", T1_PENDING, "session_incomplete")
    repository.save_t1_retry_states([_retry_state("evt-old|2026-08-14|t1")])
    clock["at"] = now - timedelta(days=1)
    attempt("evt-mixed", T1_UNAVAILABLE, "daily_unavailable")
    attempt("evt-recent", T1_PENDING, "session_incomplete")
    attempt("evt-recent", T1_UNAVAILABLE, "daily_unavailable")
    repository.save_t1_retry_states([_retry_state("evt-recent|2026-09-24|t1")])
    clock["at"] = now
    token = repository.acquire_lock(DEFAULT_LOCK_NAME, "t1-retention", 90, now)

    counts = repository.prune_retention(
        owner_id="t1-retention",
        lease_token=token,
        scan_days=30,
        now=now,
    )

    assert counts["t1_evaluations"] == 2
    assert counts["t1_retry"] == 1
    with sqlite3.connect(repository.path) as connection:
        remaining = sorted(
            connection.execute(
                "SELECT event_id, eval_version FROM breakout_t1_evaluations"
            ).fetchall()
        )
    assert remaining == [
        ("evt-mixed", 2),
        ("evt-old", 2),
        ("evt-recent", 1),
        ("evt-recent", 2),
    ]
    current = repository.overlay_t1_evaluations(
        [{"event_id": "evt-old"}, {"event_id": "evt-mixed"}]
    )
    assert [item["t1_priority"]["status"] for item in current] == [
        T1_UNAVAILABLE,
        T1_UNAVAILABLE,
    ]
    assert set(
        repository.load_t1_retry_states(
            ["evt-old|2026-08-14|t1", "evt-recent|2026-09-24|t1"]
        )
    ) == {"evt-recent|2026-09-24|t1"}


# M-2: carryover-owned identities and stray trigger stamps


def test_m2_failed_carryover_reevaluation_publishes_no_stray_transition(tmp_path):
    settings = _radar_settings(tmp_path)
    assert _scan(settings, AS_OF, "m2-seed")["status"] == "completed"
    service = RecordingService(settings, fail_continuation=True)

    second = _scan(settings, AS_OF + timedelta(minutes=5), "m2-failed", service=service)

    assert second["status"] == "completed"
    snapshot = service.results[-1]
    assert snapshot["transitions"] == []
    assert [item["error_code"] for item in snapshot["per_event_errors"]] == [
        "carryover_processing_error"
    ]
    with sqlite3.connect(settings.db_path) as connection:
        leaked = connection.execute(
            "SELECT COUNT(*) FROM breakout_transitions WHERE scan_run_id=?",
            (second["scan_run_id"],),
        ).fetchone()[0]
        state = connection.execute(
            "SELECT lifecycle_state FROM breakout_events"
        ).fetchone()[0]
        last_to_state = connection.execute(
            "SELECT to_state FROM breakout_transitions ORDER BY evidence_at DESC, rowid DESC"
        ).fetchone()[0]
    assert leaked == 0
    assert state == last_to_state


def test_m2_rediscovered_carryover_keeps_only_its_own_transitions(tmp_path, monkeypatch):
    settings = _radar_settings(tmp_path)
    assert _scan(settings, AS_OF, "m2-first")["status"] == "completed"
    service = RecordingService(settings)

    def diverging(state, observation):
        # Only continuation observations carry origin_setup_type: the new path
        # moves the rediscovered identity while the carryover keeps its state.
        if "origin_setup_type" in observation or state is not BreakoutLifecycleState.CONFIRMED:
            return TransitionResult(state, state, False, "no_meaningful_transition")
        return TransitionResult(state, HOLDING, True, "new_path_only")

    monkeypatch.setattr(service_module, "transition_state", diverging)

    second = _scan(settings, AS_OF + timedelta(minutes=5), "m2-second", service=service)

    assert second["status"] == "completed"
    snapshot = service.results[-1]
    assert snapshot["transitions"] == []
    assert [event.lifecycle_state for event in snapshot["events"]] == [
        BreakoutLifecycleState.CONFIRMED
    ]
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM breakout_transitions WHERE reason='new_path_only'"
        ).fetchone() == (0,)


def test_m2_stray_triggered_transition_never_stamps_a_watching_event(tmp_path):
    repository = BreakoutRepository(tmp_path / "trigger.db")
    repository.initialize()
    stray_at = T0 + timedelta(minutes=5)
    _publish(repository, T0, [_watching_event("evt-w", T0)])
    _publish(
        repository,
        stray_at,
        [_watching_event("evt-w", stray_at)],
        [
            {
                "event_id": "evt-w",
                "from_state": "WATCHING",
                "to_state": "TRIGGERED",
                "reason": "breakout_trigger_crossed",
                "evidence_at": stray_at,
            }
        ],
    )
    _publish(repository, T0 + timedelta(minutes=10), [_watching_event("evt-w", T0 + timedelta(minutes=10))])

    def row():
        with sqlite3.connect(repository.path) as connection:
            return connection.execute(
                "SELECT lifecycle_state, triggered_at, event_at FROM breakout_events"
            ).fetchone()

    assert row() == ("WATCHING", None, "2026-07-10T14:30:00.000000Z")

    trigger_at = T0 + timedelta(minutes=15)
    _publish(
        repository,
        trigger_at,
        [
            _watching_event(
                "evt-w",
                trigger_at,
                lifecycle_state="TRIGGERED",
                triggered_at=trigger_at,
                state_changed_at=trigger_at,
            )
        ],
        [
            {
                "event_id": "evt-w",
                "from_state": "WATCHING",
                "to_state": "TRIGGERED",
                "reason": "breakout_trigger_crossed",
                "evidence_at": trigger_at,
            }
        ],
    )
    # The real trigger wins over the earlier stray transition.
    assert row() == (
        "TRIGGERED",
        "2026-07-10T14:45:00.000000Z",
        "2026-07-10T14:45:00.000000Z",
    )


# M-3: one invalid stored snapshot must not fail every scan


def test_m3_extra_key_carryover_is_skipped_then_expired_by_minimal_fields(
    tmp_path, diagnostics
):
    settings = _radar_settings(tmp_path)
    assert _scan(settings, AS_OF, "m3-seed")["status"] == "completed"
    with sqlite3.connect(settings.db_path) as connection:
        rows = connection.execute(
            "SELECT rowid, event_snapshot_json FROM breakout_scan_events"
        ).fetchall()
        poisoned = {json.loads(raw)["event_id"] for _, raw in rows}
        for rowid, raw in rows:
            body = json.loads(raw)
            body["field_removed_by_a_later_release"] = 1
            connection.execute(
                "UPDATE breakout_scan_events SET event_snapshot_json=? WHERE rowid=?",
                (json.dumps(body), rowid),
            )
    other = _candidate("OTHER")

    second = _scan(settings, AS_OF + timedelta(minutes=5), "m3-second", candidate=other)

    assert second["status"] == "completed"
    details = _worker_status(settings.db_path, "m3-second")["details"]
    assert details["source_status"]["carryover"] == "degraded"
    assert details["per_event_error_count"] == len(poisoned)
    assert {item["event_id"] for item in details["per_event_errors"]} == poisoned
    assert {item["error_code"] for item in details["per_event_errors"]} == {
        "carryover_snapshot_invalid"
    }
    assert any(
        "stage=breakout_carryover_snapshot_invalid symbol=TEST" in message
        for message in diagnostics()
    )

    due_at = AS_OF + timedelta(hours=72)
    assert _scan(settings, due_at, "m3-due", candidate=other)["status"] == "completed"
    with sqlite3.connect(settings.db_path) as connection:
        states = dict(
            connection.execute("SELECT event_id, lifecycle_state FROM breakout_events")
        )
    assert {states[event_id] for event_id in poisoned} == {"EXPIRED"}
    later = BreakoutRepository(settings.db_path).load_carryover_events(
        as_of=due_at + timedelta(minutes=5),
        event_ttl_seconds=settings.event_ttl_seconds,
        limit=150,
        expired_due_limit=40,
    )
    assert poisoned.isdisjoint(str(item["event_id"]) for item in later.events)


def test_m3_invalid_realtime_event_is_reported_and_skipped(diagnostics):
    settings = BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="shadow")
    good = _premarket_event(_service(settings), ticker="GOOD")
    bad = {
        **good.model_dump(mode="python"),
        "event_id": "bad-live-event-0001",
        "ticker": "BAD",
        "state_version": 1,
        "stale_live_key": True,
    }

    payload = asyncio.run(
        _service(settings).build_snapshot(
            _discovery(at=AS_OF, session=MarketSession.REGULAR, candidates=[]),
            carryover_events=[good.model_dump(mode="python")],
            realtime_events=[bad],
        )
    )

    assert [event.event_id for event in payload["events"]] == [good.event_id]
    assert payload["realtime_events"] == []
    assert payload["per_event_errors"] == [
        {
            "event_id": "bad-live-event-0001",
            "ticker": "BAD",
            "error_code": "realtime_snapshot_invalid",
            "error_type": "ValidationError",
        }
    ]
    assert payload["source_status"]["carryover"] == "degraded"
    assert any(
        "stage=breakout_realtime_snapshot_invalid symbol=BAD" in message
        for message in diagnostics()
    )


def test_m3_unreviewed_fallback_reuses_the_validated_event(monkeypatch):
    settings = BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="shadow")
    event = _premarket_event(_service(settings), ticker="SAME")
    assert event.previous_state is not event.lifecycle_state
    service = _service(settings)

    def refuse(cls, *_args, **_kwargs):
        raise AssertionError("the fallback must not validate the payload again")

    monkeypatch.setattr(BreakoutEvent, "model_validate", classmethod(refuse))
    continued, transitions, shadow = service._unreviewed_carryover(
        event,
        observed_at=AS_OF,
        reason="carryover_processing_error",
        versions={},
    )

    assert continued.event_id == event.event_id
    assert transitions == []
    assert shadow["event_id"] == event.event_id
    # An unchanged republish must not re-trigger the stored transition.
    assert continued.previous_state is continued.lifecycle_state
    assert "carryover_processing_error" in continued.warnings


# M-4: an injected discovery provider survives across workers


def test_m4_injected_provider_accumulates_failures_until_its_circuit_opens(tmp_path):
    requests: list[httpx.Request] = []

    async def unavailable(request):
        requests.append(request)
        return httpx.Response(503)

    settings = _radar_settings(
        tmp_path, provider_retry_attempts=1, provider_failure_threshold=3
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    provider = TradingViewDiscoveryProvider(settings, client=client)
    closed: list[bool] = []

    async def record_close() -> None:
        closed.append(True)

    provider.aclose = record_close

    async def scheduled_scans() -> list:
        results = []
        for index in range(4):
            worker = BreakoutWorker(
                settings,
                BreakoutRepository(settings.db_path),
                provider=provider,
                clock=MarketClock(now=lambda: WORKER_NOW),
                owner_id=f"circuit-{index}",
            )
            results.append(await worker.run_once())
        await client.aclose()
        return results

    results = asyncio.run(scheduled_scans())

    assert [item["status"] for item in results] == ["degraded"] * 4
    assert len(requests) == 3
    assert provider.health["consecutive_failures"] == 3
    assert provider.health["circuit_open"] is True
    assert closed == []
    with sqlite3.connect(settings.db_path) as connection:
        stored = connection.execute(
            "SELECT consecutive_failures, json_extract(details_json, '$.circuit_open') "
            "FROM breakout_provider_health WHERE provider='tradingview'"
        ).fetchone()
    assert stored == (3, 1)


def test_m4_worker_owned_provider_is_created_and_closed_each_run(tmp_path, monkeypatch):
    created = []

    class OwnedProvider:
        def __init__(self, _settings, **_kwargs) -> None:
            self.closed = False
            created.append(self)

        @property
        def health(self):
            return {
                "provider": "fixture",
                "status": "active",
                "consecutive_failures": 0,
                "stale_snapshot_available": False,
            }

        async def scan(self, *, session, as_of, profile):
            return DiscoverySnapshot(
                provider="fixture",
                status=ProviderStatus.ACTIVE,
                as_of=as_of,
                session=session,
                schema_version="fixture-v1",
                candidate_count=0,
                candidates=[],
                cache_key=f"owned-{as_of.isoformat()}",
            )

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(providers_package, "TradingViewDiscoveryProvider", OwnedProvider)
    settings = _radar_settings(tmp_path)
    worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        clock=MarketClock(now=lambda: WORKER_NOW),
        owner_id="owned-provider",
    )

    for _ in range(2):
        assert asyncio.run(worker.run_once())["status"] == "completed"

    assert len(created) == 2
    assert all(item.closed for item in created)
    assert worker.provider is None


# M-5: swallowed adapter failures are visible


class _BrokenDailyPrices(Prices):
    async def daily(self, tickers, *, cutoff, period):
        raise TypeError("adapter bug: unexpected keyword")


class _BrokenIntradayPrices(Prices):
    async def intraday(self, tickers, *, cutoff, interval):
        raise TypeError("adapter bug: unexpected keyword")


@pytest.mark.parametrize(
    ("prices", "stage", "source"),
    [
        (_BrokenDailyPrices, "breakout_daily_prices", "prices"),
        (_BrokenIntradayPrices, "breakout_intraday_batch", "intraday"),
    ],
)
def test_m5_price_adapter_type_error_is_visible_in_worker_details(
    tmp_path, diagnostics, prices, stage, source
):
    settings = _radar_settings(tmp_path)

    result = _scan(
        settings,
        AS_OF,
        "m5-prices",
        service=_service(settings, price_data_adapter=prices()),
    )

    assert result["status"] == "completed"
    assert result["event_count"] == 0
    details = _worker_status(settings.db_path, "m5-prices")["details"]
    assert details["source_errors"] == {source: "TypeError"}
    if source == "prices":
        assert details["source_status"]["prices"] == "unavailable"
    assert any(
        f"stage={stage} " in message and "error_type=TypeError" in message
        for message in diagnostics()
    )


def test_m5_universe_market_and_sector_failures_are_recorded(diagnostics):
    class BrokenUniverse(Universe):
        async def tickers(self, *, as_of):
            raise RuntimeError("universe store offline")

        def sector_benchmark(self, ticker, provider_sector=None):
            raise RuntimeError("sector map offline")

    class BrokenMarket(Market):
        async def snapshot(self, *, as_of):
            raise RuntimeError("market shape offline")

    service = _service(
        BreakoutSettings(_env_file=None),
        universe=BrokenUniverse(),
        market=BrokenMarket(),
    )

    payload = asyncio.run(
        service.build_snapshot(
            _discovery(at=AS_OF, session=MarketSession.REGULAR, candidates=[_candidate()])
        )
    )

    assert payload["source_errors"] == {
        "universe": "RuntimeError",
        "market_shape": "RuntimeError",
    }
    assert payload["source_status"]["market_shape"] == "unavailable"
    messages = diagnostics()
    for stage in ("breakout_universe", "breakout_market_shape", "breakout_sector_benchmark"):
        assert _logged(messages, stage)


def test_m5_range_feature_failure_is_recorded(diagnostics):
    result = _safe_range_feature(
        _daily()[["Close", "Volume"]],
        cutoff=AS_OF,
        length=35,
        fast_length=3,
        slope_lookback=5,
        ratio_window=10,
        ratio_threshold=60,
        min_history_multiplier=5,
        version="range-persistence-v1",
    )

    assert result["status"] == "unavailable"
    assert _logged(diagnostics(), "breakout_range_feature")


def test_m5_scan_failure_and_retention_failure_leave_diagnostics(
    tmp_path, monkeypatch, diagnostics
):
    settings = _radar_settings(tmp_path)
    failed = asyncio.run(
        BreakoutWorker(
            settings,
            BreakoutRepository(settings.db_path),
            provider=FixtureProvider(fail=True),
            clock=MarketClock(now=lambda: WORKER_NOW),
            owner_id="m5-failed",
        ).run_once()
    )
    repository = BreakoutRepository(settings.db_path)

    def locked_retention(**_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "prune_retention", locked_retention)
    completed = asyncio.run(
        BreakoutWorker(
            settings,
            repository,
            provider=FixtureProvider(),
            clock=MarketClock(now=lambda: WORKER_NOW),
            owner_id="m5-retention",
        ).run_once()
    )

    assert failed["status"] == "degraded"
    assert completed["status"] == "completed"
    assert _worker_status(settings.db_path, "m5-retention")["details"]["retention"] is None
    messages = diagnostics()
    assert _logged(messages, "breakouts_scan_failed")
    assert _logged(messages, "breakouts_retention")


# M-6: OHLC validity includes the open


def test_m6_clean_frame_drops_an_open_outside_the_bar_range():
    index = pd.date_range("2026-07-06", periods=5, freq="D")
    frame = pd.DataFrame(
        {
            "Open": [10.0, 1000.0, 7.0, 12.0, 8.0],
            "High": [12.0] * 5,
            "Low": [8.0] * 5,
            "Close": [10.0] * 5,
            "Volume": [1_000.0] * 5,
        },
        index=index,
    )

    cleaned = _clean_frame(frame)

    assert list(cleaned.index) == [index[0], index[3], index[4]]
    assert cleaned["Open"].tolist() == [10.0, 12.0, 8.0]


# Low-severity findings


def test_low_continuation_state_machine_stops_at_a_repeated_state(monkeypatch):
    settings = BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="shadow")
    first = _premarket_event(_service(settings), ticker="FLIP")
    held = first.model_copy(
        update={"lifecycle_state": HOLDING, "triggered_at": first.first_seen_at}
    )

    def flip(state, _observation):
        target = REACCELERATING if state is HOLDING else HOLDING
        return TransitionResult(state, target, True, "fixture_flip")

    monkeypatch.setattr(service_module, "transition_state", flip)
    payload = asyncio.run(
        _service(settings).build_snapshot(
            _discovery(at=AS_OF, session=MarketSession.REGULAR, candidates=[]),
            carryover_events=[held.model_dump(mode="python")],
        )
    )

    assert [
        (item["from_state"], item["to_state"]) for item in payload["transitions"]
    ] == [(HOLDING, REACCELERATING)]
    assert payload["events"][0].lifecycle_state is REACCELERATING


def test_low_t1_retry_attempt_and_exhaustion_never_move_backwards(tmp_path):
    repository = BreakoutRepository(tmp_path / "retry.db")
    repository.initialize()
    key = "evt-budget|2026-09-14|t1_daily_priority"
    repository.save_t1_retry_states([_retry_state(key, attempt=8, exhausted=True)])

    # A writer that could not read the stored row starts again at attempt 1.
    repository.save_t1_retry_states(
        [_retry_state(key, attempt=1, next_eligible_at="2026-09-14T21:00:30Z")]
    )

    state = repository.load_t1_retry_states([key])[key]
    assert state["attempt"] == 8
    assert state["exhausted"] is True


def test_low_t1_reads_tolerate_a_missing_file_but_report_database_errors(
    tmp_path, monkeypatch
):
    missing = BreakoutRepository(tmp_path / "missing.db", read_only=True)
    assert missing.overlay_t1_evaluations([{"event_id": "evt"}]) == [{"event_id": "evt"}]
    assert missing.load_t1_retry_states(["evt|2026-09-14|t1"]) == {}

    repository = BreakoutRepository(tmp_path / "t1-errors.db")
    repository.initialize()

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "_t1_payloads_locked", locked)
    monkeypatch.setattr(repository, "_has_t1_retry_table", locked)
    with pytest.raises(sqlite3.OperationalError):
        repository.overlay_t1_evaluations([{"event_id": "evt"}])
    with pytest.raises(sqlite3.OperationalError):
        repository.load_t1_retry_states(["evt|2026-09-14|t1"])


def test_low_worker_records_a_failed_t1_retry_state_read(
    tmp_path, monkeypatch, diagnostics
):
    settings = WorkerSettings(tmp_path / "t1-read.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    event = {
        "event_id": "evt-read-failure",
        "ticker": "AAA",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "trading_date": SESSION.isoformat(),
        "lifecycle_state": "WATCHING",
        "structure": {"resistance_zone": {"high": 100.0, "low": 90.0}},
        "features": {"t1_priority": {"status": T1_PENDING, "reason": "session_incomplete"}},
        "event_at": "2026-09-14T18:00:00+00:00",
    }
    monkeypatch.setattr(repository, "latest_completed_scan", lambda: {"events": [event]})

    def locked(_keys):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "load_t1_retry_states", locked)
    frame = _daily_frame()

    async def daily(tickers, **_kwargs):
        return {str(tickers[0]): SimpleNamespace(frame=frame)}

    worker = BreakoutWorker(
        settings,
        repository,
        provider=FixtureProvider(),
        scan_service=SimpleNamespace(price_data=SimpleNamespace(daily=daily)),
        clock=MarketClock(now=lambda: datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc)),
        owner_id="t1-read-failure",
    )

    result = asyncio.run(worker.run_once())

    assert result["status"] == "paused"
    assert result["t1_completion"]["completed"] == 1
    stored = repository.overlay_t1_evaluations([{"event_id": "evt-read-failure"}])[0]
    assert stored["t1_priority"]["status"] == T1_MET
    assert _logged(diagnostics(), "breakouts_t1_retry_read")


def test_low_lease_loss_survives_a_failed_scan_record(tmp_path, monkeypatch, diagnostics):
    settings = WorkerSettings(tmp_path / "lease.db")
    repository = BreakoutRepository(settings.db_path)

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "heartbeat_lock", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(repository, "fail_scan", locked)
    worker = BreakoutWorker(
        settings,
        repository,
        provider=FixtureProvider(),
        clock=MarketClock(now=lambda: WORKER_NOW),
        owner_id="lease-lost",
    )

    with pytest.raises(LeaseLostError):
        asyncio.run(worker.run_once())
    assert _logged(diagnostics(), "breakouts_lease_lost_scan_record")


def test_low_provider_health_write_failure_keeps_the_degraded_status(
    tmp_path, monkeypatch, diagnostics
):
    settings = WorkerSettings(tmp_path / "health.db")
    repository = BreakoutRepository(settings.db_path)

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "record_provider_health", locked)

    result = asyncio.run(
        BreakoutWorker(
            settings,
            repository,
            provider=FixtureProvider(fail=True),
            clock=MarketClock(now=lambda: WORKER_NOW),
            owner_id="health-write",
        ).run_once()
    )

    assert result["status"] == "degraded"
    status = _worker_status(settings.db_path, "health-write")
    assert status["status"] == "degraded"
    assert status["error_code"] == "provider_fixture_failed"
    assert _logged(diagnostics(), "breakouts_provider_health")


def test_low_unknown_price_timezone_marks_rows_instead_of_raising():
    dataset = _price_dataset({"AAPL": [{"date": "2026-01-05", "close": 101.0}]})
    dataset["timezone"] = "Mars/Olympus_Mons"

    result = attach_forward_return_labels([_observation(date(2026, 1, 2))], dataset)

    labels = result["observations"][0]["labels"]
    assert {item["reason"] for item in labels.values()} == {"invalid_trading_date"}


def test_low_trading_date_fallback_uses_the_new_york_session(tmp_path):
    late = datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc)
    repository = BreakoutRepository(tmp_path / "dates.db")
    repository.initialize()
    event = _watching_event(
        "evt-late",
        late,
        event_at=late,
        first_seen_at=late,
        state_changed_at=late,
    )
    del event["trading_date"]

    _publish(repository, late, [event])

    with sqlite3.connect(repository.path) as connection:
        assert connection.execute(
            "SELECT trading_date FROM breakout_events WHERE event_id='evt-late'"
        ).fetchone() == ("2026-07-10",)


# Safety cleanup


def test_break_buffer_pairs_the_percentage_and_atr_legs():
    settings = BreakoutSettings(
        _env_file=None,
        break_buffer_pct=0.01,
        break_buffer_atr=0.5,
    )

    assert break_buffer(100.0, 1.0, settings) == pytest.approx(1.0)
    assert break_buffer(100.0, 4.0, settings) == pytest.approx(2.0)
    assert break_buffer(None, 4.0, settings) == pytest.approx(2.0)
    assert break_buffer(None, None, settings) == 0.0


def test_intraday_adapter_records_primary_and_fallback_failures(monkeypatch, diagnostics):
    def offline(*_args, **_kwargs):
        raise RuntimeError("provider offline")

    monkeypatch.setattr(massive, "configured", offline)
    monkeypatch.setattr(price_data, "download_in_bounded_batches", offline)
    cutoff = TemporalCutoff(event_at=AS_OF, session=MarketSession.REGULAR)

    result = asyncio.run(
        YahooPriceDataAdapter().intraday(["AAPL"], cutoff=cutoff, interval="5m")
    )

    assert result == {}
    messages = diagnostics()
    assert _logged(messages, "breakout_intraday_massive")
    assert _logged(messages, "breakout_intraday_yfinance")
