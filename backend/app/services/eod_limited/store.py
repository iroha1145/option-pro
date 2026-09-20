"""Isolated EOD limited snapshot store. Not the strength 24-variant cache."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from app.data_paths import get_data_paths

from . import PURPOSE_LIVE

BATCH_NAME = "batch.json"


def snapshot_dir(root: Path | None = None) -> Path:
    base = Path(root or get_data_paths().root)
    return base / "eod-limited-v1"


def snapshot_path(root: Path | None = None) -> Path:
    return snapshot_dir(root) / BATCH_NAME


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_batch(root: Path | None = None) -> dict[str, Any] | None:
    path = snapshot_path(root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def publish_batch(
    payload: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    previous = read_batch(root)
    try:
        body = dict(payload)
        body["published_at"] = body.get("published_at") or time.time()
        body["integrity"] = "complete"
        _atomic_write(snapshot_path(root), body)
        return {"ok": True, "served_session": body.get("served_session"), "integrity": "complete"}
    except OSError:
        if previous is None:
            raise
        return {
            "ok": False,
            "publish_failed": True,
            "integrity": "stale_previous_retained",
            "served_session": previous.get("served_session"),
            "attempted_session": payload.get("attempted_session"),
        }


def variant_key(profile: str, horizon: str) -> str:
    return f"{profile}|{horizon}"


def read_variant(
    profile: str,
    horizon: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    batch = read_batch(root)
    if batch is None:
        return None
    variants = batch.get("variants") or {}
    scored = variants.get(variant_key(profile, horizon))
    if not isinstance(scored, dict):
        return None
    out = dict(scored)
    out["purpose"] = batch.get("purpose") or out.get("purpose")
    out["served_session"] = batch.get("served_session") or out.get("served_session")
    out["attempted_session"] = batch.get("attempted_session") or out.get("attempted_session")
    out["historical_example"] = bool(batch.get("purpose") and batch.get("purpose") != PURPOSE_LIVE)
    out["synthetic"] = bool(batch.get("purpose") == "synthetic" or out.get("synthetic"))
    out["available_variants"] = sorted(variants)
    out["batch_integrity"] = batch.get("integrity")
    return out
