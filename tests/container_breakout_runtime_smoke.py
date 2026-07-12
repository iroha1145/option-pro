"""Deterministic Breakout Radar checks executed inside the production image."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.worker import BreakoutWorker


CLOSED_AT = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
REGULAR_AT = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


class RejectingProvider:
    calls = 0

    async def scan(self, **_kwargs):
        self.calls += 1
        raise AssertionError("a closed-session worker must not call its Provider")


class EmptyScanService:
    async def build_snapshot(self, **kwargs):
        discovery = kwargs["discovery"]
        assert discovery.status.value == "active"
        assert [item.ticker for item in discovery.candidates] == ["AAPL"]
        return {"events": []}


async def check_enabled_closed(root: Path) -> dict[str, object]:
    path = root / "closed.db"
    settings = BreakoutSettings(
        _env_file=None,
        enabled=True,
        db_path=path,
    )
    provider = RejectingProvider()
    repository = BreakoutRepository(path)
    result = await BreakoutWorker(
        settings,
        repository,
        provider=provider,
        clock=MarketClock(now=lambda: CLOSED_AT),
        owner_id="container-closed",
    ).run_once()

    assert result["status"] == "paused"
    assert result["reason"] == "market_closed"
    assert result["scan_run_id"] is None
    assert provider.calls == 0
    status = BreakoutRepository(path, read_only=True).status()
    assert status["worker"]["status"] == "paused"
    assert status["provider_health"] == []
    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM breakout_scan_runs"
        ).fetchone()[0] == 0
    return result


async def check_mocked_active(root: Path, fixture_path: Path) -> dict[str, object]:
    path = root / "active.db"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.method == "POST"
        assert request.url.host == "scanner.tradingview.com"
        return httpx.Response(200, json=fixture)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = BreakoutSettings(
        _env_file=None,
        enabled=True,
        db_path=path,
    )
    repository = BreakoutRepository(path)
    provider = TradingViewDiscoveryProvider(settings, client=client)
    try:
        result = await BreakoutWorker(
            settings,
            repository,
            provider=provider,
            scan_service=EmptyScanService(),
            clock=MarketClock(now=lambda: REGULAR_AT),
            owner_id="container-active",
        ).run_once()
    finally:
        await client.aclose()

    assert result["status"] == "completed"
    assert result["event_count"] == 0
    assert requests == 1
    latest = BreakoutRepository(path, read_only=True).latest_completed_scan()
    assert latest is not None
    assert latest["candidate_count"] == 1
    assert latest["provider_snapshot"]["status"] == "active"
    assert latest["provider_snapshot"]["candidate_count"] == 1
    status = BreakoutRepository(path, read_only=True).status()
    assert status["provider_health"][0]["provider"] == "tradingview"
    assert status["provider_health"][0]["status"] == "active"
    return result


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: container_breakout_runtime_smoke.py FIXTURE")
    fixture_path = Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="optix-container-smoke-") as directory:
        root = Path(directory)
        closed = await check_enabled_closed(root)
        active = await check_mocked_active(root, fixture_path)
    print(json.dumps({"closed": closed, "active": active}, allow_nan=False))


if __name__ == "__main__":
    asyncio.run(main())
