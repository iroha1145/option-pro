"""Current EOD action, publication and public-read integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import strength, worker_actions
from app.worker.state import WorkerStateRepository
from app.worker.tasks import StrengthRefreshTask


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
