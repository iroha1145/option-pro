"""Existing job stores warm before serving, without provider or job execution."""
import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    from app import config, main, personal_config
    from app.services import massive, realtime_quotes
    from app.services.catalysts import hotspot_reads
    events = []
    settings = SimpleNamespace(quotes_enabled=False, quotes_signals_enabled=False,
                               openai_job_db_path=tmp_path / 'jobs.db')
    features = SimpleNamespace(catalyst_mode='read')
    class Pool:
        def start(self): events.append('pool_start')
        def close(self): events.append('pool_close')
    class Hub:
        def __init__(self, *args, **kwargs): pass
        async def start(self): events.append('hub_start')
        async def close(self): events.append('hub_close')
    async def logos_start(): events.append('logos_start')
    async def logos_close(): events.append('logos_close')
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    monkeypatch.setattr(personal_config, 'get_personal_config', lambda: SimpleNamespace(features=features))
    monkeypatch.setattr(hotspot_reads, 'HotspotReadPool', Pool)
    monkeypatch.setattr(realtime_quotes, 'QuoteHub', Hub)
    monkeypatch.setattr(main.stocks, 'start_company_logo_client', logos_start)
    monkeypatch.setattr(main.stocks, 'close_company_logo_client', logos_close)
    monkeypatch.setattr(massive, 'close', lambda: events.append('massive_close'))
    return main, settings, features, Pool, events, SimpleNamespace(state=SimpleNamespace())


def test_existing_store_initialized_before_serving_and_fast_reopen(lifecycle, monkeypatch):
    from app.services.ai_jobs.repository import AIJobRepository
    main, settings, _, _, events, application = lifecycle
    # Existing legacy SQLite file: initialization must finish before yield.
    sqlite3.connect(settings.openai_job_db_path).close()
    async def run():
        async with main._lifespan(application):
            with sqlite3.connect(settings.openai_job_db_path) as conn:
                assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='ai_jobs'").fetchone()
            # A new request-side repository must reuse the initialized schema.
            monkeypatch.setattr(AIJobRepository, '_initialize_database', lambda self: pytest.fail('repeated migration'))
            AIJobRepository(settings.openai_job_db_path).ensure_initialized()
    asyncio.run(run())
    assert events[-4:] == ['pool_close', 'hub_close', 'logos_close', 'massive_close']


@pytest.mark.parametrize('mode', ['read', 'off'])
def test_missing_store_not_created_and_off_does_not_touch_store(lifecycle, monkeypatch, mode):
    from app.services.ai_jobs.repository import AIJobRepository
    main, settings, features, _, _, application = lifecycle
    features.catalyst_mode = mode
    monkeypatch.setattr(AIJobRepository, 'ensure_initialized', lambda self: pytest.fail('unexpected initialization'))
    if mode == 'off':
        monkeypatch.setattr(main, '_prepare_existing_catalyst_jobs', lambda path: pytest.fail('off touched store'))
    async def run():
        async with main._lifespan(application): pass
    asyncio.run(run())
    assert not settings.openai_job_db_path.exists()


@pytest.mark.parametrize('error', [
    PermissionError('secret path'),
    sqlite3.DatabaseError('bad store'),
    RuntimeError('ai_job_manual_cooldown_index_checksum_mismatch'),
    RuntimeError('ai_stock_quota_schema_checksum_mismatch'),
    RuntimeError('ai_earnings_lock_schema_checksum_mismatch'),
    RuntimeError('model_budget_schema_checksum_mismatch'),
    RuntimeError('model_budget_history_invalid'),
    ValueError('invalid legacy value'),
    TypeError('invalid legacy type'),
])
def test_expected_store_failures_degrade_with_safe_diagnostic(lifecycle, monkeypatch, error):
    from app import failure_diagnostics
    from app.services.ai_jobs.repository import AIJobRepository
    main, settings, _, _, events, application = lifecycle
    settings.openai_job_db_path.touch()
    def fail(self): raise error
    seen = []
    monkeypatch.setattr(AIJobRepository, 'ensure_initialized', fail)
    monkeypatch.setattr(failure_diagnostics, 'record_fallback_failure', lambda stage, exc: seen.append((stage, exc)))
    async def run():
        async with main._lifespan(application): events.append('serving')
    asyncio.run(run())
    assert ('catalyst_job_startup', error) in seen
    assert 'serving' in events


@pytest.mark.parametrize('cancel', [False, True])
def test_parallel_startup_drains_work_before_failure_or_repeated_cancel(lifecycle, monkeypatch, cancel):
    main, _, _, Pool, events, application = lifecycle
    entered_pool, entered_db, release = threading.Event(), threading.Event(), threading.Event()
    def start(self):
        entered_pool.set()
        assert entered_db.wait(3), 'startup not parallel'
        if not cancel: raise RuntimeError('pool failed')
    def prepare(path):
        entered_db.set()
        assert entered_pool.wait(3), 'startup not parallel'
        assert release.wait(3)
        events.append('db_finished')
    monkeypatch.setattr(Pool, 'start', start)
    monkeypatch.setattr(main, '_prepare_existing_catalyst_jobs', prepare)
    async def run():
        async def serve():
            async with main._lifespan(application): pytest.fail('must not serve')
        task = asyncio.create_task(serve())
        try:
            assert await asyncio.to_thread(entered_db.wait, 2)
            assert await asyncio.to_thread(entered_pool.wait, 2)
            if cancel:
                task.cancel()
                await asyncio.sleep(.01)
                task.cancel()
            await asyncio.sleep(.02)
            assert not task.done()
            assert 'pool_close' not in events
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError): await task
    asyncio.run(run())
    assert events.index('db_finished') < events.index('pool_close')
    assert events[-3:] == ['hub_close', 'logos_close', 'massive_close']
    assert application.state.quote_hub is None
