from __future__ import annotations

import asyncio
import json

import pytest

from app.services.catalysts import read_updates as m


def test_no_db_created_for_missing_path(tmp_path) -> None:
    path = tmp_path / "private-name.sqlite"
    rev = m.cache_revision(path)
    assert len(rev) == 24 and all(c in "0123456789abcdef" for c in rev)
    assert list(tmp_path.iterdir()) == []
    assert m.cache_revision(path) == rev


def test_wal_commits_trigger_change_and_shm_does_not(tmp_path) -> None:
    db = tmp_path / "db"
    db.write_bytes(b"base")
    baseline = m.cache_revision(db)
    (tmp_path / "db-shm").write_bytes(b"reader")
    assert m.cache_revision(db) == baseline
    (tmp_path / "db-wal").write_bytes(b"commit")
    assert m.cache_revision(db) != baseline


def test_replaced_file_and_deleted_wal_change_revision(tmp_path) -> None:
    db = tmp_path / "db"
    db.write_bytes(b"base")
    (tmp_path / "db-wal").write_bytes(b"a")
    before = m.cache_revision(db)
    (tmp_path / "db-wal").unlink()
    assert m.cache_revision(db) != before
    before = m.cache_revision(db)
    another = tmp_path / "new"
    another.write_bytes(b"base")
    another.replace(db)
    assert m.cache_revision(db) != before


def test_frame_contains_only_opaque_revision() -> None:
    frame = m.update_frame("a" * 24)
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload == {"revision": "a" * 24}
    assert frame.endswith("\n\n")
    assert "event: catalyst-update" in frame


def test_event_baseline_heartbeat_and_changed_revision(tmp_path) -> None:
    class Request:
        async def is_disconnected(self) -> bool:
            return False

    async def run() -> None:
        db = tmp_path / "db"
        frames = m.event_frames(Request(), db, interval=0, lifetime=3)
        assert await anext(frames) == "retry: 10000\n\n"
        assert "event: catalyst-update" in await anext(frames)
        assert await anext(frames) == ": keep-alive\n\n"
        db.write_bytes(b"new")
        assert "event: catalyst-update" in await anext(frames)
        await frames.aclose()

    asyncio.run(run())


def test_disconnected_client_stops_without_scan(tmp_path, monkeypatch) -> None:
    class Request:
        async def is_disconnected(self) -> bool:
            return True

    def fail(_path) -> str:
        raise AssertionError("should not read metadata")

    monkeypatch.setattr(m, "cache_revision", fail)

    async def run() -> None:
        frames = [frame async for frame in m.event_frames(Request(), tmp_path / "db", interval=0)]
        assert frames == ["retry: 10000\n\n"]

    asyncio.run(run())


def test_response_constructed_but_unused_reserves_no_slot(tmp_path) -> None:
    start = m._active_streams

    class Request:
        pass

    response = m.catalyst_update_response(Request(), tmp_path / "db")
    assert m._active_streams == start
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"


def test_stream_cap_returns_429_without_creating_stream(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(m, "_active_streams", 32)

        async def frames():
            raise AssertionError("stream must not run")
            yield ""

        response = m._LimitedUpdates(frames())
        sent: list = []

        async def send(message) -> None:
            sent.append(message)

        async def recv():
            return {"type": "http.disconnect"}

        await response(
            {"type": "http", "method": "GET", "asgi": {"spec_version": "2.4"}},
            recv,
            send,
        )
        assert sent[0]["status"] == 429
        assert m._active_streams == 32

    asyncio.run(run())


def test_completion_and_send_failure_release_slots(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(m, "_active_streams", 0)

        async def frames():
            yield "hello"

        async def recv():
            return {"type": "http.disconnect"}

        async def send(_message) -> None:
            return None

        scope = {"type": "http", "method": "GET", "asgi": {"spec_version": "2.4"}}
        await m._LimitedUpdates(frames())(scope, recv, send)
        assert m._active_streams == 0

        async def failing(_message) -> None:
            raise RuntimeError("proxy disconnected")

        with pytest.raises(RuntimeError):
            await m._LimitedUpdates(frames())(scope, recv, failing)
        assert m._active_streams == 0

    asyncio.run(run())


def test_cancellation_releases_slot(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(m, "_active_streams", 0)
        ready = asyncio.Event()

        async def frames():
            ready.set()
            await asyncio.sleep(999)
            yield ""

        async def recv():
            return {"type": "http.disconnect"}

        async def send(_message) -> None:
            return None

        task = asyncio.create_task(
            m._LimitedUpdates(frames())(
                {"type": "http", "method": "GET", "asgi": {"spec_version": "2.4"}},
                recv,
                send,
            )
        )
        await ready.wait()
        assert m._active_streams == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert m._active_streams == 0

    asyncio.run(run())
