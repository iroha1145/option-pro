"""Current EOD action/publish/GET chain plus historical scorer regressions."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
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


def _live_repository(tmp_path: Path, monkeypatch):
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

    return repository, token


def test_e01_real_action_real_eod_publish_and_read(tmp_path: Path, monkeypatch) -> None:
    from app.services.eod_limited import worker, store
    from app.services.research_eod_v1.calendar_asof import last_complete_eod_session
    from app.services.research_eod_v1.fixtures import make_series

    repository, token = _live_repository(tmp_path, monkeypatch)
    session = last_complete_eod_session(datetime.now(timezone.utc))
    panel = worker.build_synthetic_panel(end=session)
    # Keep the six theme candidates, with enough additional reference stocks
    # for the default v1.4 G1 gate. Funds cannot fill this stock-only reference.
    dates = panel["SPY"].dates
    step = np.arange(len(dates), dtype=float)
    for index in range(24):
        sid = f"REFERENCE{index:02}"
        # Varied, declining price paths provide a non-degenerate comparison
        # pool while the original candidates retain their existing price paths.
        close = 100.0 - (.04 + .001 * index) * step + 6.0 * np.sin(2 * np.pi * step / (13 + index % 7))
        panel[sid] = make_series(sid, dates, close, theme_ids=("all_market_stocks",))
    runs = []

    def real_eod_with_labeled_fixture(**kwargs):
        runs.append(kwargs)
        return worker.run_eod_limited_job(
            **kwargs, session=session, panel=panel, root=tmp_path,
            synthetic_input=True, themes=("semiconductors",), algorithms=("A_trend_quality",),
        )

    parameters = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "sector_id": "semiconductors", "min_price": 0.0,
        "min_avg_dollar_volume": 0.0, "include_options": False,
    }
    expected = strength.strength_execution_parameters(parameters)
    app = FastAPI()
    app.include_router(worker_actions.router)
    app.include_router(strength.router)
    with TestClient(app) as client:
        posted = client.post("/api/worker/actions/strength_refresh", json={
            "parameters": parameters, "idempotency_key": "e01-semiconductors",
        })
        assert posted.status_code == 202, posted.text
        action = posted.json()
        assert action["reason"] == "queued"
        assert action["details"]["parameters"] == expected
        assert action["details"]["parameters_hash"] == strength.strength_scan_parameters_hash(expected)
        claimed = repository.claim_actions("test-worker", token, "strength_refresh")
        result = asyncio.run(StrengthRefreshTask(eod_runner=real_eod_with_labeled_fixture).run_for_actions(claimed))
        assert result.status == "idle"
        assert result.details["published"] is True
        assert result.details["parameters"] == expected
        assert result.details["parameters_hash"] == action["details"]["parameters_hash"]
        assert result.details["score_data_through"] == session.isoformat()
        assert result.details["count"] == 9
        assert len(runs) == 1
        assert store.snapshot_path(tmp_path).is_file()
        scored = store.read_variant("balanced", "mid", root=tmp_path)
        assert scored["watch_list"]
        assert all(row["atr_reference_n"] == 30 for row in scored["watch_list"])
        completion = result.details["action_completions"][0]
        repository.finish_actions(
            "test-worker", token, [action["request_id"]], succeeded=completion["succeeded"],
            details={"result": completion["result"]},
        )
        status = client.get(f"/api/worker/actions/{action['request_id']}")
        assert status.status_code == 200
        assert status.json()["status"] == "completed"
        response = client.get("/api/strength/scan", params={
            "ranking_algorithm": "production", "timeframe": "all",
            "sector_id": "semiconductors", "min_price": 0,
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["rows"]
        assert {row["ticker"] for row in payload["rows"]} <= set(panel)
        assert payload["score_data_through"] == session.isoformat()
        assert payload["snapshot_source"] == "eod_limited_worker"
        assert payload["effective_algorithm"] == "eod_limited_v1"
        assert payload["synthetic"] is True
        assert payload["source_status"] == "historical"
        replay = client.get("/api/strength/scan", params={
            "ranking_algorithm": "a0_mid_long", "timeframe": "all",
            "sector_id": "semiconductors", "min_price": 0,
        }).json()
        assert replay["rows"] == payload["rows"]
        assert replay["score_data_through"] == payload["score_data_through"]


def test_legacy_sector_filter_does_not_rescore_the_same_ticker(
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
