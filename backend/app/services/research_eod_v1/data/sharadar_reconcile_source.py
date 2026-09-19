"""Read the already-captured comparison bars. No new Yahoo fetch, no fallback source."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.data.sharadar_identity import resolve_identity_for_session
from app.services.research_eod_v1.data.sharadar_schema import ALLOWED_END, FULL_HISTORY_START
from app.services.research_eod_v1.mathutil import finite
from app.services.research_eod_v1.paths import RESEARCH_ROOT

# Caches captured by earlier rounds. Reading them is not a live Yahoo request.
CACHE_CANDIDATES = (
    RESEARCH_ROOT / "return_pack" / "yahoo_current_universe" / "daily_bars.parquet",
    RESEARCH_ROOT / "data" / "cache" / "offline_replay" / "daily_bars.parquet",
)

RETURN_BASIS = "total_return_index_tri_vs_sharadar_closeadj"
VOLUME_BASIS = "tape_volume_from_split_volume_and_raw_close_ratio"


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _load_parquet(path: Path) -> list[dict[str, Any]]:
    import pandas as pd

    frame = pd.read_parquet(path)
    return [
        {key: (None if value != value else value) for key, value in row.items()}  # noqa: PLR0124
        for row in frame.to_dict(orient="records")
    ]


def available_cache_paths(paths: Sequence[Path] | None = None) -> list[Path]:
    return [path for path in (paths or CACHE_CANDIDATES) if path.is_file()]


def load_reconcile_rows(
    *,
    identities_by_ticker: Mapping[str, Any] | None = None,
    paths: Sequence[Path] | None = None,
    start: date = FULL_HISTORY_START,
    end: date = ALLOWED_END,
) -> dict[str, Any]:
    """Align the cache onto permanent security ids and a declared adjustment basis.

    Rows that cannot be mapped to a Sharadar identity are reported, never guessed
    onto a same-named security.
    """

    found = available_cache_paths(paths)
    if not found:
        return {
            "available": False,
            "status": "RECONCILIATION_MISSING",
            "rows": [],
            "source": {
                "kind": "captured_bar_cache",
                "available": False,
                "searched": [str(path) for path in (paths or CACHE_CANDIDATES)],
                "reason": "no_captured_comparison_cache_present",
                "live_yahoo_request": False,
            },
        }
    by_ticker = dict(identities_by_ticker or {})
    rows: list[dict[str, Any]] = []
    unmapped: set[str] = set()
    outside_window = 0
    per_path: list[dict[str, Any]] = []
    for path in found:
        raw = _load_parquet(path)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in raw:
            token = str(item.get("security_id") or item.get("symbol") or "").strip()
            if not token:
                continue
            grouped.setdefault(token, []).append(item)
        mapped_here = 0
        for token, items in grouped.items():
            candidates = by_ticker.get(token) or by_ticker.get(token.upper()) or []
            if not isinstance(candidates, (list, tuple)):
                candidates = [candidates]
            if not candidates:
                unmapped.add(token)
                continue
            items.sort(key=lambda entry: str(entry.get("session_date") or ""))
            prev_tri: float | None = None
            for entry in items:
                session = _parse_date(entry.get("session_date") or entry.get("date"))
                if session is None:
                    continue
                if session < start or session > end:
                    outside_window += 1
                    prev_tri = finite(entry.get("tri"))
                    continue
                identity = resolve_identity_for_session(list(candidates), session)
                if identity is None:
                    unmapped.add(f"{token}@{session.isoformat()}")
                    prev_tri = finite(entry.get("tri"))
                    continue
                tri = finite(entry.get("tri"))
                ret = None
                if tri is not None and prev_tri not in (None, 0):
                    ret = tri / float(prev_tri) - 1.0
                prev_tri = tri if tri is not None else prev_tri
                rows.append({
                    "security_id": identity.security_id,
                    "session_date": session.isoformat(),
                    "return": ret,
                    "volume": _tape_volume(entry),
                    "return_basis": RETURN_BASIS,
                    "volume_basis": VOLUME_BASIS,
                })
                mapped_here += 1
        per_path.append({"path": str(path), "rows": len(raw), "mapped_rows": mapped_here})
    return {
        "available": True,
        "status": "READ_OK" if rows else "INSUFFICIENT",
        "rows": rows,
        "source": {
            "kind": "captured_bar_cache",
            "available": True,
            "paths": per_path,
            "identity_alignment": "cache_ticker_to_sharadar_permaticker",
            "unmapped_cache_ids": sorted(unmapped),
            "unmapped_n": len(unmapped),
            "rows_outside_allowed_window": outside_window,
            "return_basis": RETURN_BASIS,
            "volume_basis": VOLUME_BASIS,
            "yahoo_is_not_truth": True,
            "live_yahoo_request": False,
        },
    }


def _tape_volume(entry: Mapping[str, Any]) -> float | None:
    volume = finite(entry.get("volume"))
    if volume is None:
        return None
    close = finite(entry.get("close"))
    raw_close = finite(entry.get("raw_close"))
    if close is None or raw_close in (None, 0):
        return volume
    return volume * close / float(raw_close)
