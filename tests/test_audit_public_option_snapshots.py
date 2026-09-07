from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from fastapi import HTTPException

from app import public_option_data as public
from app.access import request_owner_access_context
from app.api import options
from app.worker.tasks import PublicHomeTask

_real_trusted_symbol = public._trusted_symbol

NOW = datetime(2026, 9, 7, 15, tzinfo=timezone.utc).timestamp()
EXP = "2026-09-11"


def expirations():
    return {"ticker": "AAPL", "expirations": [EXP, "2026-09-18"], "options_status": "supported",
            "as_of": "2026-09-07T15:00:00Z", "retryable": False, "source_status": "active"}


def chain(expiration=EXP):
    return {"ticker": "AAPL", "expiration": expiration, "as_of": "2026-09-07T15:00:00Z",
            "underlying_price": 100, "calls": [{"strike": 100, "implied_volatility": .2}], "puts": [], "alerts": []}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(public, "default_option_root", lambda: tmp_path)
    monkeypatch.setattr(public, "_trusted_symbol", lambda symbol, _root: symbol == "AAPL")
    monkeypatch.setattr(public.time, "time", lambda: NOW)
    options.cache.clear()
    options._option_failure_cache.clear()
    yield
    options.cache.clear()
    options._option_failure_cache.clear()


def test_worker_prepares_expirations_and_default_chain_for_anonymous_http_without_provider_calls(tmp_path, monkeypatch):
    calls = []

    async def loader(symbol, expiry):
        calls.append((symbol, expiry))
        return chain(expiry) if expiry else expirations()

    async def run():
        with request_owner_access_context(False):
            with pytest.raises(HTTPException) as error:
                await options.expirations("AAPL")
            assert error.value.detail["code"] == "public_option_snapshot_pending"
            assert calls == []
        worker = public.PublicOptionDataRefresh(root=tmp_path, loader=loader, target_reader=lambda _entries: ["AAPL"],
                                               clock=lambda: NOW, start_interval_seconds=0)
        await worker.poll({})
        await worker._consumer
        await worker.aclose()
        options.cache.clear()
        monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", lambda *_: pytest.fail("HTTP contacted provider"))
        monkeypatch.setattr(options.yahoo, "get_option_chain", lambda *_: pytest.fail("HTTP contacted provider"))
        with request_owner_access_context(False):
            dates = await options.expirations("AAPL")
            result = await options.option_chain("AAPL", EXP)
        assert dates["expirations"] == [EXP, "2026-09-18"]
        assert result["calls"][0]["strike"] == 100
        assert result["snapshot_saved_at"] == "2026-09-07T15:00:00Z"
        assert result["cache_stale"] is False

    asyncio.run(run())
    assert calls == [("AAPL", ""), ("AAPL", EXP)]


