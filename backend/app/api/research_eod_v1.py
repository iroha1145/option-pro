"""Research EOD v1 HTTP surface. Default-off. Never a production ranking."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.data_paths import get_data_paths
from app.services.research_eod_v1 import FEATURE_VERSION, RESEARCH_FLAG
from app.services.research_eod_v1.eod_shadow import (
    NETWORK_COUNTER,
    intraday_view,
    read_snapshot,
    research_enabled,
    snapshot_path,
)

router = APIRouter(prefix="/api/research/eod/v1", tags=["research-eod-v1"])


def _status_payload() -> dict[str, Any]:
    settings = get_settings()
    enabled = research_enabled(settings)
    path = snapshot_path(get_data_paths().root)
    snap = read_snapshot(path)
    return {
        "research_flag": RESEARCH_FLAG,
        "enabled": enabled,
        "feature_version": FEATURE_VERSION,
        "production_default_unchanged": True,
        "production_algorithms_untouched": ["production", "a0_mid_long", "t1_daily_priority"],
        "snapshot_present": snap is not None,
        "snapshot_session": None if snap is None else snap.get("session_date"),
        "network_counters": dict(NETWORK_COUNTER),
        "intraday_provider_calls": NETWORK_COUNTER["intraday_provider_calls"],
        "status": "SHADOW_ONLY" if snap is not None else ("DISABLED" if not enabled else "NO_SNAPSHOT"),
    }


@router.get("/status")
def research_status() -> dict[str, Any]:
    return _status_payload()


@router.get("/snapshot")
def research_snapshot(
    sector_id: str | None = Query(default=None),
    algorithm_id: str | None = Query(default=None),
    profile: str | None = Query(default=None),
    top_k: int = Query(default=20, ge=0, le=100),
) -> dict[str, Any]:
    path = snapshot_path(get_data_paths().root)
    return intraday_view(
        read_snapshot(path),
        sector_id=sector_id,
        algorithm_id=algorithm_id,
        profile=profile,
        top_k=top_k,
    )


@router.post("/refresh")
def research_refresh() -> JSONResponse:
    """Owner-only batch hook. Refuses to run unless the research flag is on.

    This endpoint never becomes the production default scanner.
    """

    if not research_enabled(get_settings()):
        return JSONResponse(
            {
                "status": "DISABLED",
                "reason": "RESEARCH_EOD_V1_ENABLED is false",
                "production_default_unchanged": True,
                "network_calls": 0,
            },
            status_code=409,
        )
    return JSONResponse(
        {
            "status": "DATA_INSUFFICIENT",
            "reason": "licensed_pit_history_not_verified",
            "production_default_unchanged": True,
            "network_calls": 0,
        },
        status_code=409,
    )
