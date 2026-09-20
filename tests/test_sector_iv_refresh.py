from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.access import request_owner_access_context
from app.api import sectors
from app.services import sector_iv_refresh as refresh
from app.worker.tasks import SectorIVTask


def stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(sectors.router)
    with TestClient(app) as client:
        yield client


def action_headers():
    return {'Origin': 'http://testserver', 'X-Optix-Action': '1'}


@pytest.mark.parametrize('owner', [False, True])
def test_request_worker_persistence_and_read_share_public_path(monkeypatch, client, owner):
    calls = []
    now = time.time()
    monkeypatch.setattr(sectors.massive, 'configured', lambda: False)
    monkeypatch.setattr(sectors, '_read_strength_sector_iv_snapshot', lambda *a, **k: None)

    def snapshot(ticker):
        calls.append(ticker)
        return {'atm_iv': .2 + len(ticker) / 100, 'as_of': stamp(now - 30), '_stale': False}

    monkeypatch.setattr(sectors.yahoo, 'get_stock_iv_snapshot', snapshot)
    monkeypatch.setattr(sectors.yahoo, 'get_last_price', lambda ticker: 100)
    with request_owner_access_context(owner):
        first = client.get('/api/sectors/semiconductors/iv-ranking')
        assert first.status_code == 200
        assert first.json()['source_status'] == 'insufficient_data'
        assert first.json()['refresh']['status'] == 'queued'
        for _ in range(3):
            response = client.post('/api/sectors/semiconductors/iv-refresh', headers=action_headers())
            assert response.status_code == 202
            assert response.json()['refresh']['requested_at'] == first.json()['refresh']['requested_at']
        assert calls == []
        result = asyncio.run(refresh.run_refresh_batch(schedule=False))
        assert result == {'completed': 1, 'failed': 0}
        assert sorted(calls) == sorted(sectors.SECTORS['semiconductors']['tickers'])
        # Fresh store instance and disk read prove that process-local caches are unnecessary.
        sectors._cache.clear()
        sectors._sector_iv_documents.invalidate()
        assert refresh.default_store().status('semiconductors')['status'] == 'idle'
        second = client.get('/api/sectors/semiconductors/iv-ranking').json()
        assert second['success_count'] == len(calls)
        assert second['refresh']['completed_at']
        assert second['as_of'] == stamp(now - 30)
        assert second['snapshot_origin'] == 'worker'
        assert second['source_status'] == 'active'
        heatmap = client.get('/api/sectors/semiconductors/heatmap').json()
        assert heatmap['refresh'] == second['refresh']
        assert len(heatmap['data']) == len(calls)
        cooldown = client.post('/api/sectors/semiconductors/iv-refresh', headers=action_headers())
        assert cooldown.status_code == 200
        assert cooldown.json()['refresh']['status'] == 'cooldown'
        assert 0 < cooldown.json()['refresh']['retry_after_seconds'] <= 300


def test_partial_strength_fallback_still_scans_every_member(monkeypatch, client):
    now = time.time()
    partial = sectors._rank_iv_rows('semiconductors', [
        {'ticker': 'AMD', 'iv': .3, 'as_of': stamp(now), 'provider': 'Yahoo/yfinance'},
    ])
    partial['snapshot_source'] = 'strength_worker'
    monkeypatch.setattr(sectors, '_read_strength_sector_iv_snapshot', lambda *a, **k: partial)
    monkeypatch.setattr(sectors.massive, 'configured', lambda: False)
    calls = []
    def snapshot(ticker):
        calls.append(ticker)
        return {'atm_iv': .25, 'as_of': stamp(now)}
    monkeypatch.setattr(sectors.yahoo, 'get_stock_iv_snapshot', snapshot)
    monkeypatch.setattr(sectors.yahoo, 'get_last_price', lambda ticker: 100)
    first = client.get('/api/sectors/semiconductors/iv-ranking').json()
    assert first['success_count'] == 1
    assert first['refresh']['status'] == 'queued'
    asyncio.run(refresh.run_refresh_batch(schedule=False))
    assert len(calls) == 14
    assert client.get('/api/sectors/semiconductors/iv-ranking').json()['success_count'] == 14


