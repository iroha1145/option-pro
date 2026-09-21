"""Bounded per-security platform geometry, without cross-sectional scoring."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from itertools import islice
import multiprocessing
from typing import Any, Iterable, Iterator

from app.services.research_eod_v1.factors import RawComponents, apply_sector_gates
from app.services.research_eod_v1.series import SecuritySeries

MAX_GEOMETRY_WORKERS = 4
GEOMETRY_BATCH_SIZE = 128
GEOMETRY_CHUNK_SIZE = 8
GeometryTask = tuple[str, RawComponents, SecuritySeries, list[tuple[str, dict[str, Any]]]]
GeometryResult = tuple[str, list[tuple[str, RawComponents]]]


def validate_geometry_workers(workers: int) -> None:
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= MAX_GEOMETRY_WORKERS:
        raise ValueError("geometry_workers must be an integer between 1 and 4")


def _security_geometry(task: GeometryTask) -> GeometryResult:
    """One security stays in one child, sharing its frozen platforms across themes."""
    sid, raw, series, themes = task
    geometry_cache: dict = {}
    return sid, [
        (theme, apply_sector_gates(raw, series, gates, geometry_cache=geometry_cache))
        for theme, gates in themes
    ]


def parallel_geometry(tasks: Iterable[GeometryTask], *, workers: int) -> Iterator[GeometryResult]:
    """Submit at most 128 securities at once; any failure aborts the whole call.

    Spawn is required because the live worker invokes inference from a thread.
    Children receive one security and its applicable gates, never the full panel.
    The executor context waits for its bounded pending batch and joins children
    before an exception leaves this generator.
    """
    validate_geometry_workers(workers)
    pending = iter(tasks)
    batch = list(islice(pending, GEOMETRY_BATCH_SIZE))
    if not batch:
        return
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as executor:
        while batch:
            yield from executor.map(_security_geometry, batch, chunksize=GEOMETRY_CHUNK_SIZE)
            batch = list(islice(pending, GEOMETRY_BATCH_SIZE))
