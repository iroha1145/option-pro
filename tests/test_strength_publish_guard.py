"""Formal R1/R2 regressions for snapshot quality and scheduled variant status.

These tests import production writers and StrengthRefreshTask. They do not copy
review-package excerpts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.api import strength
from app.services.strength.freshness import should_replace_published_snapshot
from app.worker.tasks import StrengthRefreshTask
from tests.test_strength_variant_lifecycle import _payload

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 4, 16, 30, tzinfo=ET).timestamp()
CURRENT = "2026-09-04T20:00:00+00:00"
PREVIOUS = "2026-09-03T20:00:00+00:00"
DEFAULT = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
VARIANT_RETRY_SECONDS = 300.0


def _failed_payload(parameters: dict) -> dict:
    canonical = strength.normalize_strength_scan_parameters(parameters)
    return {
        "as_of": "2026-09-04T20:30:00+00:00",
        "score_version": strength.STRENGTH_SCORE_VERSION,
        "params": {key: value for key, value in canonical.items() if key != "include_options"},
        "count": 0,
        "rows": [],
        "results": [],
        "universe_count": 8,
        "skipped": {"data_error": 8},
        "data_sources": {"prices": {"status": "failed"}},
    }


def _multi_row_payload(
    parameters: dict,
    rows: list[tuple[str, str | None]],
    *,
    summary: str | None = None,
) -> dict:
    payload = _payload(parameters, ticker=rows[0][0], through=rows[0][1])
    built = []
    throughs: list[str] = []
    for ticker, through in rows:
        row: dict = {"ticker": ticker, "score": 91.0}
        if through:
            row["daily_data_through"] = through
            row["price_as_of"] = through
            throughs.append(through)
        built.append(row)
    payload["rows"] = built
    payload["results"] = list(built)
    payload["count"] = len(built)
    if summary is None:
        payload["score_data_through"] = min(throughs) if throughs else None
    else:
        payload["score_data_through"] = summary
    return payload


def _write_default(path: Path, *, through: str = CURRENT, saved_at: float = NOW - 60) -> bytes:
    strength._write_strength_snapshot(
        path,
        parameters=dict(DEFAULT),
        payload=_payload(through=through),
        saved_at=saved_at,
    )
    return path.read_bytes()


def _write_variant(
    base: Path,
    parameters: dict,
    *,
    through: str = PREVIOUS,
    saved_at: float = NOW - 30,
    mtime: float | None = None,
) -> Path:
    target = strength._strength_snapshot_path(parameters, base_path=base)
    strength._write_strength_snapshot(
        target,
        base_path=base,
        parameters=parameters,
        payload=_payload(parameters, ticker="NVDA", through=through),
        saved_at=saved_at,
    )
    if mtime is not None:
        import os

        os.utime(target, (mtime, mtime))
    return target


def test_write_does_not_replace_older_input_with_newer_saved_at(tmp_path: Path) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    before = _write_default(path, through=CURRENT, saved_at=NOW)
    outcome = strength._write_strength_snapshot(
        path,
        parameters=dict(DEFAULT),
        payload=_payload(through=PREVIOUS),
        saved_at=NOW + 60,
    )
    assert outcome != "written"
    assert path.read_bytes() == before
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["payload"]["score_data_through"] == CURRENT
    assert document["saved_at"] == NOW


def test_run_does_not_publish_computable_but_older_provider_data(tmp_path: Path) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    before = _write_default(path, through=CURRENT, saved_at=NOW)

    async def older_but_publishable(**_kwargs):
        return _payload(through=PREVIOUS)

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=older_but_publishable,
            snapshot_path=path,
            clock=lambda: NOW + 60,
        )._run(dict(DEFAULT))
    )
    assert result.details.get("published") is not True
    assert result.status == "degraded"
    assert result.error_code
    assert result.details.get("reason")
    assert result.details.get("result") == "kept_previous_snapshot"
    assert path.read_bytes() == before


def test_partial_row_lag_does_not_replace_even_when_summary_looks_current(
    tmp_path: Path,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    good = _multi_row_payload(DEFAULT, [("AAPL", CURRENT), ("MSFT", CURRENT)])
    strength._write_strength_snapshot(
        path, parameters=dict(DEFAULT), payload=good, saved_at=NOW,
    )
    before = path.read_bytes()
    lagged = _multi_row_payload(
        DEFAULT,
        [("AAPL", CURRENT), ("MSFT", PREVIOUS)],
        summary=CURRENT,
    )
    outcome = strength._write_strength_snapshot(
        path, parameters=dict(DEFAULT), payload=lagged, saved_at=NOW + 60,
    )
    assert outcome != "written"
    assert path.read_bytes() == before


def test_unknown_incoming_time_does_not_replace_known_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    before = _write_default(path, through=CURRENT, saved_at=NOW)
    unknown = _payload(through=None)
    unknown.pop("score_data_through", None)
    outcome = strength._write_strength_snapshot(
        path, parameters=dict(DEFAULT), payload=unknown, saved_at=NOW + 60,
    )
    assert outcome != "written"
    assert path.read_bytes() == before


def test_same_session_republish_with_unchanged_prices_is_allowed(tmp_path: Path) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    _write_default(path, through=CURRENT, saved_at=NOW)
    outcome = strength._write_strength_snapshot(
        path,
        parameters=dict(DEFAULT),
        payload=_payload(ticker="AAPL", through=CURRENT),
        saved_at=NOW + 60,
    )
    assert outcome == "written"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["saved_at"] == NOW + 60
    assert document["payload"]["score_data_through"] == CURRENT


def test_late_saved_at_still_loses_to_newer_publish(tmp_path: Path) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    _write_default(path, through=CURRENT, saved_at=NOW)
    outcome = strength._write_strength_snapshot(
        path,
        parameters=dict(DEFAULT),
        payload=_payload(ticker="OLD", through=CURRENT),
        saved_at=NOW - 30,
    )
    assert outcome == "kept_newer_publish"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "AAPL" in path.read_text(encoding="utf-8")
    assert "OLD" not in path.read_text(encoding="utf-8")
    assert document["saved_at"] == NOW


def test_saved_at_only_helper_still_rejects_late_publish() -> None:
    from app.services.strength.freshness import decide_published_snapshot_replacement

    assert should_replace_published_snapshot(
        existing_saved_at=100.0,
        incoming_saved_at=99.0,
    ) is False
    decision = decide_published_snapshot_replacement(
        existing_saved_at=NOW,
        incoming_saved_at=NOW + 60,
        existing_payload=_payload(through=CURRENT),
        incoming_payload=_payload(through=PREVIOUS),
    )
    assert decision.replace is False
    assert decision.reason


def test_scheduled_refresh_does_not_hide_variant_degraded(tmp_path: Path) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    _write_default(base)
    variant = {**DEFAULT, "sector_id": "semiconductors"}
    _write_variant(base, variant, mtime=NOW)

    async def scanner(**kwargs):
        parameters = {key: kwargs[key] for key in DEFAULT}
        if kwargs.get("sector_id") == "semiconductors":
            return _failed_payload(parameters)
        return _payload(parameters, through=CURRENT)

    result = asyncio.run(
        StrengthRefreshTask(scanner=scanner, snapshot_path=base, clock=lambda: NOW)()
    )
    assert result.status == "degraded"
    assert result.error_code
    assert result.details["variant_refresh_attempted"] == 1
    assert result.details["variant_refresh_failed"] == 1
    assert result.details["variant_refresh_published"] == 0
    errors = result.details["variant_refresh_errors"]
    assert errors
    assert errors[0]["parameters_hash"] == strength.strength_scan_parameters_hash(variant)
    assert errors[0]["error_code"]
    assert errors[0]["reason"]


def test_scheduled_refresh_does_not_hide_variant_exception(tmp_path: Path) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    _write_default(base)
    variant = {**DEFAULT, "sector_id": "semiconductors"}
    _write_variant(base, variant, mtime=NOW)

    async def scanner(**kwargs):
        if kwargs.get("sector_id") == "semiconductors":
            raise ValueError("provider exploded")
        parameters = {key: kwargs[key] for key in DEFAULT}
        return _payload(parameters, through=CURRENT)

    result = asyncio.run(
        StrengthRefreshTask(scanner=scanner, snapshot_path=base, clock=lambda: NOW)()
    )
    assert result.status == "degraded"
    assert result.error_code
    assert result.details["variant_refresh_failed"] == 1
    errors = result.details["variant_refresh_errors"]
    assert errors[0]["parameters_hash"] == strength.strength_scan_parameters_hash(variant)
    assert errors[0]["reason"] == "ValueError"


def test_scheduled_refresh_partial_failure_counts_match_hashes(tmp_path: Path) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    _write_default(base)
    degraded = {**DEFAULT, "sector_id": "semiconductors"}
    exploding = {**DEFAULT, "sector_id": "software"}
    healthy = {**DEFAULT, "top": 40}
    for index, parameters in enumerate((degraded, exploding, healthy), start=1):
        _write_variant(base, parameters, mtime=NOW + index)

    async def scanner(**kwargs):
        parameters = {key: kwargs[key] for key in DEFAULT}
        if kwargs.get("sector_id") == "semiconductors":
            return _failed_payload(parameters)
        if kwargs.get("sector_id") == "software":
            raise RuntimeError("timeout")
        return _payload(parameters, ticker="X", through=CURRENT)

    result = asyncio.run(
        StrengthRefreshTask(scanner=scanner, snapshot_path=base, clock=lambda: NOW)()
    )
    assert result.status == "degraded"
    assert result.details["variant_refresh_attempted"] == 3
    assert result.details["variant_refresh_published"] == 1
    assert result.details["variant_refresh_failed"] == 2
    hashes = {item["parameters_hash"] for item in result.details["variant_refresh_errors"]}
    assert hashes == {
        strength.strength_scan_parameters_hash(degraded),
        strength.strength_scan_parameters_hash(exploding),
    }


def test_scheduled_refresh_all_variants_succeed_without_errors(tmp_path: Path) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    _write_default(base)
    recent = {**DEFAULT, "sector_id": "semiconductors"}
    _write_variant(base, recent, mtime=NOW)

    async def scanner(**kwargs):
        parameters = {key: kwargs[key] for key in DEFAULT}
        return _payload(parameters, ticker="X", through=CURRENT)

    result = asyncio.run(
        StrengthRefreshTask(scanner=scanner, snapshot_path=base, clock=lambda: NOW)()
    )
    assert result.status == "idle"
    assert result.error_code is None
    assert result.details["variant_refresh_attempted"] == 1
    assert result.details["variant_refresh_published"] == 1
    assert result.details["variant_refresh_failed"] == 0
    assert result.details["variant_refresh_errors"] == []


def test_default_failure_keeps_existing_failure_semantics(tmp_path: Path) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    before = _write_default(base)
    recent = {**DEFAULT, "sector_id": "semiconductors"}
    _write_variant(base, recent, mtime=NOW)

    async def scanner(**kwargs):
        parameters = {key: kwargs[key] for key in DEFAULT}
        if kwargs.get("sector_id") is None:
            return _failed_payload(parameters)
        return _payload(parameters, through=CURRENT)

    result = asyncio.run(
        StrengthRefreshTask(scanner=scanner, snapshot_path=base, clock=lambda: NOW)()
    )
    assert result.status == "degraded"
    assert result.error_code == "strength_input_unavailable"
    assert result.next_delay_seconds is None
    assert result.details["result"] == "kept_previous_snapshot"
    assert json.loads(base.read_bytes())["payload"]["score_data_through"] == CURRENT
    assert before == base.read_bytes()


def test_variant_retry_does_not_postpone_default_or_pile_tasks(tmp_path: Path) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    _write_default(base)
    variant = {**DEFAULT, "sector_id": "semiconductors"}
    _write_variant(base, variant, mtime=NOW)
    clock = [NOW]
    default_scans = {"count": 0}

    async def scanner(**kwargs):
        parameters = {key: kwargs[key] for key in DEFAULT}
        if kwargs.get("sector_id") is None:
            default_scans["count"] += 1
            return _payload(parameters, through=CURRENT)
        return _failed_payload(parameters)

    task = StrengthRefreshTask(
        scanner=scanner,
        snapshot_path=base,
        clock=lambda: clock[0],
        scheduled_interval_seconds=86_400.0,
    )
    first = asyncio.run(task())
    marked = task._last_scheduled_at
    assert first.status == "degraded"
    assert first.error_code == "strength_variant_refresh_failed"
    assert first.next_delay_seconds is not None
    assert first.next_delay_seconds <= VARIANT_RETRY_SECONDS
    assert first.next_delay_seconds <= 86_400.0
    deadline = marked + 86_400.0

    clock[0] = NOW + float(first.next_delay_seconds)
    second = asyncio.run(task())
    assert task._last_scheduled_at == marked
    assert second.status == "degraded"
    assert second.next_delay_seconds == pytest.approx(deadline - clock[0])
    assert second.next_delay_seconds <= 86_400.0
    assert default_scans["count"] == 1