@pytest.mark.parametrize("initial_snapshot", ["fresh", "stale", "missing"])
def test_publication_during_refresh_state_handoff_returns_new_snapshot(
    monkeypatch,
    initial_snapshot,
):
    now = time.time()
    sector_id = "semiconductors"
    old_age = 60 if initial_snapshot == "fresh" else 2 * 86400
    old = sectors._rank_iv_rows(
        sector_id,
        [{"ticker": "AMD", "iv": .3, "as_of": stamp(now - old_age)}],
    )
    new = sectors._rank_iv_rows(
        sector_id,
        [{"ticker": "AMD", "iv": .31, "as_of": stamp(now - 30)}],
    )
    if initial_snapshot != "missing":
        sectors._write_sector_iv_snapshot(
            sector_id,
            old,
            saved_at=now - old_age,
            snapshot_origin="worker",
        )

    store = refresh.default_store()
    assert store.request(sector_id)["status"] == "queued"
    claim = store.claim()
    assert claim is not None

    first_read_finished = threading.Event()
    allow_request_to_continue = threading.Event()
    original_read = sectors._read_sector_iv_snapshot
    observed_reads = []

    def controlled_read(*args, **kwargs):
        captured = original_read(*args, **kwargs)
        observed_reads.append(captured)
        if len(observed_reads) == 1:
            first_read_finished.set()
            assert allow_request_to_continue.wait(timeout=5)
        return captured

    monkeypatch.setattr(sectors, "_read_sector_iv_snapshot", controlled_read)
    monkeypatch.setattr(sectors, "_read_strength_sector_iv_snapshot", lambda *a, **k: None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            lambda: asyncio.run(sectors._request_iv_payload(sector_id))
        )
        assert first_read_finished.wait(timeout=5)
        assert store.finish(
            *claim,
            publish=lambda: sectors._write_sector_iv_snapshot(
                sector_id,
                new,
                saved_at=time.time(),
                snapshot_origin="worker",
            ),
        )
        allow_request_to_continue.set()
        result = pending.result(timeout=5)

    assert len(observed_reads) == 2
    assert result["as_of"] == new["as_of"]
    assert result["rankings"] == new["rankings"]
    expected_status = "idle" if initial_snapshot == "fresh" else "cooldown"
    assert result["refresh"]["status"] == expected_status
    assert result["refresh"]["completed_at"] is not None
    if expected_status == "idle":
        assert result["refresh"]["retry_after_seconds"] == 0
    else:
        assert 0 < result["refresh"]["retry_after_seconds"] <= refresh.MIN_REFRESH_SECONDS


def test_57_day_source_rejected_even_if_file_just_saved(monkeypatch, client):
    now = time.time()
    old = sectors._rank_iv_rows('semiconductors', [
        {'ticker': 'AMD', 'iv': .3, 'as_of': stamp(now - 57 * 86400)},
    ])
    sectors._write_sector_iv_snapshot('semiconductors', old, saved_at=now)
    monkeypatch.setattr(sectors, '_read_strength_sector_iv_snapshot', lambda *a, **k: old)
    result = client.get('/api/sectors/semiconductors/iv-ranking').json()
    assert result['rankings'] == []
    assert result['as_of'] is None
    assert result['source_status'] == 'insufficient_data'
    assert result['refresh']['status'] == 'queued'


def test_single_sample_can_be_persisted_with_null_rank_and_failures(monkeypatch, client):
    now = time.time()
    monkeypatch.setattr(sectors.massive, 'configured', lambda: False)
    monkeypatch.setattr(sectors.yahoo, 'get_last_price', lambda ticker: None)
    monkeypatch.setattr(sectors.yahoo, 'get_stock_iv_snapshot', lambda ticker: {
        'atm_iv': .3 if ticker == 'AMD' else None, 'as_of': stamp(now),
    })
    client.get('/api/sectors/semiconductors/iv-ranking')
    assert asyncio.run(refresh.run_refresh_batch(schedule=False))['completed'] == 1
    result = client.get('/api/sectors/semiconductors/iv-ranking').json()
    assert result['rankings'][0]['sector_iv_rank'] is None
    assert result['success_count'] == 1
    assert len(result['failed_symbols']) == 13
    assert result['source_status'] == 'degraded'
    assert result['refresh']['status'] == 'idle'


