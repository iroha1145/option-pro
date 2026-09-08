"""Regression cases found while reviewing the published snapshot path."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import pytest

from app.api import strength
from app.services.strength import scanner
from app.worker.tasks import StrengthRefreshTask
from tests.http_response_support import anonymous_get_request, response_payload
from tests.test_screener_freshness_task_chain import _install_provider_boundary
from tests.test_strength_variant_lifecycle import _payload


OBSERVED = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
CURRENT_THROUGH = "2026-09-04T20:00:00+00:00"
OLD_THROUGH = "2026-07-02T20:00:00+00:00"


@pytest.fixture
def snapshot_path(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "strength-snapshot-v1.json"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: OBSERVED.timestamp())
    return path


def _publish(path: Path, payload: dict) -> None:
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=payload,
        saved_at=OBSERVED.timestamp() - 10,
    )


def _read() -> dict:
    return response_payload(asyncio.run(strength.scan(
        anonymous_get_request(), **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
    )))


@pytest.mark.parametrize(
    ("version", "status", "reason"),
    [
        ("strength-obsolete", "stale", "scoring_version_mismatch"),
        (None, "unknown", "missing_scoring_version"),
    ],
)
def test_current_daily_bars_cannot_hide_unverified_scoring_version(
    snapshot_path: Path, version: str | None, status: str, reason: str,
) -> None:
    payload = _payload(through=CURRENT_THROUGH)
    payload["score_version"] = version
    _publish(snapshot_path, payload)
    result = _read()
    assert result["source_status"] == status
    assert result["stale_reason"] == reason


def test_summary_date_cannot_hide_an_old_displayed_row(snapshot_path: Path) -> None:
    payload = _payload(through=OLD_THROUGH)
    payload["score_version"] = scanner.STRENGTH_SCORE_VERSION
    payload["score_data_through"] = CURRENT_THROUGH
    _publish(snapshot_path, payload)
    result = _read()
    assert result["source_status"] == "historical"
    assert result["score_data_through"] == OLD_THROUGH
    assert result["rows"][0]["daily_data_through"] == OLD_THROUGH


def test_future_input_rejected_by_policy_is_not_reintroduced_by_get(snapshot_path: Path) -> None:
    payload = _payload(through="2026-12-01T21:00:00+00:00")
    payload["score_version"] = scanner.STRENGTH_SCORE_VERSION
    _publish(snapshot_path, payload)
    result = _read()
    assert result["source_status"] == "unknown"
    assert result.get("score_data_through") is None


def _install_history(monkeypatch):
    _install_provider_boundary(monkeypatch)
    panel = scanner._download_history(["NVDA", "AAPL", "MSFT"])

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return OBSERVED.astimezone(tz) if tz else OBSERVED.replace(tzinfo=None)

    monkeypatch.setattr(scanner, "datetime", FixedDatetime)
    monkeypatch.setattr(scanner, "_download_history", lambda *_args, **_kwargs: panel)
    return panel


def test_real_scanner_does_not_use_a_different_sectors_newer_daily_date(
    snapshot_path: Path, monkeypatch,
) -> None:
    panel = _install_history(monkeypatch)
    panel.loc[panel.index.date > datetime(2026, 7, 2).date(), "NVDA"] = float("nan")
    parameters = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "sector_id": "semiconductors", "include_options": False,
        "min_price": 0.0, "min_avg_dollar_volume": 0.0,
    }
    payload = asyncio.run(scanner.scan_strength(**parameters, force_refresh=True))
    assert [row["ticker"] for row in payload["rows"]] == ["NVDA"]
    assert payload["rows"][0]["daily_data_through"] == OLD_THROUGH
    assert payload["score_data_through"] == OLD_THROUGH


def test_provider_truncated_all_histories_keeps_the_previous_snapshot(
    snapshot_path: Path, monkeypatch,
) -> None:
    panel = _install_history(monkeypatch)
    for symbol in ("NVDA", "AAPL", "MSFT"):
        panel.loc[panel.index[:-4], symbol] = float("nan")
    old = _payload(through=CURRENT_THROUGH)
    old["score_version"] = scanner.STRENGTH_SCORE_VERSION
    _publish(snapshot_path, old)
    before = snapshot_path.read_bytes()
    result = asyncio.run(StrengthRefreshTask(
        snapshot_path=snapshot_path, clock=lambda: OBSERVED.timestamp(),
    )())
    assert result.status == "degraded"
    assert result.details["result"] == "kept_previous_snapshot"
    assert snapshot_path.read_bytes() == before


def test_partial_missing_history_still_publishes_the_other_scored_rows(
    snapshot_path: Path, monkeypatch,
) -> None:
    panel = _install_history(monkeypatch)
    panel.loc[panel.index[:-4], "NVDA"] = float("nan")
    result = asyncio.run(StrengthRefreshTask(
        snapshot_path=snapshot_path, clock=lambda: OBSERVED.timestamp(),
    )())
    assert result.status == "idle"
    payload = _read()
    assert {row["ticker"] for row in payload["rows"]} == {"AAPL", "MSFT"}
    assert payload["skipped"]["insufficient_history"] == 1
    assert payload["source_status"] == "active"


def test_cancelled_provider_thread_cannot_publish_after_a_newer_scan(
    snapshot_path: Path, monkeypatch,
) -> None:
    panel = _install_history(monkeypatch)
    older_panel = panel.loc[panel.index.date <= datetime(2026, 7, 2).date()].copy()
    started, release, finished = Event(), Event(), Event()
    calls = 0

    def download(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            try:
                assert release.wait(5), "test must release the provider thread"
                return older_panel
            finally:
                finished.set()
        return panel

    monkeypatch.setattr(scanner, "_download_history", download)

    async def scenario():
        task = StrengthRefreshTask(
            snapshot_path=snapshot_path, clock=lambda: OBSERVED.timestamp(),
        )
        cancelled = asyncio.create_task(task())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            result = await task()
            assert result.status == "idle"
            published = snapshot_path.read_bytes()
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
            assert snapshot_path.read_bytes() == published
        finally:
            release.set()
            if not cancelled.done():
                cancelled.cancel()
                await asyncio.gather(cancelled, return_exceptions=True)

    asyncio.run(scenario())
    assert _read()["score_data_through"] == CURRENT_THROUGH


def test_malformed_variant_cannot_abort_successful_default_refresh(
    snapshot_path: Path,
) -> None:
    variant = snapshot_path.with_name(f"{snapshot_path.stem}-{'a' * 20}.json")
    variant.write_text("[]", encoding="utf-8")

    async def scan(**_kwargs):
        payload = _payload(through=CURRENT_THROUGH)
        payload["score_version"] = scanner.STRENGTH_SCORE_VERSION
        return payload

    result = asyncio.run(StrengthRefreshTask(
        scanner=scan, snapshot_path=snapshot_path,
        clock=lambda: OBSERVED.timestamp(),
    )())
    assert result.status == "idle"
    assert result.details["result"] == "refreshed"
    assert result.details["variant_refresh_attempted"] == 0


@pytest.mark.parametrize("invalid_kind", ["oversized", "filename_parameters"])
def test_variant_discovery_skips_unreadable_or_misidentified_snapshots(
    snapshot_path: Path, invalid_kind: str,
) -> None:
    parameters = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 40}
    variant = strength._strength_snapshot_path(parameters, base_path=snapshot_path)
    document = {
        "version": 1,
        "saved_at": OBSERVED.timestamp() - 10,
        "parameters": parameters,
        "payload": _payload(parameters, through=CURRENT_THROUGH),
    }
    if invalid_kind == "oversized":
        document["padding"] = "x" * strength._STRENGTH_SNAPSHOT_MAX_BYTES
    else:
        document["parameters"] = {**parameters, "top": 50}
        document["payload"] = _payload(document["parameters"], through=CURRENT_THROUGH)
    variant.write_text(json.dumps(document), encoding="utf-8")
    assert strength.list_recent_strength_variant_parameters(snapshot_path) == []
