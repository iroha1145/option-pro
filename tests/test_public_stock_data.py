from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import public_stock_data as public
from app.stock_pull_snapshot import read_stock_pull_resource, write_stock_pull_resources
from app.worker.tasks import PublicHomeTask


NOW = datetime(2026, 9, 4, 16, tzinfo=timezone.utc).timestamp()


def _completed():
    return {
        "status": "completed", "persistence_status": "completed",
        "resources": {key: {"status": "available", "persisted": True} for key in ("overview", "daily_chart", "signals")},
    }


def _write_bundle(path: Path, ticker: str, now: float, resources=None) -> None:
    values = {
        "overview": {"ticker": ticker, "price": 101.0},
        "daily_chart": {
            "ticker": ticker, "range": "1d", "price_adjustment": "raw",
            "bars": [{"t": int(now - 86400), "o": 100, "h": 102, "l": 99, "c": 101, "v": 10}],
        },
        "signals": {key: {"value": 1.0} for key in ("rsi14", "return_20d", "macd_hist")},
    }
    write_stock_pull_resources(
        ticker, {key: (value, now) for key, value in values.items() if resources is None or key in resources},
        path=path, now=now,
    )


def _queue(tmp_path, puller, now=None, **kwargs):
    return public.PublicStockDataRefresh(
        root=tmp_path, puller=puller, clock=now or (lambda: NOW),
        current_reader=lambda: [], default_reader=lambda: [],
        phase_reader=lambda _now: "regular", start_interval_seconds=0, **kwargs,
    )


def test_home_selection_matches_market_date_featured_order_movers_and_radar():
    # It is still Sept 3 in New York at this UTC instant.
    midnight = datetime(2026, 9, 4, 2, tzinfo=timezone.utc).timestamp()
    rows = [{"ticker": f"P{i}", "change_percent": value} for i, value in enumerate([1, -9, 2, -7, 3, 5, 4, None])]
    earnings = [
        {"ticker": "OLD", "earnings_date": "2026-09-02", "public_featured": True},
        {"ticker": "P0", "earnings_date": "2026-09-03", "market_cap": 3},
        {"ticker": "FEATURED", "earnings_date": "2026-09-03", "market_cap": 8, "public_featured": True},
        *[{"ticker": f"E{i}", "earnings_date": "2026-09-03", "market_cap": 20 - i} for i in range(8)],
    ]
    targets = public.public_stock_targets(
        {"watchlist": {"payload": {"groups": [{"stocks": rows}]}}, "earnings": {"payload": {"earnings": earnings}}},
        current_tickers=["R0", "R0", *[f"R{i}" for i in range(1, 10)]],
        default_tickers=["DEFAULT", "P0"], requested_tickers=["HISTORY"], now=midnight,
    )
    assert list(targets)[:9] == ["NVDA", *[f"R{i}" for i in range(8)]]
    assert list(targets)[9:15] == ["FEATURED", "P0", "E0", "E1", "E2", "E3"]
    assert targets["P1"] == targets["P6"] == targets["HISTORY"] == 0
    assert "P7" not in targets and "OLD" not in targets and "E4" not in targets
    assert targets["R8"] == targets["R9"] == 1
    assert targets["DEFAULT"] == 2


def test_demand_survives_restart_deduplicates_and_prunes_with_a_hard_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(public, "PUBLIC_STOCK_DEMAND_MAX_TICKERS", 3)
    public.register_public_stock_demand(["AAA", "BBB", "CCC", "../escape", "AAA"], root=tmp_path, now=NOW)
    path = tmp_path / "public-stock-data-v1" / "demand" / "AAA.json"
    identity = path.stat().st_mtime_ns
    public.register_public_stock_demand(["aaa"], root=tmp_path, now=NOW + 1)
    assert path.stat().st_mtime_ns == identity
    public.register_public_stock_demand(["DDD", "EEE"], root=tmp_path, now=NOW + 100)
    active = public._active_demands(path.parent, NOW + 101, prune=False)
    assert len(active) == 3 and {"DDD", "EEE"} <= set(active)
    public.register_public_stock_demand(["NEW"], root=tmp_path, now=NOW + 86401 + 100)
    assert {item.stem for item in path.parent.glob("*.json")} == {"NEW"}
    with pytest.raises(ValueError):
        public.public_stock_snapshot_path("../../outside", root=tmp_path)


