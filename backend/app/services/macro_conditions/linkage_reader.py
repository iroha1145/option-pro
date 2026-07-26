"""Read-only macro fit provider, shared by every consumer of the linkage.

Three surfaces need the same three things: the published macro snapshot, one
macro fit per sector, and the structural composite. Letting each open its own
repository would read the same file once per surface and -- worse -- could read
*different* snapshots if a publication landed in between, so the screener, the
sector radar and the breakout list could disagree about the macro environment
while sitting on one page. One reader per request removes both problems.

Nothing here may create, migrate or refresh anything. A screener scan or a
breakout page load must never trigger a FRED fetch, so the repository is opened
read-only and every failure degrades to "no macro read" instead of raising:
these fields are annotations on somebody else's page, and an unreadable macro
snapshot is not a reason to fail that page.

"No read" is deliberately distinct from "neutral". A caller that cannot get
macro context reports nothing, never 50 -- 50 is a claim about the environment.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .exposures import EXPOSURE_VERSION
from .linkage import (
    UNAVAILABLE,
    MacroFit,
    compute_macro_fit,
    structural_macro_score,
)

#: Why no fit is available, when none is. Reported verbatim so the interface can
#: tell "the macro module is not installed" apart from "nothing published yet".
REASON_MODULE = "macro_module_unavailable"
REASON_SNAPSHOT = "macro_snapshot_unavailable"


@dataclass
class MacroFitReader:
    """One published macro snapshot, plus memoized per-sector fits."""

    available: bool
    reason: Optional[str] = None
    snapshot_date: Optional[str] = None
    scoring_version: Optional[str] = None
    available_at: Optional[str] = None
    structural_score: Optional[float] = None
    factors: tuple[Mapping[str, Any], ...] = ()
    _fits: dict[str, MacroFit] = field(default_factory=dict, repr=False)

    def fit_for(self, sector_id: Optional[str]) -> MacroFit:
        """This sector's fit. Memoized: the profile is the sector's, not the row's."""

        if not self.available:
            return UNAVAILABLE
        key = str(sector_id or "")
        cached = self._fits.get(key)
        if cached is None:
            cached = compute_macro_fit(self.factors, sector_id=sector_id or None)
            self._fits[key] = cached
        return cached

    def provenance(self) -> dict[str, Any]:
        """Which snapshot and which algorithm versions produced these fits."""

        return {
            "exposure_version": EXPOSURE_VERSION,
            "macro_snapshot_date": self.snapshot_date,
            "macro_scoring_version": self.scoring_version,
            "macro_available_at": self.available_at,
        }


def unavailable_reader(reason: str) -> MacroFitReader:
    return MacroFitReader(available=False, reason=reason)


#: The published snapshot is reused between calls while the file is unchanged.
#: A stock drawer opens this reader once per request, and every open used to run
#: three SQLite queries for a snapshot that is republished twice a day.
_CACHE_MAX_AGE_SECONDS = 30.0
_cache_lock = threading.Lock()
_cached: Optional[tuple[Any, float, MacroFitReader]] = None


def _snapshot_fingerprint() -> Optional[tuple[Any, ...]]:
    """Size and mtime of the database and its WAL, or None if unreadable.

    The WAL is included because a publication commits there first: the main
    file's mtime can lag a write that is already visible to readers. None means
    "do not cache" -- a missing database is exactly the state that must be
    re-checked, since the worker may be creating it right now.
    """

    try:
        from app.data_paths import get_data_paths

        path = str(get_data_paths().macro_conditions_db)
    except Exception:
        return None
    parts: list[Any] = []
    for candidate in (path, f"{path}-wal"):
        try:
            stat = os.stat(candidate)
            parts.append((stat.st_size, stat.st_mtime_ns))
        except OSError:
            # A missing WAL is normal (checkpointed); a missing database is not.
            if candidate == path:
                return None
            parts.append(None)
    return tuple(parts)


def reset_macro_fit_reader_cache() -> None:
    """Drop the memoized snapshot.

    Production does not need to call this -- a publication changes the file's
    size and mtime, which invalidates the entry on its own. It exists so tests
    can start from a known state instead of inheriting another test's snapshot.
    """

    global _cached
    with _cache_lock:
        _cached = None


def load_macro_fit_reader() -> MacroFitReader:
    """Open the published snapshot read-only. Never raises, never fetches.

    Memoized on the database's size and mtime, with a short maximum age as a
    backstop for a write that somehow lands inside one mtime tick. The returned
    reader is shared between callers and must be treated as read-only; its
    ``fit_for`` memoization is a pure derivation of ``factors``, so a race
    between threads costs a duplicate computation and nothing else.
    """

    global _cached
    fingerprint = _snapshot_fingerprint()
    now = time.monotonic()
    if fingerprint is not None:
        with _cache_lock:
            if _cached is not None:
                cached_fingerprint, loaded_at, reader = _cached
                if (
                    cached_fingerprint == fingerprint
                    and now - loaded_at < _CACHE_MAX_AGE_SECONDS
                ):
                    return reader

    reader = _load_macro_fit_reader_uncached()

    # Only a real snapshot is worth holding. Caching "unavailable" would keep
    # answering "no macro read" for 30s after the worker publishes one.
    if fingerprint is not None and reader.available:
        with _cache_lock:
            _cached = (fingerprint, now, reader)
    return reader


def _load_macro_fit_reader_uncached() -> MacroFitReader:
    try:
        from app.data_paths import get_data_paths
        from app.personal_config import get_personal_config

        from .repository import MacroRepository
        from .service import MacroConditionsService, MacroServiceConfig
    except Exception:
        return unavailable_reader(REASON_MODULE)

    try:
        # read_only: constructing this must not create or migrate the file.
        repository = MacroRepository(
            get_data_paths().macro_conditions_db, read_only=True
        )
        service = MacroConditionsService(
            repository,
            config=MacroServiceConfig.from_personal_config(get_personal_config()),
        )
        inputs = service.linkage_inputs()
    except Exception:
        inputs = None
    if not inputs:
        return unavailable_reader(REASON_SNAPSHOT)

    return MacroFitReader(
        available=True,
        snapshot_date=inputs.get("snapshot_date"),
        scoring_version=inputs.get("scoring_version"),
        available_at=inputs.get("available_at"),
        structural_score=structural_macro_score(inputs.get("modules") or []),
        factors=tuple(inputs.get("factors") or ()),
    )


__all__ = [
    "REASON_MODULE",
    "REASON_SNAPSHOT",
    "MacroFitReader",
    "load_macro_fit_reader",
    "reset_macro_fit_reader_cache",
    "unavailable_reader",
]
