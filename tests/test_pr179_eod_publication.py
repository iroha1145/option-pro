"""A completed EOD refresh must identify the snapshot the reader can see."""

import asyncio
import time
from datetime import datetime

import pytest

from app.api import strength
from app.services.eod_limited import PURPOSE_LIVE, store
from app.services.eod_limited.worker import (
    build_synthetic_panel,
    resolve_inference_session,
    run_eod_limited_job,
)
from app.worker.tasks import StrengthRefreshTask


def _parameters():
    return {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "ranking_algorithm": "eod_limited_v1",
        "timeframe": "mid",
    }


def test_completed_eod_action_identifies_its_readable_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "snapshot_dir", lambda root=None: tmp_path / "eod-limited-v1")
    session = resolve_inference_session()
    panel = build_synthetic_panel(end=session)

    def runner(**kwargs):
        return run_eod_limited_job(panel=panel, session=session, **kwargs)

    # Task bookkeeping happens after the atomic publication, often much later
    # when serialization or disk writes are slow.
    task = StrengthRefreshTask(eod_runner=runner, clock=lambda: time.time() + 60)
    result = asyncio.run(task._run(_parameters()))
    assert result.status == "idle"
    assert result.details["published"] is True
    payload, saved_at, stale = strength._read_eod_limited_snapshot(
        parameters=_parameters(), list_kind="observation", resolution=None,
    )
    assert payload["purpose"] == PURPOSE_LIVE
    assert payload["source_status"] == "active"
    assert stale is False
    completed_at = datetime.fromisoformat(result.details["completed_at"].replace("Z", "+00:00"))
    visible_at = datetime.fromisoformat(payload["snapshot_saved_at"])
    assert completed_at == visible_at
    assert abs(completed_at.timestamp() - saved_at) < 0.000001
    assert result.details["score_version"] == payload["score_version"]


@pytest.mark.parametrize("published_at", [None, 0, float("nan"), float("inf"), True])
def test_eod_action_does_not_claim_success_without_publication_time(published_at):
    task = StrengthRefreshTask(eod_runner=lambda **kwargs: {
        "status": "RAN",
        "publish": {"ok": True},
        "published_at": published_at,
    })
    result = asyncio.run(task._run(_parameters()))
    assert result.status == "degraded"
    assert result.details["published"] is False
