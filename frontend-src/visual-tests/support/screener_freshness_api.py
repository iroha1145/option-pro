#!/usr/bin/env python3
"""Isolated FastAPI for live screener freshness browser tests.

The production action router, durable queue, WorkerSupervisor and scanner run
against synthetic provider inputs. Only this loopback test server exposes reset
and diagnostic endpoints; it does not exercise production authentication.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

DATA_DIR = Path(os.environ.get("SCREENER_FRESHNESS_DATA", tempfile.mkdtemp(prefix="screener-freshness-")))
# Never inherit a developer's application data directory into this fixture.
os.environ["DATA_DIR"] = str(DATA_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)

from app.api import strength, worker_actions  # noqa: E402
from app.access import request_owner_access_context  # noqa: E402
from app.services.breakouts.config import BreakoutSettings  # noqa: E402
from app.services.strength import scanner  # noqa: E402
from app.services.strength.market_regime import MARKET_BENCHMARKS  # noqa: E402
from app.worker.tasks import StrengthRefreshTask  # noqa: E402
from app.worker.runtime import TaskSpec, WorkerSupervisor  # noqa: E402
from app.worker.state import WorkerStateRepository  # noqa: E402
from app.services.strength.freshness import expected_complete_session  # noqa: E402

SNAPSHOT = DATA_DIR / "strength-snapshot-v1.json"
NOW = time.time()
DATA_THROUGH = expected_complete_session(datetime.now(timezone.utc)).isoformat()
REPOSITORY: WorkerStateRepository | None = None
SUPERVISOR: WorkerSupervisor | None = None
WORKER_RUN: asyncio.Task | None = None
PROVIDER_DELAY = 0.25
PROVIDER_FAILURE = False
worker_actions._repository = lambda: REPOSITORY
POST_COUNT = 0
SCAN_COUNT = 0


def _history(*, slope: float, offset: float = 0.0, size: int = 320) -> pd.DataFrame:
    index = pd.bdate_range(end=DATA_THROUGH, periods=size, tz="America/New_York")
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


def _install_provider_boundary() -> None:
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
        "NVDA": _history(slope=0.22),
        "AAPL": _history(slope=0.10, offset=8.0),
        "MSFT": _history(slope=0.12, offset=4.0),
    }
    for symbol in MARKET_BENCHMARKS:
        frames.setdefault(symbol, _history(slope=0.06, offset=20.0))
    panel = pd.concat(frames, axis=1)
    panel.attrs["price_source"] = {
        "provider": "synthetic-fixture",
        "status": "active",
        "message": "controlled daily bars",
    }

    scanner._theme_universe = lambda sector_id=None: (["NVDA", "AAPL", "MSFT"], deepcopy(metadata))
    def download(symbols, period="2y"):
        global SCAN_COUNT
        SCAN_COUNT += 1
        time.sleep(PROVIDER_DELAY)
        if PROVIDER_FAILURE:
            raise RuntimeError("synthetic_provider_failure")
        return panel

    scanner._download_history = download
    scanner.enrich_rows_with_yahoo_options = lambda rows, display_top: {
        "provider": "Yahoo/yfinance",
        "status": "skipped",
        "enriched": 0,
    }
    scanner.enrich_rows_with_finnhub = lambda rows: {"provider": "Finnhub", "status": "skipped", "enriched": 0}
    scanner.enrich_rows_with_marketdata_options = lambda rows: {
        "provider": "MarketData.app",
        "status": "skipped",
        "enriched": 0,
    }

    import app.services.breakouts.config as breakout_config

    breakout_config.get_breakout_settings = lambda: BreakoutSettings(
        _env_file=None,
        RANGE_PERSISTENCE_MODE="shadow",
    )


def _row(ticker: str, score: float, price: float, through: str, sector_id: str, sector_name: str) -> dict:
    return {
        "ticker": ticker,
        "score": score,
        "final_score": score,
        "price": price,
        "avg_dollar_volume_20d": 50_000_000.0,
        "daily_data_through": through,
        "price_as_of": through,
        "sector_id": sector_id,
        "sector_name": sector_name,
    }


def _seed_snapshots() -> None:
    strength._STRENGTH_SNAPSHOT_PATH = SNAPSHOT
    through = DATA_THROUGH
    default_row = _row("AAPL", 80.0, 190.0, through, "hardware", "硬件")
    strength._write_strength_snapshot(
        SNAPSHOT,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload={
            "as_of": datetime.now(timezone.utc).isoformat(),
            "score_version": scanner.STRENGTH_SCORE_VERSION,
            "score_data_through": through,
            "params": {
                key: value
                for key, value in strength.DEFAULT_STRENGTH_SCAN_PARAMETERS.items()
                if key != "include_options"
            },
            "count": 1,
            "rows": [default_row],
            "results": [default_row],
            "universe_count": 3,
            "screened_count": 3,
            "data_sources": {"prices": {"status": "active", "provider": "synthetic-fixture"}},
        },
        saved_at=NOW - 60,
    )
    variant_params = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "semiconductors"}
    old = (datetime.fromisoformat(DATA_THROUGH) - timedelta(days=60)).date().isoformat()
    old_row = _row("NVDA", 91.0, 12.5, old, "semiconductors", "半导体")
    strength._write_strength_snapshot(
        strength._strength_snapshot_path(variant_params, base_path=SNAPSHOT),
        base_path=SNAPSHOT,
        parameters=variant_params,
        payload={
            "as_of": f"{old}T20:00:00+00:00",
            "score_version": scanner.STRENGTH_SCORE_VERSION,
            "score_data_through": old,
            "params": {key: value for key, value in variant_params.items() if key != "include_options"},
            "count": 1,
            "rows": [old_row],
            "results": [old_row],
            "universe_count": 3,
            "screened_count": 3,
            "data_sources": {"prices": {"status": "active", "provider": "legacy-snapshot"}},
        },
        saved_at=NOW - 30,
    )


async def _stop_worker() -> None:
    if SUPERVISOR is not None and WORKER_RUN is not None:
        SUPERVISOR.request_stop()
        await WORKER_RUN


async def _reset(*, provider_delay: float = 0.25, provider_failure: bool = False,
                 software_fresh: bool = False) -> None:
    global REPOSITORY, SUPERVISOR, WORKER_RUN, SNAPSHOT, NOW
    global POST_COUNT, SCAN_COUNT, PROVIDER_DELAY, PROVIDER_FAILURE
    await _stop_worker()
    scenario = Path(tempfile.mkdtemp(prefix="scenario-", dir=DATA_DIR))
    os.environ["DATA_DIR"] = str(scenario)
    SNAPSHOT = scenario / "strength-snapshot-v1.json"
    NOW = time.time()
    POST_COUNT = SCAN_COUNT = 0
    PROVIDER_DELAY = provider_delay
    PROVIDER_FAILURE = provider_failure
    _install_provider_boundary()
    _seed_snapshots()
    if software_fresh:
        parameters = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "software"}
        row = _row("MSFT", 85.0, 95.0, DATA_THROUGH, "software", "软件")
        strength._write_strength_snapshot(
            strength._strength_snapshot_path(parameters, base_path=SNAPSHOT),
            base_path=SNAPSHOT, parameters=parameters, saved_at=NOW - 60,
            payload={"as_of": datetime.now(timezone.utc).isoformat(),
                     "score_version": scanner.STRENGTH_SCORE_VERSION,
                     "score_data_through": DATA_THROUGH, "count": 1,
                     "rows": [row], "results": [row], "universe_count": 3,
                     "screened_count": 3,
                     "params": {k: v for k, v in parameters.items() if k != "include_options"},
                     "data_sources": {"prices": {"status": "active", "provider": "synthetic-fixture"}}},
        )
    REPOSITORY = WorkerStateRepository(scenario / "worker.db")
    SUPERVISOR = WorkerSupervisor(
        REPOSITORY,
        [TaskSpec("strength_refresh", StrengthRefreshTask(snapshot_path=SNAPSHOT),
                  interval_seconds=86_400, timeout_seconds=30, manual_only=True)],
        owner_id="screener-browser-fixture", shutdown_grace_seconds=5,
    )
    WORKER_RUN = asyncio.create_task(SUPERVISOR.run_forever())
    for _ in range(200):
        if WORKER_RUN.done():
            await WORKER_RUN
            raise RuntimeError("fixture_worker_stopped")
        try:
            health = await asyncio.to_thread(REPOSITORY.health)
            if health.get("healthy") and health.get("tasks"):
                return
        except (OSError, RuntimeError):
            pass
        await asyncio.sleep(0.01)
    raise RuntimeError("fixture_worker_start_timeout")


@asynccontextmanager
async def lifespan(app):
    await _reset()
    try:
        yield
    finally:
        await _stop_worker()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def fixture_owner(request: Request, call_next):
    global POST_COUNT
    if request.method == "POST" and request.url.path == "/api/worker/actions/strength_refresh":
        POST_COUNT += 1
    with request_owner_access_context(True):
        return await call_next(request)


@app.get("/health")
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.post("/debug/reset")
async def reset(request: Request) -> dict:
    options = await request.json()
    await _reset(
        provider_delay=float(options.get("provider_delay", 0.25)),
        provider_failure=bool(options.get("provider_failure", False)),
        software_fresh=bool(options.get("software_fresh", False)),
    )
    return {"status": "ready", "score_data_through": DATA_THROUGH}


@app.get("/debug/screener-stats")
def screener_stats() -> dict:
    actions = REPOSITORY.action_requests(limit=100)
    return {
        "post_count": POST_COUNT,
        "scan_count": SCAN_COUNT,
        "score_data_through": DATA_THROUGH,
        "snapshot_hashes": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in SNAPSHOT.parent.glob("strength-snapshot-v1*.json")
        },
        "actions": [
            {"request_id": item["request_id"], "status": item["status"],
             "parameters_hash": item["details"].get("parameters_hash"),
             "sector_id": item["details"].get("parameters", {}).get("sector_id"),
             "result": item["details"].get("result")}
            for item in actions
        ],
    }


@app.get("/api/access/status")
def access_status() -> dict:
    return {
        "access_mode": "private_network",
        "logged_in": True,
        "account": {"logged_in": False, "username": None},
    }


@app.get("/api/strength/profiles")
def profiles() -> dict:
    return {
        "profiles": ["conservative", "balanced", "aggressive"],
        "sectors": [
            {"id": "semiconductors", "name": "半导体"},
            {"id": "software", "name": "软件"},
            {"id": "hardware", "name": "硬件"},
        ],
    }


@app.get("/api/strength/market")
def market() -> dict:
    return {"as_of": "2026-09-04T20:30:00+00:00", "market_regime": {}}


app.include_router(strength.router)


@app.get("/api/ai/status")
def ai_status() -> dict:
    return {"enabled": False, "status": "disabled"}


@app.get("/api/runtime-settings")
def runtime_settings() -> dict:
    return {"version": 1, "settings": {"ai": {"manual_analysis_enabled": False}}}


app.include_router(worker_actions.router)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def other_api(rest: str):
    if rest.startswith("strength/") or rest.startswith("worker/"):
        return JSONResponse({"detail": "unhandled"}, status_code=404)
    return JSONResponse({}, status_code=200)


if __name__ == "__main__":
    host = os.environ.get("SCREENER_FRESHNESS_HOST", "127.0.0.1")
    port = int(os.environ.get("SCREENER_FRESHNESS_PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="warning")
