#!/usr/bin/env python3
"""Isolated FastAPI for live screener freshness browser tests.

GET /strength/scan reads published snapshots only. POST strength_refresh runs
the real StrengthRefreshTask against provider stubs, then returns the finished
action so the page can force-read the new snapshot.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

DATA_DIR = Path(os.environ.get("SCREENER_FRESHNESS_DATA", tempfile.mkdtemp(prefix="screener-freshness-")))
os.environ.setdefault("DATA_DIR", str(DATA_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

from app.api import strength  # noqa: E402
from app.services.breakouts.config import BreakoutSettings  # noqa: E402
from app.services.strength import scanner  # noqa: E402
from app.services.strength.market_regime import MARKET_BENCHMARKS  # noqa: E402
from app.worker.tasks import StrengthRefreshTask  # noqa: E402

SNAPSHOT = DATA_DIR / "strength-snapshot-v1.json"
NOW = datetime(2026, 9, 4, 16, 30, tzinfo=timezone.utc).timestamp()
ACTIONS: dict[str, dict] = {}


def _history(*, slope: float, offset: float = 0.0, size: int = 320) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-09-04", periods=size, tz="America/New_York")
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
    scanner._download_history = lambda symbols, period="2y": panel
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
        "daily_data_through": through,
        "price_as_of": through,
        "sector_id": sector_id,
        "sector_name": sector_name,
    }


def _seed_snapshots() -> None:
    strength._STRENGTH_SNAPSHOT_PATH = SNAPSHOT
    through = "2026-09-03T20:00:00+00:00"
    default_row = _row("AAPL", 80.0, 190.0, through, "hardware", "硬件")
    strength._write_strength_snapshot(
        SNAPSHOT,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload={
            "as_of": "2026-09-03T20:30:00+00:00",
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
    old = "2026-07-02T20:00:00+00:00"
    old_row = _row("NVDA", 91.0, 12.5, old, "semiconductors", "半导体")
    strength._write_strength_snapshot(
        strength._strength_snapshot_path(variant_params, base_path=SNAPSHOT),
        base_path=SNAPSHOT,
        parameters=variant_params,
        payload={
            "as_of": "2026-07-02T15:01:00+00:00",
            "score_data_through": old,
            "params": {key: value for key, value in variant_params.items() if key != "include_options"},
            "count": 1,
            "rows": [old_row],
            "results": [old_row],
            "universe_count": 3,
            "screened_count": 1,
            "data_sources": {"prices": {"status": "active", "provider": "legacy-snapshot"}},
        },
        saved_at=NOW - 30,
    )


_install_provider_boundary()
_seed_snapshots()

app = FastAPI()


@app.get("/health")
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


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


@app.get("/api/worker/status")
def worker_status() -> dict:
    return {"healthy": True, "status": "idle", "tasks": [{"name": "strength_refresh", "enabled": True}]}


@app.get("/api/worker/actions/{request_id}")
def worker_action_status(request_id: str) -> dict:
    action = ACTIONS.get(request_id)
    if action is None:
        return JSONResponse({"detail": {"code": "action_not_found"}}, status_code=404)
    return action


@app.post("/api/worker/actions/strength_refresh")
async def strength_refresh(request: Request) -> JSONResponse:
    body = await request.json()
    raw = body.get("parameters") or dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    parameters = strength.normalize_strength_scan_parameters(raw)
    request_id = str(uuid.uuid4())
    result = await StrengthRefreshTask(snapshot_path=SNAPSHOT).run_for_actions(
        [{"details": {"parameters": parameters, "parameters_hash": strength.strength_scan_parameters_hash(parameters)}}]
    )
    action = {
        "request_id": request_id,
        "action_type": "strength_refresh",
        "status": "completed" if result.status == "idle" else "failed",
        "error_code": result.error_code,
        "details": dict(result.details),
        "reused": False,
        "reason": "queued",
        "completed_at": result.details.get("completed_at"),
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    ACTIONS[request_id] = action
    return JSONResponse(action, status_code=202)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def other_api(rest: str):
    if rest.startswith("strength/") or rest.startswith("worker/"):
        return JSONResponse({"detail": "unhandled"}, status_code=404)
    return JSONResponse({}, status_code=200)


if __name__ == "__main__":
    host = os.environ.get("SCREENER_FRESHNESS_HOST", "127.0.0.1")
    port = int(os.environ.get("SCREENER_FRESHNESS_PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="warning")
