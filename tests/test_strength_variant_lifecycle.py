from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api import strength
from app.worker.tasks import StrengthRefreshTask
from tests.http_response_support import anonymous_get_request as _areq, response_payload as _rp

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 4, 16, 30, tzinfo=ET).timestamp()


def _payload(parameters: dict | None = None, *, ticker: str = "AAPL", through: str | None = None) -> dict:
    canonical = strength.normalize_strength_scan_parameters(
        parameters or strength.DEFAULT_STRENGTH_SCAN_PARAMETERS
    )
    row = {"ticker": ticker, "score": 91.0}
    if through:
        row["daily_data_through"] = through
        row["price_as_of"] = through
    return {
        "as_of": "2026-09-04T20:30:00+00:00",
        "score_version": strength.STRENGTH_SCORE_VERSION,
        "score_data_through": through,
        "params": {key: value for key, value in canonical.items() if key != "include_options"},
        "count": 1,
        "rows": [row],
        "results": [row],
        "data_sources": {"prices": {"status": "active"}},
    }


def test_b01_fresh_default_does_not_make_old_semiconductor_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    variant_params = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "sector_id": "semiconductors",
    }
    strength._write_strength_snapshot(
        base,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW - 60,
    )
    variant = strength._strength_snapshot_path(variant_params, base_path=base)
    strength._write_strength_snapshot(
        variant,
        base_path=base,
        parameters=variant_params,
        payload=_payload(variant_params, ticker="NVDA", through="2026-07-02T20:00:00+00:00"),
        saved_at=NOW - 30,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", base)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)

    default = _rp(asyncio.run(strength.scan(_areq(), **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)))
    variant_body = _rp(asyncio.run(strength.scan(_areq(), **variant_params)))

    assert default["_stale"] is False
    assert default["source_status"] == "active"
    assert variant_body["_stale"] is True
    assert variant_body["source_status"] == "historical"
    assert variant_body["rows"][0]["ticker"] == "NVDA"


def test_b05_today_write_with_old_bars_is_not_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(through="2026-07-02T20:00:00+00:00"),
        saved_at=NOW,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    result = _rp(asyncio.run(strength.scan(_areq(), **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)))
    assert result["_stale"] is True
    assert result["stale_reason"] == "score_data_too_old"
    assert result["snapshot_saved_at"].startswith("2026-09-04")


def test_b09_corrupt_and_mismatched_snapshots_stay_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(strength.scan(_areq(), **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "strength_snapshot_unavailable"


def test_late_write_cannot_replace_newer_publish(tmp_path: Path) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="NEW", through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW,
    )
    outcome = strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="OLD", through="2026-07-02T20:00:00+00:00"),
        saved_at=NOW - 30,
    )
    assert outcome == "kept_newer_publish"
    document = path.read_text(encoding="utf-8")
    assert "NEW" in document
    assert "OLD" not in document


def test_scheduled_refresh_updates_recent_variants_only(
    tmp_path: Path,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    calls: list[str | None] = []

    async def fake_scanner(**kwargs):
        calls.append(kwargs.get("sector_id"))
        parameters = {key: kwargs[key] for key in strength.DEFAULT_STRENGTH_SCAN_PARAMETERS}
        return _payload(parameters, ticker="X", through="2026-09-03T20:00:00+00:00")

    recent = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "semiconductors"}
    older = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "software"}
    unused = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 40}
    extra = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 50}
    extra2 = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 60}
    extra3 = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 70}
    extra4 = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 80}
    import os

    for index, parameters in enumerate((older, unused, extra, extra2, extra3, extra4, recent), start=1):
        target = strength._strength_snapshot_path(parameters, base_path=base)
        strength._write_strength_snapshot(
            target,
            base_path=base,
            parameters=parameters,
            payload=_payload(parameters, ticker="V", through="2026-07-02T20:00:00+00:00"),
            saved_at=NOW - 1000 + index,
        )
        stamped = NOW - 200 + index
        os.utime(target, (stamped, stamped))

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=base,
            clock=lambda: NOW,
        )()
    )
    assert result.status == "idle"
    assert calls[0] is None
    assert "semiconductors" in calls
    assert result.details["variant_refresh_attempted"] == 4
    assert len(calls) == 5
    assert "software" not in calls


def test_failed_provider_does_not_publish_empty_fresh_snapshot(tmp_path: Path) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        base,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="KEEP", through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW - 10,
    )
    original = base.read_bytes()

    async def failed(**_kwargs):
        return {
            "as_of": "2026-09-04T20:30:00+00:00",
            "params": {
                key: value
                for key, value in strength.DEFAULT_STRENGTH_SCAN_PARAMETERS.items()
                if key != "include_options"
            },
            "count": 0,
            "rows": [],
            "results": [],
            "universe_count": 8,
            "skipped": {"data_error": 8},
            "data_sources": {"prices": {"status": "failed"}},
        }

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=failed,
            snapshot_path=base,
            clock=lambda: NOW,
        )()
    )
    assert result.status == "degraded"
    assert result.error_code == "strength_input_unavailable"
    assert result.details["result"] == "kept_previous_snapshot"
    assert base.read_bytes() == original