def test_cooldown_restart_concurrent_claims_and_fencing(tmp_path):
    clock = [1_790_000_000.]
    path = tmp_path / 'refresh.sqlite'
    stores = [refresh.SectorIVRefreshStore(path, clock=lambda: clock[0]) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = list(pool.map(lambda store: store.request('semiconductors'), stores))
        claims = list(pool.map(lambda store: store.claim(), stores))
    assert len({state['requested_at'] for state in states}) == 1
    claimed = [claim for claim in claims if claim]
    assert len(claimed) == 1
    sector_id, token = claimed[0]
    restarted = refresh.SectorIVRefreshStore(path, clock=lambda: clock[0])
    assert restarted.status(sector_id)['status'] == 'running'
    assert restarted.finish(sector_id, token)
    assert restarted.request(sector_id)['status'] == 'cooldown'
    clock[0] += 301
    assert restarted.request(sector_id)['status'] == 'queued'
    new_claim = restarted.claim()
    clock[0] += refresh.LEASE_SECONDS + 1
    assert restarted.status(sector_id)['error_code'] == 'worker_interrupted'
    assert not restarted.finish(*new_claim, publish=lambda: pytest.fail('expired token published'))
    assert restarted.request(sector_id)['status'] == 'queued'


def test_all_failed_preserves_old_bytes_and_source_time_then_retries(monkeypatch, client):
    clock = [time.time()]
    monkeypatch.setattr(refresh.time, 'time', lambda: clock[0])
    monkeypatch.setattr(sectors, '_read_strength_sector_iv_snapshot', lambda *a, **k: None)
    old_time = clock[0] - 2 * 86400
    old = sectors._rank_iv_rows('semiconductors', [{'ticker': 'AMD', 'iv': .3, 'as_of': stamp(old_time)}])
    sectors._write_sector_iv_snapshot('semiconductors', old, saved_at=clock[0] - 86400)
    path = sectors._sector_iv_snapshot_path('semiconductors')
    original = path.read_bytes()
    async def failed(_sector_id):
        return sectors._rank_iv_rows('semiconductors', [])
    monkeypatch.setattr(sectors, '_iv_ranking_payload', failed)
    client.get('/api/sectors/semiconductors/iv-ranking')
    assert asyncio.run(refresh.run_refresh_batch(schedule=False))['failed'] == 1
    result = client.get('/api/sectors/semiconductors/iv-ranking').json()
    assert result['refresh']['status'] == 'failed'
    assert result['refresh']['retry_after_seconds'] == 300
    assert result['as_of'] == stamp(old_time)
    assert result['_stale'] is True
    assert path.read_bytes() == original
    clock[0] += 301
    refresh.default_store().schedule_due()
    assert refresh.default_store().status('semiconductors')['status'] == 'queued'


def test_queue_reports_offline_worker_and_recovers(tmp_path):
    clock = [1_790_000_000.]
    store = refresh.SectorIVRefreshStore(tmp_path / 'queue.sqlite', clock=lambda: clock[0])
    store.request('semiconductors')
    clock[0] += refresh.QUEUE_TIMEOUT_SECONDS + 1
    assert store.status('semiconductors')['status'] == 'failed'
    assert store.status('semiconductors')['error_code'] == 'worker_unavailable'
    assert store.request('semiconductors')['status'] == 'failed'
    clock[0] += 301
    store.schedule_due()
    assert store.status('semiconductors')['status'] == 'queued'


def test_schedule_covers_catalog_and_market_open_shortens_deadline(monkeypatch, tmp_path):
    clock = [1_790_000_000.]
    monkeypatch.setattr(refresh, 'refresh_interval', lambda now: 21600)
    store = refresh.SectorIVRefreshStore(tmp_path / 'queue.sqlite', clock=lambda: clock[0])
    store.schedule_due()
    assert len(sectors.SECTORS) == 24
    for sector in sectors.SECTORS:
        assert store.status(sector)['status'] == 'queued'
    while claim := store.claim():
        store.finish(*claim)
    clock[0] += 901
    store.schedule_due()
    assert store.status('semiconductors')['status'] == 'idle'
    monkeypatch.setattr(refresh, 'refresh_interval', lambda now: 900)
    store.schedule_due()
    assert all(store.status(sector)['status'] == 'queued' for sector in sectors.SECTORS)


def test_worker_task_executes_queue(monkeypatch):
    observed = []
    async def run():
        observed.append(True)
        return {'completed': 1, 'failed': 0}
    monkeypatch.setattr(refresh, 'run_refresh_batch', run)
    outcome = asyncio.run(SectorIVTask()())
    assert observed == [True]
    assert outcome.details['completed'] == 1
    assert outcome.next_delay_seconds == 5


def test_public_post_origin_catalog_and_rate_limit(client):
    assert client.post('/api/sectors/semiconductors/iv-refresh').status_code == 403
    assert client.post('/api/sectors/not-a-sector/iv-refresh', headers=action_headers()).status_code == 404
    for _ in range(8):
        assert client.post('/api/sectors/semiconductors/iv-refresh', headers=action_headers()).status_code == 202
    limited = client.post('/api/sectors/semiconductors/iv-refresh', headers=action_headers())
    assert limited.status_code == 429
    assert int(limited.headers['Retry-After']) > 0
    assert client.get('/api/sectors/semiconductors/iv-ranking').status_code == 200


def test_public_demand_prioritizes_scheduled_queue_without_duplicate_request(tmp_path):
    store = refresh.SectorIVRefreshStore(tmp_path / "queue.sqlite")
    store.schedule_due()
    original = store.status("semiconductors")["requested_at"]
    store.request("semiconductors")
    store.request("semiconductors")
    assert store.claim()[0] == "semiconductors"
    assert store.status("semiconductors")["requested_at"] == original


def test_worker_drops_expired_member_and_reranks_remaining_sample(monkeypatch, client):
    now = time.time()
    async def rows(sector_id):
        return [
            {"ticker": "AMD", "iv": .3, "as_of": stamp(now - 30)},
            {"ticker": "NVDA", "iv": .6, "as_of": stamp(now - 57 * 86400)},
        ]
    monkeypatch.setattr(sectors, "_sector_iv_rows", rows)
    client.get("/api/sectors/semiconductors/iv-ranking")
    assert asyncio.run(refresh.run_refresh_batch(schedule=False))["completed"] == 1
    result = client.get("/api/sectors/semiconductors/iv-ranking").json()
    assert [row["ticker"] for row in result["rankings"]] == ["AMD"]
    assert result["rankings"][0]["sector_iv_rank"] is None
    assert set(result["failed_symbols"]) == set(sectors.SECTORS["semiconductors"]["tickers"]) - {"AMD"}
    assert result["as_of"] == stamp(now - 30)


def test_regular_and_closed_sessions_use_expected_refresh_interval():
    assert refresh.refresh_interval(datetime(2026, 9, 18, 16, tzinfo=timezone.utc).timestamp()) == 900
    assert refresh.refresh_interval(datetime(2026, 9, 20, 16, tzinfo=timezone.utc).timestamp()) == 21600


def test_closed_market_snapshot_remains_fresh_beyond_old_one_hour_limit(monkeypatch, client):
    now = datetime(2026, 9, 20, 16, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(sectors.time, "time", lambda: now)
    payload = sectors._rank_iv_rows("semiconductors", [{"ticker": "AMD", "iv": .3, "as_of": stamp(now - 7200)}])
    sectors._write_sector_iv_snapshot("semiconductors", payload, saved_at=now - 7200)
    result = client.get("/api/sectors/semiconductors/iv-ranking").json()
    assert result["_stale"] is False
    assert result["refresh"]["status"] == "idle"
    assert result["as_of"] == stamp(now - 7200)


def test_failures_back_off_and_remain_bounded(tmp_path):
    clock = [1_790_000_000.]
    store = refresh.SectorIVRefreshStore(tmp_path / 'queue.sqlite', clock=lambda: clock[0])
    for expected in (300, 600, 1200, 2400, 3600, 3600):
        assert store.request('semiconductors')['status'] == 'queued'
        store.finish(*store.claim(), error_code='sector_iv_refresh_failed')
        assert store.status('semiconductors')['retry_after_seconds'] == expected
        clock[0] += expected + 1


def test_worker_timeout_leaves_retryable_status(monkeypatch):
    async def hanging(sector_id):
        await asyncio.Event().wait()
    monkeypatch.setattr(sectors, '_iv_ranking_payload', hanging)
    monkeypatch.setattr(refresh, 'SCAN_TIMEOUT_SECONDS', .01)
    store = refresh.default_store()
    store.request('semiconductors')
    assert asyncio.run(refresh.run_refresh_batch(schedule=False))['failed'] == 1
    state = store.status('semiconductors')
    assert state['status'] == 'failed'
    assert state['error_code'] == 'sector_iv_timeout'
    assert state['retry_after_seconds'] > 0


def test_worker_cancelled_scan_leaves_retryable_status(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        async def hanging(sector_id):
            started.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(sectors, '_iv_ranking_payload', hanging)
        store = refresh.default_store()
        store.request('semiconductors')
        task = asyncio.create_task(refresh.run_refresh_batch(schedule=False))
        await started.wait()
        assert store.status('semiconductors')['status'] == 'running'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.status('semiconductors')['status'] == 'failed'
        assert store.status('semiconductors')['error_code'] == 'worker_interrupted'
    asyncio.run(scenario())
