"""Real worker action → real scanner → publish → GET, with provider stubs only."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import strength, worker_actions
from app.services.breakouts.config import BreakoutSettings
from app.services.market_calendar import last_completed_trading_day
from app.services.strength import scanner
from app.services.strength.market_regime import MARKET_BENCHMARKS
from app.worker.state import WorkerStateRepository
from app.worker.tasks import StrengthRefreshTask


def _history(*, end: date, slope: float, offset: float = 0.0, size: int = 320) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=size, tz="America/New_York")
    step = np.arange(size, dtype=float)
    close = 40.0 + offset + step * slope + np.sin(step / 9.0)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": 2_500_000.0 + step * 1_000.0,
        },
        index=index,
    )


def _install_provider_boundary(monkeypatch) -> list[tuple[list[str], str]]:
    requested: list[tuple[list[str], str]] = []
    # The real freshness check needs current completed-session data, even after
    # the calendar advances beyond the date this fixture was first written.
    last_session = last_completed_trading_day(datetime.now(timezone.utc))
    metadata = {
        "NVDA": {
            "sector_id": "semiconductors",
            "sector_name": "半导体",
            "primary_sector_id": "semiconductors",
            "primary_sector_name": "半导体",
            "theme_ids": ["semiconductors"],
            "theme_names": ["半导体"],
        },
        "AAPL": {
            "sector_id": "hardware",
            "sector_name": "硬件",
            "primary_sector_id": "hardware",
            "primary_sector_name": "硬件",
            "theme_ids": ["hardware"],
            "theme_names": ["硬件"],
        },
        "MSFT": {
            "sector_id": "software",
            "sector_name": "软件",
            "primary_sector_id": "software",
            "primary_sector_name": "软件",
            "theme_ids": ["software"],
            "theme_names": ["软件"],
        },
    }
    frames = {
        "NVDA": _history(end=last_session, slope=0.22),
        "AAPL": _history(end=last_session, slope=0.10, offset=8.0),
        "MSFT": _history(end=last_session, slope=0.12, offset=4.0),
    }
    for symbol in MARKET_BENCHMARKS:
        frames.setdefault(symbol, _history(end=last_session, slope=0.06, offset=20.0))
    panel = pd.concat(frames, axis=1)
    panel.attrs["price_source"] = {
        "provider": "synthetic-fixture",
        "status": "active",
        "message": "controlled daily bars",
    }

    monkeypatch.setattr(
        scanner,
        "_theme_universe",
        lambda sector_id=None: (["NVDA", "AAPL", "MSFT"], deepcopy(metadata)),
    )

    def download(symbols, period="2y"):
        requested.append((list(symbols), period))
        return panel

    monkeypatch.setattr(scanner, "_download_history", download)
    monkeypatch.setattr(
        "app.services.breakouts.config.get_breakout_settings",
        lambda: BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="shadow"),
    )
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_yahoo_options",
        lambda rows, display_top: {"provider": "Yahoo/yfinance", "status": "skipped", "enriched": 0},
    )
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_finnhub",
        lambda rows: {"provider": "Finnhub", "status": "skipped", "enriched": 0},
    )
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_marketdata_options",
        lambda rows: {"provider": "MarketData.app", "status": "skipped", "enriched": 0},
    )
    return requested


def _live_repository(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire("test-worker", lease_seconds=300, now=observed)
    assert token is not None
    repository.record_task(
        "test-worker",
        token,
        "strength_refresh",
        enabled=True,
        status="idle",
        now=observed,
    )


def test_e01_real_action_real_scanner_publish_and_read(tmp_path: Path, monkeypatch) -> None:
    downloads = _install_provider_boundary(monkeypatch)
    _live_repository(tmp_path, monkeypatch)
    snapshot = tmp_path / "strength-snapshot-v1.json"
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", snapshot)

    parameters = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "sector_id": "semiconductors",
        "top": 20,
        "min_price": 0.0,
        "min_avg_dollar_volume": 0.0,
        "include_options": False,
    }

    app = FastAPI()
    app.include_router(worker_actions.router)
    app.include_router(strength.router)
    with TestClient(app) as client:
        posted = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": parameters, "idempotency_key": "e01-semiconductors"},
        )
        assert posted.status_code == 202, posted.text
        action = posted.json()
        assert action["reason"] == "queued"
        assert action["reused"] in {False, None}
        assert action["details"]["parameters"]["sector_id"] == "semiconductors"
        assert action["details"]["parameters_hash"] == strength.strength_scan_parameters_hash(
            parameters
        )

        result = asyncio.run(
            StrengthRefreshTask(snapshot_path=snapshot).run_for_actions([action])
        )
        assert result.status == "idle"
        assert result.details["result"] == "refreshed"
        assert result.details["published"] is True
        assert result.details["parameters"]["sector_id"] == "semiconductors"
        assert result.details["parameters_hash"] == action["details"]["parameters_hash"]
        assert result.details["score_data_through"]
        assert result.details["count"] >= 1
        assert downloads, "scanner must consume the provider stub"

        variant = strength._strength_snapshot_path(parameters, base_path=snapshot)
        assert variant.is_file()

        status = client.get(f"/api/worker/actions/{action['request_id']}")
        assert status.status_code == 200
        assert status.json()["request_id"] == action["request_id"]

        payload, _saved_at, stale = asyncio.run(strength._scan_snapshot_payload(**parameters))
        assert payload["rows"][0]["ticker"] == "NVDA"
        assert payload.get("score_data_through")
        assert payload["snapshot_source"] == "worker"
        assert stale is False
        assert payload["source_status"] == "active"

        same_scores = asyncio.run(
            StrengthRefreshTask(snapshot_path=snapshot).run_for_actions([action])
        )
        assert same_scores.status == "idle"
        replay, _, _ = asyncio.run(strength._scan_snapshot_payload(**parameters))
        assert [row["ticker"] for row in replay["rows"]] == [
            row["ticker"] for row in payload["rows"]
        ]
        assert replay["score_data_through"] == payload["score_data_through"]


def test_b03_sector_filter_does_not_rescore_the_same_ticker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_provider_boundary(monkeypatch)
    default = asyncio.run(
        scanner.scan_strength(
            universe="themes",
            timeframe="all",
            profile="balanced",
            top=20,
            sector_id=None,
            min_price=0.0,
            min_avg_dollar_volume=0.0,
            include_options=False,
            force_refresh=True,
        )
    )
    sector = asyncio.run(
        scanner.scan_strength(
            universe="themes",
            timeframe="all",
            profile="balanced",
            top=20,
            sector_id="semiconductors",
            min_price=0.0,
            min_avg_dollar_volume=0.0,
            include_options=False,
            force_refresh=True,
        )
    )
    default_nvda = next(row for row in default["rows"] if row["ticker"] == "NVDA")
    sector_nvda = next(row for row in sector["rows"] if row["ticker"] == "NVDA")
    assert default_nvda["final_score"] == sector_nvda["final_score"]
    assert default["universe_count"] == sector["universe_count"]
    assert default["universe_count"] > sector["count"]
    assert {row["ticker"] for row in sector["rows"]} == {"NVDA"}
