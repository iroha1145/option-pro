"""EOD shadow: one batch fetch when enabled; intraday reads local snapshots only."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.research_eod_v1 import FEATURE_VERSION
from app.services.research_eod_v1.calendar_asof import last_completed_session, require_aware

NETWORK_COUNTER: dict[str, int] = {"batch_downloads": 0, "intraday_provider_calls": 0}


def research_enabled(settings: Any | None = None) -> bool:
    if settings is not None and hasattr(settings, "research_eod_v1_enabled"):
        return bool(settings.research_eod_v1_enabled)
    return os.environ.get("RESEARCH_EOD_V1_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def snapshot_path(root: Path) -> Path:
    return Path(root) / "research-eod-v1-snapshot.json"


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".research-eod-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        return None
    return payload


def cache_key(*, session_date: str, feature_version: str, config_hash: str, universe_version: str) -> str:
    return f"{session_date}|{feature_version}|{config_hash}|{universe_version}"


def publish_snapshot(
    path: Path,
    *,
    session_date: str,
    config_hash: str,
    universe_version: str,
    rows: list[dict[str, Any]],
    integrity: str = "complete",
) -> dict[str, Any]:
    previous = read_snapshot(path)
    payload = {
        "session_date": session_date,
        "feature_version": FEATURE_VERSION,
        "config_hash": config_hash,
        "universe_version": universe_version,
        "cache_key": cache_key(
            session_date=session_date,
            feature_version=FEATURE_VERSION,
            config_hash=config_hash,
            universe_version=universe_version,
        ),
        "integrity": integrity,
        "rows": rows,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "algorithm_family": "research_eod_v1",
        "production_default_unchanged": True,
    }
    try:
        atomic_write_json(path, payload)
        return payload
    except Exception:
        if previous is not None:
            return {**previous, "integrity": "stale_previous_retained", "publish_failed": True}
        raise


def intraday_view(
    snapshot: Mapping[str, Any] | None,
    *,
    sector_id: str | None = None,
    algorithm_id: str | None = None,
    profile: str | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    NETWORK_COUNTER["intraday_provider_calls"] += 0
    if snapshot is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "NO_PUBLISHED_SNAPSHOT",
            "network_calls": 0,
            "rows": [],
        }
    rows = list(snapshot.get("rows") or [])
    if sector_id:
        rows = [row for row in rows if row.get("sector_context") == sector_id]
    if algorithm_id:
        rows = [row for row in rows if row.get("algorithm_id") == algorithm_id]
    if profile:
        rows = [row for row in rows if row.get("profile") == profile]
    eligible = [row for row in rows if row.get("status") == "eligible"]
    eligible.sort(key=lambda r: (-float(r.get("score") or 0), r.get("security_id") or ""))
    capped = eligible[: max(0, int(top_k))]
    return {
        "status": "SHADOW_ONLY",
        "session_date": snapshot.get("session_date"),
        "integrity": snapshot.get("integrity"),
        "cache_key": snapshot.get("cache_key"),
        "eligible_count": len(eligible),
        "cap": top_k,
        "display": f"本次合格 {min(len(eligible), top_k)} / 上限 {top_k}",
        "rows": capped,
        "network_calls": 0,
        "production_default_unchanged": True,
    }


def record_batch_download(count: int = 1) -> None:
    NETWORK_COUNTER["batch_downloads"] += count