def test_untrusted_symlink_cannot_redirect_demand_writes(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "public-stock-data-v1").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        public.register_public_stock_demand(["AAPL"], root=tmp_path, now=NOW)
    assert not list(outside.iterdir())


def test_all_default_stocks_get_real_daily_bundles_without_evicting_manual_pulls(tmp_path, monkeypatch):
    from app.services.accounts import AccountStore
    store = AccountStore(tmp_path / "accounts.db")
    monkeypatch.setattr("app.services.accounts.get_account_store", lambda: store)
    async def run():
        defaults = public._default_tickers()
        assert defaults == ["AAPL", "MSFT", "NVDA", "SPY"]
        manual = tmp_path / "stock-pull-snapshots-v1.json"
        _write_bundle(manual, "MANUAL", NOW)
        original = manual.read_bytes()
        seen = []
        active = peak = 0

        async def puller(symbol, *, snapshot_path, include_options):
            nonlocal active, peak
            assert include_options is False
            active += 1
            peak = max(peak, active)
            seen.append(symbol)
            await asyncio.sleep(0)
            await asyncio.to_thread(_write_bundle, snapshot_path, symbol, NOW)
            active -= 1
            return _completed()

        queue = _queue(tmp_path, puller)
        queue._default_reader = lambda: defaults
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        assert set(seen) == set(defaults) | {"NVDA"}
        assert len(seen) == len(set(seen)) and peak <= 2
        for symbol in defaults:
            assert public.read_public_stock_resource(symbol, "daily_chart", root=tmp_path, now=NOW)["payload"]["bars"]
        assert manual.read_bytes() == original
        assert read_stock_pull_resource("MANUAL", "daily_chart", path=manual, now=NOW)
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        assert len(seen) == len(set(seen))
        await queue.aclose()

    asyncio.run(run())


def test_browsed_history_is_prioritized_without_reading_old_scans(tmp_path):
    async def run():
        public.register_public_stock_demand(["HISTORY"], root=tmp_path, now=NOW)
        seen = []

        async def puller(symbol, *, snapshot_path, include_options):
            seen.append(symbol)
            _write_bundle(snapshot_path, symbol, NOW)
            return _completed()

        queue = _queue(tmp_path, puller, concurrency=1)
        queue._current_reader = lambda: [f"R{i}" for i in range(10)]
        queue._default_reader = lambda: ["DEFAULT"]
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        assert seen[0] == "NVDA" and "HISTORY" in seen
        assert public.read_public_stock_status("HISTORY", root=tmp_path, now=NOW)["status"] == "ready"
        await queue.aclose()

    asyncio.run(run())


def test_partial_bundle_is_not_ready_and_failure_cooldown_survives_restart(tmp_path):
    async def run():
        now = [NOW]
        path = public.public_stock_snapshot_path("NVDA", root=tmp_path)
        _write_bundle(path, "NVDA", NOW, resources={"daily_chart"})
        before = path.read_bytes()
        calls = []

        async def unavailable(symbol, **kwargs):
            calls.append(symbol)
            raise RuntimeError("provider unavailable")

        queue = _queue(tmp_path, unavailable, now=lambda: now[0])
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        state = public.read_public_stock_status("NVDA", root=tmp_path, now=NOW)
        assert state["status"] == "partial" and state["retry_after_seconds"] == 60
        assert state["resources"]["daily_chart"]["available"]
        assert not state["resources"]["overview"]["available"]
        assert path.read_bytes() == before
        await queue.aclose()
        restarted = _queue(tmp_path, unavailable, now=lambda: now[0])
        await restarted.poll({})
        await asyncio.gather(*restarted._consumers)
        assert calls == ["NVDA"]
        now[0] += 61
        await restarted.poll({})
        await asyncio.gather(*restarted._consumers)
        assert calls == ["NVDA", "NVDA"]
        await restarted.aclose()

    asyncio.run(run())


