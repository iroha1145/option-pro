from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import strength


NOW = 1_789_000_000.0


def _payload(
    *,
    top: int = 20,
    ticker: str = "AAPL",
    parameters: dict | None = None,
) -> dict:
    canonical = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    canonical["top"] = top
    if parameters is not None:
        canonical = strength.normalize_strength_scan_parameters(parameters)
    row = {"ticker": ticker, "score": 91.0}
    return {
        "as_of": "2026-09-07T11:06:40+00:00",
        "params": {
            key: value for key, value in canonical.items() if key != "include_options"
        },
        "count": 1,
        "rows": [row],
        "results": [row],
    }


def _run_scan(*, top: int = 20) -> dict:
    return asyncio.run(
        strength.scan(
            universe="themes",
            timeframe="all",
            profile="balanced",
            top=top,
            sector_id=None,
            min_price=5.0,
            min_avg_dollar_volume=10_000_000.0,
        )
    )


def test_default_scan_reads_fresh_worker_snapshot_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert strength.DEFAULT_STRENGTH_SCAN_PARAMETERS["top"] == 20
    path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(),
        saved_at=NOW - 10,
    )

    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)

    result = _run_scan()

    assert result["rows"][0]["ticker"] == "AAPL"
    assert result["snapshot_source"] == "worker"
    assert result["_cached"] is True
    assert result["_stale"] is False
    assert result["source_status"] == "active"
    assert result["snapshot_saved_at"].endswith("+00:00")


@pytest.mark.parametrize(
    "invalid_kind",
    ["missing", "parameters", "payload", "corrupt"],
)
def test_invalid_or_mismatched_snapshot_returns_typed_unavailable_without_scanning(
    invalid_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    if invalid_kind != "missing":
        strength._write_strength_snapshot(
            path,
            parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
            payload=_payload(),
            saved_at=NOW - 10,
        )
    if invalid_kind == "parameters":
        document = json.loads(path.read_text(encoding="utf-8"))
        document["parameters"]["top"] = 31
        path.write_text(json.dumps(document), encoding="utf-8")
    elif invalid_kind == "payload":
        document = json.loads(path.read_text(encoding="utf-8"))
        document["payload"]["count"] = 2
        path.write_text(json.dumps(document), encoding="utf-8")
    elif invalid_kind == "corrupt":
        path.write_text("{broken", encoding="utf-8")

    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)

    with pytest.raises(HTTPException) as caught:
        _run_scan()

    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "code": "strength_snapshot_unavailable",
        "status": "unavailable",
        "message": "强势雷达后台快照暂不可用",
    }


def test_expired_cache_is_returned_as_stale_worker_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    saved_at = NOW - strength.STRENGTH_CACHE_TTL_SECONDS - 5
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(),
        saved_at=saved_at,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)

    result = _run_scan()

    assert result["rows"][0]["ticker"] == "AAPL"
    assert result["snapshot_source"] == "worker"
    assert result["_stale"] is True
    assert result["source_status"] == "stale"
    assert result["stale_reason"] == "worker_snapshot_expired"
    assert result["stale_age_seconds"] == pytest.approx(NOW - saved_at)


def test_old_snapshot_remains_visible_after_refresh_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    saved_at = NOW - 3 * 24 * 60 * 60
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="MSFT"),
        saved_at=saved_at,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)

    result = _run_scan()

    assert result["rows"][0]["ticker"] == "MSFT"
    assert result["_stale"] is True
    assert result["source_status"] == "stale"
    assert result["stale_age_seconds"] == pytest.approx(NOW - saved_at)


def test_nondefault_parameters_do_not_reuse_default_worker_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(),
        saved_at=NOW - 10,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)

    with pytest.raises(HTTPException) as caught:
        _run_scan(top=31)

    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "strength_snapshot_unavailable"


def test_strength_snapshot_replace_is_atomic_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="AAPL"),
        saved_at=NOW - 10,
    )
    original = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr(strength.os, "replace", fail_replace)

    with pytest.raises(OSError):
        strength._write_strength_snapshot(
            path,
            parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
            payload=_payload(ticker="MSFT"),
            saved_at=NOW,
        )

    assert path.read_bytes() == original
    assert list(tmp_path.glob(".strength-snapshot-v1.json.*.tmp")) == []


def test_nondefault_worker_snapshot_is_path_isolated_and_read_by_exact_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    parameters = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "timeframe": "short",
        "profile": "aggressive",
        "top": 30,
        "sector_id": "semiconductors",
        "min_price": 0.0,
        "min_avg_dollar_volume": 0.0,
        "include_options": False,
    }
    target = strength._strength_snapshot_path(parameters, base_path=base)
    assert target != base
    assert strength.strength_scan_parameters_hash(parameters) in target.name
    strength._write_strength_snapshot(
        target,
        base_path=base,
        parameters=parameters,
        payload=_payload(parameters=parameters, ticker="NVDA"),
        saved_at=NOW - 10,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", base)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)

    result = asyncio.run(strength.scan(**parameters))

    assert result["rows"][0]["ticker"] == "NVDA"
    assert result["snapshot_source"] == "worker"
    with pytest.raises(HTTPException) as caught:
        _run_scan()
    assert caught.value.status_code == 503


def test_variant_writer_rejects_wrong_path_and_bounds_variant_count(
    tmp_path: Path,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        base,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(),
        saved_at=NOW,
    )
    nondefault = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": 30}
    with pytest.raises(ValueError, match="path does not match"):
        strength._write_strength_snapshot(
            base,
            base_path=base,
            parameters=nondefault,
            payload=_payload(parameters=nondefault),
            saved_at=NOW,
        )

    for top in [value for value in range(5, 32) if value != 20]:
        parameters = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": top}
        target = strength._strength_snapshot_path(parameters, base_path=base)
        strength._write_strength_snapshot(
            target,
            base_path=base,
            parameters=parameters,
            payload=_payload(parameters=parameters),
            saved_at=NOW,
        )

    variants = list(tmp_path.glob("strength-snapshot-v1-*.json"))
    assert len(variants) == strength._STRENGTH_SNAPSHOT_VARIANT_LIMIT
    assert base.is_file()


@pytest.mark.parametrize(
    "updates",
    [
        {"top": True},
        {"top": 121},
        {"sector_id": "../../escape"},
        {"min_price": float("nan")},
        {"include_options": 1},
    ],
)
def test_parameter_normalization_rejects_unsafe_boundaries(updates: dict) -> None:
    with pytest.raises(ValueError):
        strength.normalize_strength_scan_parameters(
            {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, **updates}
        )
