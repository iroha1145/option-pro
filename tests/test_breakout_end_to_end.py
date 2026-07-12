from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.api import breakouts as breakout_api
from app.main import app
from app.services.breakouts.clock import MarketClock
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.service import BreakoutRadarService
from app.services.breakouts.worker import BreakoutWorker
from app.services.breakouts.adapters import price_data
from app.services.strength import scanner


NY = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 7, 10, 10, 30, tzinfo=NY)
FIXTURE = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "breakouts"
        / "provider_regular.json"
    ).read_text()
)


def _daily(offset: float = 0) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-09", periods=420)
    close = 120 + offset + np.arange(len(index)) * 0.15 + np.sin(np.arange(len(index)) / 6)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 2_000_000,
        },
        index=index,
    )


def _intraday() -> pd.DataFrame:
    index = pd.date_range(AS_OF.replace(hour=9, minute=30), AS_OF, freq="5min")
    close = np.concatenate([np.linspace(220, 222, 6), np.linspace(226, 230, len(index) - 6)])
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 200_000,
        },
        index=index,
    )


def test_offline_provider_to_worker_to_sqlite_to_api_chain(tmp_path, monkeypatch) -> None:
    daily_panel = pd.concat({"AAPL": _daily(), "SPY": _daily(-5)}, axis=1)
    daily_panel.attrs["price_source"] = {"provider": "fixture", "status": "active"}
    intraday_panel = pd.concat({"AAPL": _intraday()}, axis=1)
    monkeypatch.setattr(scanner, "_download_history", lambda _symbols, period="2y": daily_panel)
    monkeypatch.setattr(price_data.yf, "download", lambda **_kwargs: intraday_panel)

    async def handler(_request):
        return httpx.Response(200, json=FIXTURE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        BREAKOUT_DB_PATH=tmp_path / "breakouts.db",
    )
    provider = TradingViewDiscoveryProvider(settings, client=client)
    worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=provider,
        scan_service=BreakoutRadarService(settings),
        clock=MarketClock(now=lambda: AS_OF.astimezone(timezone.utc)),
        owner_id="offline-e2e",
    )
    result = asyncio.run(worker.run_once())
    asyncio.run(client.aclose())
    assert result["status"] == "completed"
    assert result["event_count"] == 1

    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(breakout_api, "_now", lambda: AS_OF.astimezone(timezone.utc))
    payload = TestClient(app, base_url="http://localhost").get(
        "/api/breakouts/current"
    ).json()
    assert payload["status"] == "active"
    assert payload["events"][0]["ticker"] == "AAPL"
    assert payload["events"][0]["provenance"]["source_snapshot_id"]
    assert "raw_provider_fields" not in json.dumps(payload)