def test_claimed_success_without_a_saved_kline_stays_pending_or_failed(tmp_path):
    async def run():
        async def empty_puller(symbol, **kwargs):
            return {"status": "completed"}

        queue = _queue(tmp_path, empty_puller)
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        assert public.read_public_stock_status("NVDA", root=tmp_path, now=NOW)["status"] == "failed"
        assert public.read_public_stock_resource("NVDA", "daily_chart", root=tmp_path, now=NOW) is None
        await queue.aclose()

    asyncio.run(run())


def test_close_stops_new_claims_but_even_cancelled_close_waits_for_disk_commit(tmp_path):
    async def run():
        release = asyncio.Event()
        started = asyncio.Event()
        calls = []

        async def slow_puller(symbol, *, snapshot_path, include_options):
            calls.append(symbol)
            if len(calls) == 2:
                started.set()
            await release.wait()
            await asyncio.to_thread(_write_bundle, snapshot_path, symbol, NOW)
            return _completed()

        queue = _queue(tmp_path, slow_puller)
        queue._default_reader = lambda: ["AAA", "BBB", "CCC"]
        await queue.poll({})
        await asyncio.wait_for(started.wait(), timeout=2)
        closing = asyncio.create_task(queue.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert len(calls) == 2
        for symbol in calls:
            assert public.read_public_stock_status(symbol, root=tmp_path, now=NOW)["status"] == "ready"
        assert not queue._active

    asyncio.run(run())


def test_large_continuously_due_history_cannot_starve_first_default_coverage(tmp_path):
    async def run():
        now = [NOW]
        seen = []
        public.register_public_stock_demand([f"H{i}" for i in range(120)], root=tmp_path, now=NOW)

        async def puller(symbol, *, snapshot_path, include_options):
            seen.append(symbol)
            # Older high-priority bundles expire while the queue is still busy.
            now[0] += 100
            _write_bundle(snapshot_path, symbol, now[0])
            return _completed()

        queue = _queue(tmp_path, puller, now=lambda: now[0], concurrency=1)
        queue._default_reader = lambda: ["DEFAULTA", "DEFAULTB", "DEFAULTC"]
        await queue.poll({})
        # The mock clock makes the hot tier continuously due. Stop after the
        # reserved slots have covered every default symbol.
        while len(seen) < 12:
            await asyncio.sleep(0)
        await queue.aclose()
        assert {"DEFAULTA", "DEFAULTB", "DEFAULTC"} <= set(seen[:12])

    asyncio.run(run())


def test_old_signal_snapshot_does_not_hide_this_rounds_failed_refresh(tmp_path):
    async def run():
        now = [NOW]
        path = public.public_stock_snapshot_path("NVDA", root=tmp_path)
        _write_bundle(path, "NVDA", NOW - 3600)
        calls = []

        async def partial_puller(symbol, *, snapshot_path, include_options):
            calls.append(symbol)
            _write_bundle(snapshot_path, symbol, now[0], resources={"overview", "daily_chart"})
            value = _completed()
            value["status"] = "partial"
            value["resources"]["signals"] = {"status": "failed", "persisted": False}
            return value

        queue = _queue(tmp_path, partial_puller, now=lambda: now[0])
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        assert public.read_public_stock_status("NVDA", root=tmp_path, now=NOW)["status"] == "partial"
        now[0] += 30
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        assert calls == ["NVDA"]
        assert public.read_public_stock_resource("NVDA", "signals", root=tmp_path, now=now[0])["saved_at"] == NOW - 3600
        await queue.aclose()
        restarted = _queue(tmp_path, partial_puller, now=lambda: now[0])
        await restarted.poll({})
        await asyncio.gather(*restarted._consumers)
        assert calls == ["NVDA"]
        now[0] = NOW + 61
        await restarted.poll({})
        await asyncio.gather(*restarted._consumers)
        assert calls == ["NVDA", "NVDA"]
        state = public.read_public_stock_status("NVDA", root=tmp_path, now=now[0])
        assert state["retry_after_seconds"] == 120
        assert state["as_of"] == datetime.fromtimestamp(NOW - 3600, timezone.utc).isoformat()
        assert state["last_attempt_at"] == datetime.fromtimestamp(now[0], timezone.utc).isoformat()
        await restarted.aclose()

    asyncio.run(run())


def test_inactive_stock_files_have_age_count_and_byte_bounds_while_targets_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(public, "PUBLIC_STOCK_INACTIVE_MAX_TICKERS", 2)
    monkeypatch.setattr(public, "PUBLIC_STOCK_INACTIVE_MAX_BYTES", 5000)
    for index, symbol in enumerate(["ACTIVE", "EXPIRED", "OLD", "NEW", "NEWER"]):
        path = public.public_stock_snapshot_path(symbol, root=tmp_path)
        _write_bundle(path, symbol, NOW)
        stamp = NOW - 8 * 86400 if symbol in {"ACTIVE", "EXPIRED"} else NOW - 100 + index
        os.utime(path, (stamp, stamp))
        metadata = path.parent / "status" / f"{symbol}.json"
        public._write_metadata(metadata, {"ticker": symbol, "as_of": stamp})
        os.utime(metadata, (stamp, stamp))
    public._prune_inactive_storage(tmp_path, {"ACTIVE"}, NOW)
    base = tmp_path / "public-stock-data-v1"
    assert {path.stem for path in base.glob("*.json")} == {"ACTIVE", "NEW", "NEWER"}
    assert {path.stem for path in (base / "status").glob("*.json")} == {"ACTIVE", "NEW", "NEWER"}
    monkeypatch.setattr(public, "PUBLIC_STOCK_INACTIVE_MAX_BYTES", 1)
    public._prune_inactive_storage(tmp_path, {"ACTIVE"}, NOW)
    assert {path.stem for path in base.glob("*.json")} == {"ACTIVE"}


def test_freshness_matches_scheduled_cadence_and_stale_running_state_recovers(tmp_path):
    path = public.public_stock_snapshot_path("NVDA", root=tmp_path)
    _write_bundle(path, "NVDA", NOW)
    public._write_metadata(path.parent / "status" / "NVDA.json", {
        "ticker": "NVDA", "status": "running", "as_of": NOW, "priority": 0,
    })
    entry = public.read_public_stock_resource("NVDA", "overview", root=tmp_path, now=NOW + 301)
    assert entry["fresh_seconds"] == 300 and entry["fresh"] is False
    assert public.read_public_stock_status("NVDA", root=tmp_path, now=NOW + 1801)["status"] == "ready"
    # During the same night's closed session, bundles have the worker's six-hour cadence.
    closed_now = datetime(2026, 9, 5, 1, tzinfo=timezone.utc).timestamp()
    _write_bundle(path, "NVDA", closed_now - 1000)
    entry = public.read_public_stock_resource("NVDA", "overview", root=tmp_path, now=closed_now)
    assert entry["fresh_seconds"] == 21600 and entry["fresh"] is True


def test_public_home_polls_separate_queue_and_drains_it_without_new_task():
    async def run():
        class Queue:
            polled = []
            closed = False

            async def poll(self, entries):
                self.polled.append(entries)

            def summary(self):
                return {"target_count": 214}

            async def aclose(self):
                self.closed = True

        queue = Queue()
        config = SimpleNamespace(
            **{field: 300 for field in PublicHomeTask._INTERVAL_FIELDS.values()},
            poll_seconds=30, failure_retry_seconds=60,
        )
        task = PublicHomeTask(config, stock_data_refresh=queue, clock=lambda: NOW)

        async def entries(*args, **kwargs):
            return {}

        async def lead():
            return None

        task._read_entries = entries
        task._breakout_lead_ticker = lead
        task._is_due = lambda *args, **kwargs: False
        task._is_hard_servable = lambda *args, **kwargs: True
        result = await task()
        assert len(queue.polled) == 1 and result.details["stock_data"]["target_count"] == 214
        await task.aclose()
        assert queue.closed

    asyncio.run(run())