def test_persisted_snapshot_is_readable_by_a_new_process(tmp_path):
    public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    backend = Path(__file__).resolve().parents[1] / "backend"
    program = "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from app.public_option_data import read_option_snapshot; print(json.dumps(read_option_snapshot('AAPL',root=Path(sys.argv[2]),now=float(sys.argv[3]))))"
    result = subprocess.run([sys.executable, "-c", program, str(backend), str(tmp_path), str(NOW + 1)],
                            capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(result.stdout)["expirations"] == [EXP, "2026-09-18"]


def test_memory_expiry_keeps_honestly_stale_saved_data_and_worker_recovers(tmp_path, monkeypatch):
    now = [NOW]
    public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    public.write_option_snapshot("AAPL", chain(), EXP, root=tmp_path, now=NOW)
    now[0] += 1000
    monkeypatch.setattr(public.time, "time", lambda: now[0])

    async def loader(_symbol, expiry):
        payload = chain(expiry) if expiry else expirations()
        payload["as_of"] = public._iso(now[0])
        if expiry:
            payload["underlying_price"] = 105
        return payload

    async def run():
        with request_owner_access_context(False):
            stale = await options.option_chain("AAPL", EXP)
            assert stale["_stale"] and stale["cache_stale"] and stale["source_status"] == "stale"
            assert stale["as_of"] == "2026-09-07T15:00:00Z"
        worker = public.PublicOptionDataRefresh(root=tmp_path, loader=loader, target_reader=lambda _: ["AAPL"],
                                               clock=lambda: now[0], start_interval_seconds=0, phase_reader=lambda _: "regular")
        await worker.poll({})
        await worker._consumer
        await worker.aclose()
        with request_owner_access_context(False):
            fresh = await options.option_chain("AAPL", EXP)
        assert fresh["underlying_price"] == 105 and fresh["cache_stale"] is False
    asyncio.run(run())


def test_only_known_saved_expiration_can_enqueue_and_unknown_symbol_cannot_probe(tmp_path):
    assert not public.request_option_snapshot("UNTRUSTED", root=tmp_path, now=NOW)
    assert not public.request_option_snapshot("^GSPC", root=tmp_path, now=NOW, trusted=True)
    assert not public.request_option_snapshot("AAPL", EXP, root=tmp_path, now=NOW)
    public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    assert public.request_option_snapshot("AAPL", "2026-09-18", root=tmp_path, now=NOW)
    assert not public.request_option_snapshot("AAPL", "2030-12-20", root=tmp_path, now=NOW)
    with pytest.raises(ValueError):
        public.request_option_snapshot("../../escape", root=tmp_path)


def test_other_saved_expiration_is_prepared_on_demand(tmp_path):
    public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    public.request_option_snapshot("AAPL", "2026-09-18", root=tmp_path, now=NOW)
    calls = []
    async def loader(_symbol, expiry):
        calls.append(expiry)
        return chain(expiry) if expiry else expirations()
    async def run():
        worker = public.PublicOptionDataRefresh(root=tmp_path, loader=loader, target_reader=lambda _: [],
                                               clock=lambda: NOW, start_interval_seconds=0)
        await worker.poll({})
        await worker._consumer
        await worker.aclose()
    asyncio.run(run())
    assert calls == ["2026-09-18"]
    assert public.read_option_snapshot("AAPL", "2026-09-18", root=tmp_path, now=NOW)


def test_persisted_failure_cooldown_survives_worker_restart(tmp_path):
    now = [NOW]
    calls = []
    async def broken(_symbol, _expiry):
        calls.append("failure")
        raise RuntimeError("temporary provider outage")
    async def run_once():
        worker = public.PublicOptionDataRefresh(root=tmp_path, loader=broken, target_reader=lambda _: ["AAPL"],
                                               clock=lambda: now[0], start_interval_seconds=0)
        await worker.poll({})
        await worker._consumer
        await worker.aclose()
    asyncio.run(run_once())
    now[0] += 10
    asyncio.run(run_once())
    assert calls == ["failure"]
    now[0] += 51
    asyncio.run(run_once())
    assert calls == ["failure", "failure"]


def test_snapshot_and_demand_storage_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(public, "MAX_RESOURCES", 2)
    monkeypatch.setattr(public, "MAX_DEMANDS", 2)
    for i, symbol in enumerate(["AAA", "BBB", "CCC"]):
        public.write_option_snapshot(symbol, {**expirations(), "ticker": symbol}, root=tmp_path, now=NOW + i)
        public.request_option_snapshot(symbol, root=tmp_path, now=NOW + i, trusted=True)
    with public._connection(tmp_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM demand").fetchone()[0] == 2
    assert public.read_option_snapshot("AAA", root=tmp_path, now=NOW + 3) is None


def test_stale_provider_result_and_older_write_do_not_renew_saved_quote(tmp_path):
    public.write_option_snapshot("AAPL", chain(), EXP, root=tmp_path, now=NOW)
    public.write_option_snapshot("AAPL", {**chain(), "underlying_price": 1}, EXP, root=tmp_path, now=NOW - 10)
    public.write_option_snapshot("AAPL", {**chain(), "_stale": True}, EXP, root=tmp_path, now=NOW + 500)
    result = public.read_option_snapshot("AAPL", EXP, root=tmp_path, now=NOW + 700)
    assert result["underlying_price"] == 100
    assert result["snapshot_saved_at"] == public._iso(NOW)
    assert result["cache_stale"]


def test_expired_dates_and_overage_snapshots_are_not_returned(tmp_path):
    payload = {**expirations(), "expirations": ["2026-09-04", EXP]}
    public.write_option_snapshot("AAPL", payload, root=tmp_path, now=NOW)
    assert public.read_option_snapshot("AAPL", root=tmp_path, now=NOW)["expirations"] == [EXP]
    assert public.read_option_snapshot("AAPL", root=tmp_path, now=NOW + public.MAX_AGE_SECONDS + 1) is None


def test_damaged_or_oversize_snapshot_is_not_published(tmp_path, monkeypatch):
    monkeypatch.setattr(public, "MAX_SNAPSHOT_BYTES", 10)
    with pytest.raises(ValueError):
        public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    monkeypatch.setattr(public, "MAX_SNAPSHOT_BYTES", 10240)
    with pytest.raises(ValueError):
        public.write_option_snapshot("AAPL", {**chain(), "underlying_price": float("nan")}, EXP, root=tmp_path, now=NOW)
    public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    with public._connection(tmp_path, write=True) as connection:
        connection.execute("UPDATE snapshots SET payload='not json'")
    assert public.read_option_snapshot("AAPL", root=tmp_path, now=NOW) is None


def test_storage_symlinks_cannot_redirect_writes(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (tmp_path / "public-options-v1.sqlite").symlink_to(outside)
    with pytest.raises(ValueError):
        public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    assert outside.read_text() == "untouched"


def test_worker_close_drains_the_active_resource(tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def loader(_symbol, _expiry):
            started.set()
            await release.wait()
            return expirations()
        worker = public.PublicOptionDataRefresh(root=tmp_path, loader=loader, target_reader=lambda _: ["AAPL"],
                                               clock=lambda: NOW, start_interval_seconds=0)
        await worker.poll({})
        await started.wait()
        closing = asyncio.create_task(worker.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        assert public.read_option_snapshot("AAPL", root=tmp_path, now=NOW)
        assert public.read_option_snapshot("AAPL", EXP, root=tmp_path, now=NOW) is None
    asyncio.run(run())


def test_authorized_http_read_persists_for_public_read_after_memory_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", lambda _: expirations())
    async def run():
        with request_owner_access_context(True):
            await options.expirations("AAPL")
        options.cache.clear()
        monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", lambda _: pytest.fail("unexpected provider pull"))
        with request_owner_access_context(False):
            assert (await options.expirations("AAPL"))["expirations"] == [EXP, "2026-09-18"]
    asyncio.run(run())


def test_empty_provider_chain_does_not_replace_last_good_snapshot(tmp_path, monkeypatch):
    public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    public.write_option_snapshot("AAPL", chain(), EXP, root=tmp_path, now=NOW)
    monkeypatch.setattr(public.time, "time", lambda: NOW + 1000)
    async def empty(_symbol, expiry):
        return {"calls": [], "puts": [], "source_status": "insufficient_data"} if expiry else expirations()
    async def run():
        worker = public.PublicOptionDataRefresh(root=tmp_path, loader=empty, target_reader=lambda _: ["AAPL"],
                                               clock=lambda: NOW + 1000, start_interval_seconds=0, phase_reader=lambda _: "regular")
        await worker.poll({})
        await worker._consumer
        await worker.aclose()
        with request_owner_access_context(False):
            result = await options.option_chain("AAPL", EXP)
        assert result["calls"] == chain()["calls"]
        assert result["cache_stale"] and result["snapshot_saved_at"] == public._iso(NOW)
        assert public.option_preparation_status("AAPL", EXP, root=tmp_path, now=NOW + 1001)["status"] == "cooling"
    asyncio.run(run())


def test_old_source_time_stays_stale_after_recent_save(tmp_path):
    public.write_option_snapshot("AAPL", chain(), EXP, root=tmp_path, now=NOW + 1800)
    result = public.read_option_snapshot("AAPL", EXP, root=tmp_path, now=NOW + 1801)
    assert result["cache_stale"]
    assert result["as_of"] == chain()["as_of"]


def test_closed_market_reuses_saved_options_without_ten_minute_provider_work(tmp_path):
    public.write_option_snapshot("AAPL", expirations(), root=tmp_path, now=NOW)
    public.write_option_snapshot("AAPL", chain(), EXP, root=tmp_path, now=NOW)
    async def forbidden(*_):
        pytest.fail("closed market should reuse the saved snapshot")
    async def run():
        worker = public.PublicOptionDataRefresh(root=tmp_path, loader=forbidden, target_reader=lambda _: ["AAPL"],
                                               clock=lambda: NOW + 1800, start_interval_seconds=0, phase_reader=lambda _: "closed")
        await worker.poll({})
        await worker._consumer
        await worker.aclose()
    asyncio.run(run())


def test_one_queue_close_failure_does_not_abandon_the_other_queue():
    async def run():
        entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class BrokenStockQueue:
            async def aclose(self):
                raise RuntimeError("stock close failed")
        class OptionQueue:
            async def aclose(self):
                entered.set()
                await release.wait()
                completed.set()
        task = PublicHomeTask(None, stock_data_refresh=BrokenStockQueue(), option_data_refresh=OptionQueue())
        closing = asyncio.create_task(task.aclose())
        await entered.wait()
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(RuntimeError, match="stock close failed"):
            await closing
        assert completed.is_set()
    asyncio.run(run())


def test_unusual_snapshot_accepts_iv_provenance_and_normalizes_legacy_placeholders(tmp_path):
    from app.public_home_snapshot import create_public_home_entry, read_public_home_resource, validate_public_home_payload, public_home_resource_parameters
    from tests.test_public_home_snapshot import _payload

    payload = _payload("unusual", NOW)
    payload["results"][0].update(implied_volatility=None, iv_source="missing")
    assert validate_public_home_payload("unusual", payload)["results"][0]["iv_source"] == "missing"
    entry = create_public_home_entry("unusual", payload, saved_at=NOW,
                                     parameters=public_home_resource_parameters("unusual", now=NOW))
    # Reproduce an already-saved pre-fix snapshot, bypassing today's writer.
    entry["payload"]["results"][0].pop("iv_source")
    entry["payload"]["results"][0]["implied_volatility"] = .00001
    path = tmp_path / "legacy-public-home.json"
    path.write_text(json.dumps({"version": 1, "resources": {"unusual": entry}}))
    read = read_public_home_resource("unusual", path=path, now=NOW + 1,
                                     parameters={"type": "all", "min_vol_oi": 1.0})
    assert read["results"][0]["implied_volatility"] is None
    assert read["results"][0]["iv_source"] == "missing"
    payload["results"][0]["provider_token"] = "must not be published"
    with pytest.raises(ValueError):
        validate_public_home_payload("unusual", payload)


@pytest.mark.parametrize("stamp", ["2026-09-07T14:59:00Z", None, "2026-09-07T15:00:00Z"])
def test_later_save_cannot_replace_known_quote_with_older_unknown_or_same_source_time(tmp_path, stamp):
    public.write_option_snapshot("AAPL", {**chain(), "underlying_price": 110}, EXP, root=tmp_path, now=NOW)
    public.write_option_snapshot("AAPL", {**chain(), "as_of": stamp, "underlying_price": 100}, EXP, root=tmp_path, now=NOW + 5)
    result = public.read_option_snapshot("AAPL", EXP, root=tmp_path, now=NOW + 6)
    assert result["underlying_price"] == 110
    assert result["snapshot_saved_at"] == public._iso(NOW)


def test_concurrent_option_writers_preserve_the_latest_source_timestamp(tmp_path):
    public.write_option_snapshot("AAPL", chain(), EXP, root=tmp_path, now=NOW)
    def write(sequence):
        public.write_option_snapshot("AAPL", {**chain(), "as_of": public._iso(NOW + sequence), "underlying_price": 100 + sequence},
                                     EXP, root=tmp_path, now=NOW + 100 - sequence)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, [4, 1, 3, 2]))
    result = public.read_option_snapshot("AAPL", EXP, root=tmp_path, now=NOW + 101)
    assert result["underlying_price"] == 104
    assert result["as_of"] == public._iso(NOW + 4)


def test_an_authorized_manual_stock_snapshot_qualifies_for_bounded_option_preparation(tmp_path, monkeypatch):
    from app.stock_pull_snapshot import write_stock_pull_resources
    monkeypatch.setattr(public, "_trusted_symbol", _real_trusted_symbol)
    write_stock_pull_resources("AUDIT", {"overview": ({"ticker": "AUDIT", "price": 100}, NOW)},
                               path=tmp_path / "stock-pull-snapshots-v1.json", now=NOW)
    assert public.request_option_snapshot("AUDIT", root=tmp_path, now=NOW)
    assert not public.request_option_snapshot("UNVERIFIED", root=tmp_path, now=NOW)
