"""Read the already-captured comparison bars. No new Yahoo fetch, no fallback source.

Accepted caches:

* a parquet file with one row per (security, session) carrying tri / close /
  raw_close / volume, as written by earlier research rounds;
* the project's trusted Yahoo pickle cache (``round3_yahoo_abcd/bars.pkl``),
  read through the same allow-list the readiness audit uses.

Extra paths can be supplied by the caller or via the environment variable
``RESEARCH_EOD_RECONCILE_CACHE`` (path separator ``os.pathsep``).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.data.sharadar_identity import (
    resolve_identity_for_session,
    split_ticker_suffix,
)
from app.services.research_eod_v1.data.sharadar_schema import ALLOWED_END, FULL_HISTORY_START
from app.services.research_eod_v1.mathutil import finite
from app.services.research_eod_v1.paths import RESEARCH_ROOT

RECONCILE_CACHE_ENV = "RESEARCH_EOD_RECONCILE_CACHE"

# Caches captured by earlier rounds. Reading them is not a live Yahoo request.
CACHE_CANDIDATES = (
    RESEARCH_ROOT / "return_pack" / "yahoo_current_universe" / "daily_bars.parquet",
    RESEARCH_ROOT / "data" / "cache" / "offline_replay" / "daily_bars.parquet",
    RESEARCH_ROOT / "data" / "cache" / "round3_yahoo_abcd" / "bars.pkl",
)

RETURN_BASIS = "total_return_index_tri_vs_sharadar_closeadj"
VOLUME_BASIS = "tape_volume_from_split_volume_and_raw_close_ratio"
_TOKEN_PREFIXES = ("ticker:", "yahoo:", "symbol:")


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


def _load_pickle(path: Path) -> list[dict[str, Any]]:
    from app.services.research_eod_v1.data_readiness import load_trusted_research_bars

    payload = load_trusted_research_bars(path)
    rows: list[dict[str, Any]] = []
    for symbol, bars in payload.items():
        for bar in bars:
            rows.append({
                "security_id": symbol,
                "session_date": bar.session_date,
                "tri": bar.tri,
                "close": bar.close,
                "raw_close": bar.raw_close,
                "volume": bar.volume,
            })
    return rows


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".pkl", ".pickle"}:
        return _load_pickle(path)
    return _load_parquet(path)


def env_cache_paths() -> list[Path]:
    raw = os.environ.get(RECONCILE_CACHE_ENV, "")
    return [Path(item) for item in raw.split(os.pathsep) if item.strip()]


def available_cache_paths(paths: Sequence[Path] | None = None) -> list[Path]:
    candidates = list(paths) if paths is not None else [*env_cache_paths(), *CACHE_CANDIDATES]
    return [path for path in candidates if path.is_file()]


def _normalise_token(token: str) -> str:
    text = str(token or "").strip()
    lowered = text.lower()
    for prefix in _TOKEN_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.strip().upper()


def load_reconcile_rows(
    *,
    identities_by_ticker: Mapping[str, Any] | None = None,
    paths: Sequence[Path] | None = None,
    start: date = FULL_HISTORY_START,
    end: date = ALLOWED_END,
) -> dict[str, Any]:
    """Align the cache onto permanent security ids and a declared adjustment basis.

    Rows that cannot be mapped to a Sharadar identity are reported, never guessed
    onto a same-named security. ``identities_by_ticker`` may be keyed by ticker
    or by ticker base (the suffix-free form).
    """

    found = available_cache_paths(paths)
    searched = [str(path) for path in (paths if paths is not None else [*env_cache_paths(), *CACHE_CANDIDATES])]
    if not found:
        return {
            "available": False,
            "status": "RECONCILIATION_MISSING",
            "rows": [],
            "source": {
                "kind": "captured_bar_cache",
                "available": False,
                "searched": searched,
                "reason": "no_captured_comparison_cache_present",
                "live_yahoo_request": False,
            },
        }
    by_ticker = {str(key).upper(): value for key, value in dict(identities_by_ticker or {}).items()}
    rows: list[dict[str, Any]] = []
    unmapped: set[str] = set()
    outside_window = 0
    per_path: list[dict[str, Any]] = []
    for path in found:
        try:
            raw = _load_rows(path)
        except Exception as exc:  # unreadable cache is reported, not masked
            per_path.append({"path": str(path), "rows": 0, "mapped_rows": 0, "error": type(exc).__name__})
            continue
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in raw:
            token = _normalise_token(str(item.get("security_id") or item.get("symbol") or ""))
            if not token:
                continue
            grouped.setdefault(token, []).append(item)
        mapped_here = 0
        for token, items in grouped.items():
            base, _suffix = split_ticker_suffix(token)
            candidates = by_ticker.get(token) or by_ticker.get(base) or []
            if not isinstance(candidates, (list, tuple)):
                candidates = [candidates]
            if not candidates:
                unmapped.add(token)
                continue
            items.sort(key=lambda entry: str(entry.get("session_date") or entry.get("date") or ""))
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
            "unmapped_cache_ids": sorted(unmapped)[:200],
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
