"""财报手动刷新 Worker 化（审计 P2-04）的行为契约。

三条不变量：
1. password 模式 POST 只入队（earnings_calendar → public_home 任务），
   HTTP 请求内零上游工作；冷却语义原样透传。
2. password 模式 owner GET 直读磁盘快照——进程 TTLCache 无法跨进程失效，
   consult 它会让 Worker 发布后的跟进轮询一直读到旧载荷。
3. public_home 任务的动作路径立即重建并发布 earnings 资源，且只认
   earnings_calendar 动作。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.api import earnings
from app.services.cache import cache
from app.worker.tasks import PublicHomeTask
from tests.http_response_support import (
    anonymous_get_request,
    response_payload,
)
from tests.test_public_home_snapshot import _payload as public_home_payload


def _password_config() -> SimpleNamespace:
    return SimpleNamespace(
        access=SimpleNamespace(mode="password"),
        public_home=SimpleNamespace(earnings_seconds=1800.0),
    )


def test_password_refresh_queues_worker_action(monkeypatch) -> None:
    from app.api import worker_actions

    monkeypatch.setattr(earnings, "get_personal_config", _password_config)

    def _must_not_build(*_args, **_kwargs):
        raise AssertionError("provider work must stay out of the request")

    monkeypatch.setattr(earnings, "_build_upcoming_earnings", _must_not_build)
    submitted: list[tuple[str, str, float]] = []

    class FakeRepo:
        def request_action(
            self,
            action_type,
            task_name,
            key,
            *,
            cooldown_seconds,
            details,
        ):
            del key, details
            submitted.append((action_type, task_name, cooldown_seconds))
            return {"request_id": "req-1", "reason": None}

    monkeypatch.setattr(worker_actions, "_repository", lambda: FakeRepo())
    monkeypatch.setattr(
        worker_actions,
        "_read_health",
        lambda _repo: {
            "healthy": True,
            "tasks": [{"task_name": "public_home", "enabled": True}],
        },
    )

    result = asyncio.run(earnings.refresh_upcoming_earnings())
    assert result["refresh_status"] == "queued"
    assert result["refresh_action_id"] == "req-1"
    assert result["refresh_retry_after_seconds"] == 60
    assert submitted == [("earnings_calendar", "public_home", 60.0)]

    class CooldownRepo(FakeRepo):
        def request_action(self, *args, **kwargs):
            del args, kwargs
            return {"request_id": "req-1", "reason": "cooldown"}

    monkeypatch.setattr(worker_actions, "_repository", lambda: CooldownRepo())
    result = asyncio.run(earnings.refresh_upcoming_earnings())
    assert result["refresh_status"] == "cooldown"


def test_password_owner_get_reads_disk_not_process_cache(monkeypatch) -> None:
    monkeypatch.setattr(earnings, "current_request_is_owner", lambda: True)
    monkeypatch.setattr(earnings, "get_personal_config", _password_config)
    today = earnings._market_today()
    key = f"earnings:upcoming:{today.isoformat()}"
    cache.set(key, {"as_of": "stale-process-cache", "items": []}, 600)

    async def fake_disk(resource, *, parameters, fresh_for_seconds, now):
        del resource, parameters, fresh_for_seconds
        return {
            "payload": {"as_of": "fresh-disk", "earnings": []},
            "saved_at": now - 5,
            "fresh": True,
        }

    monkeypatch.setattr(
        earnings, "read_owner_public_home_entry_async", fake_disk
    )
    try:
        payload = response_payload(
            asyncio.run(earnings.upcoming_earnings(anonymous_get_request()))
        )
        assert payload["as_of"] == "fresh-disk"
    finally:
        cache._drop(key)


def test_public_home_task_serves_earnings_calendar_actions(tmp_path) -> None:
    now = 1_789_000_000.0
    calls = {"build": 0}

    async def builder(parameters):
        del parameters
        calls["build"] += 1
        return public_home_payload("earnings", now)

    published: list[set[str]] = []

    def writer(path, entries, *, now):
        del path, now
        published.append(set(entries))

    def reader(path, *, now):
        del path, now
        return {}

    task = PublicHomeTask(
        SimpleNamespace(
            watchlist_seconds=60,
            indices_seconds=60,
            overview_seconds=60,
            chart_seconds=60,
            signals_seconds=60,
            earnings_seconds=1800,
            unusual_seconds=1800,
        ),
        builders={"earnings": builder},
        reader=reader,
        writer=writer,
        snapshot_path=tmp_path / "bundle.json",
        watchlist_reader=lambda *args, **kwargs: None,
        watchlist_path=tmp_path / "watchlist-snapshot-v1.json",
        clock=lambda: now,
    )

    result = asyncio.run(
        task.run_for_actions(
            [{"action_type": "earnings_calendar", "request_id": "r1"}]
        )
    )
    assert result.status == "idle"
    assert result.details["result"] == "earnings_refreshed"
    assert calls["build"] == 1
    assert published and "earnings" in published[0]

    with pytest.raises(ValueError):
        asyncio.run(
            task.run_for_actions([{"action_type": "focus_refresh"}])
        )
