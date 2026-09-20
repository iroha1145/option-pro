#!/usr/bin/env python3
"""Isolated FastAPI for live screener freshness browser tests.

The production action router, durable queue, WorkerSupervisor and EOD batch
publication run against controlled inputs. Only this loopback server exposes reset
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
from datetime import datetime, timezone
from pathlib import Path

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
from app.services.algorithm_modes import PRODUCTION_ALGORITHM  # noqa: E402
from app.services.eod_limited import PURPOSE_LIVE  # noqa: E402
from app.services.eod_limited import store as eod_store  # noqa: E402
from app.services.research_eod_v1.constants import HORIZONS, PROFILES  # noqa: E402
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


def _install_provider_boundary() -> None:
    # Production reads one all-variant EOD batch. Keep the same store contract
    # while isolating every browser scenario under its own temporary DATA_DIR.
    eod_store.snapshot_dir = lambda root=None: Path(root or SNAPSHOT.parent) / "eod-limited-v1"

    production_settings = type(
        "Settings",
        (),
        {
            "algorithms": type(
                "Algos",
                (),
                {
                    "screener_ranking_algorithm": PRODUCTION_ALGORITHM,
                    "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                },
            )()
        },
    )()
    strength.get_effective_runtime_settings = lambda: production_settings

def _row(ticker: str, score: float, price: float, through: str, sector_id: str) -> dict:
    return {
        "security_id": ticker,
        "score": score,
        "price": price,
        "session_date": through,
        "status": "watch",
        "qualification": "watch",
        "sector_context": sector_id,
        "algorithm_id": "A_trend_quality",
        "stock_or_etf_track": "stock",
        "rejection_reasons": ["DOLLAR_LIQUIDITY_UNVERIFIED"],
        "factors": {"T": 80, "M": 70, "S": 60, "B": 50, "P": 40, "V": 30, "R": 20, "G": 10},
    }


def _scored(rows: list[dict], *, profile: str, horizon: str, through: str) -> dict:
    return {
        "session_date": through,
        "served_session": through,
        "attempted_session": through,
        "purpose": PURPOSE_LIVE,
        "compute_version": "limited-current-v1.1",
        "feature_version": "browser-fixture-v1",
        "profile": profile,
        "horizon": horizon,
        "capability_flags": {
            "dollar_liquidity_verified": False,
            "volume_session_verified": False,
            "volume_verified": False,
        },
        "volume_scope": "VENDOR_DAILY_UNVERIFIED",
        "panel_n": 3,
        "complete_bar_n": 3,
        "family_results": [],
        "composite_results": [],
        "watch_list": deepcopy(rows),
        "eligible_n": 0,
        "watch_n": len(rows),
        "rejected_n": 0,
        "composite_n": 0,
        "historical_example": False,
        "synthetic": False,
    }


def _publish_eod_rows(rows: list[dict], *, published_at: float) -> dict:
    variants = {
        eod_store.variant_key(profile, horizon): _scored(
            rows, profile=profile, horizon=horizon, through=DATA_THROUGH,
        )
        for profile in PROFILES
        for horizon in HORIZONS
    }
    publication = eod_store.publish_batch({
        "purpose": PURPOSE_LIVE,
        "served_session": DATA_THROUGH,
        "attempted_session": DATA_THROUGH,
        "published_at": published_at,
        "variants": variants,
    }, root=SNAPSHOT.parent)
    return {"publication": publication, "variants": variants}


def _seed_snapshots(*, software_fresh: bool = False) -> None:
    rows = [_row("AAPL", 80.0, 190.0, DATA_THROUGH, "hardware")]
    if software_fresh:
        rows.append(_row("MSFT", 85.0, 95.0, DATA_THROUGH, "software"))
    _publish_eod_rows(rows, published_at=NOW - 60)


def _run_eod_fixture(**_kwargs) -> dict:
    global SCAN_COUNT
    SCAN_COUNT += 1
    time.sleep(PROVIDER_DELAY)
    if PROVIDER_FAILURE:
        raise RuntimeError("synthetic_provider_failure")
    rows = [
        _row("NVDA", 91.0, 220.0, DATA_THROUGH, "semiconductors"),
        _row("MSFT", 85.0, 95.0, DATA_THROUGH, "software"),
        _row("AAPL", 80.0, 190.0, DATA_THROUGH, "hardware"),
    ]
    published_at = time.time()
    batch = _publish_eod_rows(rows, published_at=published_at)
    return {
        "status": "RAN",
        "purpose": PURPOSE_LIVE,
        "served_session": DATA_THROUGH,
        "published_at": published_at,
        "compute_version": "limited-current-v1.1",
        "available_variants": sorted(batch["variants"]),
        "publish": batch["publication"],
    }


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
    _seed_snapshots(software_fresh=software_fresh)
    REPOSITORY = WorkerStateRepository(scenario / "worker.db")
    SUPERVISOR = WorkerSupervisor(
        REPOSITORY,
        [TaskSpec("strength_refresh", StrengthRefreshTask(
                  snapshot_path=SNAPSHOT, eod_runner=_run_eod_fixture),
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
            str(path.relative_to(SNAPSHOT.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [eod_store.snapshot_path()]
            if path.is_file()
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
