from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from app.services.research_eod_v1.constants import INDUSTRY_MIN_FOR_LAMBDA, PARENT_MIN_FOR_Q
from app.services.research_eod_v1.mathutil import midrank_percentiles, shrink_q


def parent_pool(track: str, values: dict[str, float | None], tracks: dict[str, str]) -> dict[str, float | None]:
    return {sid: values[sid] for sid in values if tracks.get(sid) == track}


def ranked_q(
    values: dict[str, float | None],
    members: Iterable[str],
) -> dict[str, float | None]:
    names = list(members)
    raw = [values.get(name) for name in names]
    ranks = midrank_percentiles([item if item is None else float(item) for item in raw])
    return dict(zip(names, ranks))


def q_star(
    values: dict[str, float | None],
    *,
    industry: dict[str, str | None],
    parent: dict[str, str | None],
    tracks: dict[str, str],
    invert: bool = False,
) -> dict[str, float | None]:
    signed = {k: (None if v is None else (-v if invert else v)) for k, v in values.items()}
    out: dict[str, float | None] = {}
    by_track: dict[str, list[str]] = defaultdict(list)
    by_industry: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_parent: dict[tuple[str, str], list[str]] = defaultdict(list)
    for sid, value in signed.items():
        track = tracks.get(sid, "stock")
        by_track[track].append(sid)
        if industry.get(sid):
            by_industry[(track, industry[sid] or "")].append(sid)
        if parent.get(sid):
            by_parent[(track, parent[sid] or "")].append(sid)
    q_all: dict[str, float | None] = {}
    for track, members in by_track.items():
        q_all.update(ranked_q(signed, members if len(members) >= PARENT_MIN_FOR_Q else members))
        if len(members) < 2:
            for sid in members:
                q_all[sid] = None
    q_ind: dict[str, float | None] = {}
    n_ind: dict[str, int] = {}
    for key, members in by_industry.items():
        ranks = ranked_q(signed, members)
        for sid in members:
            q_ind[sid] = ranks[sid]
            n_ind[sid] = sum(1 for item in members if signed.get(item) is not None)
    for sid in signed:
        track = tracks.get(sid, "stock")
        q_p = q_all.get(sid)
        if q_p is None and parent.get(sid):
            fallback = by_parent.get((track, parent[sid] or ""), [])
            q_p = ranked_q(signed, fallback).get(sid)
        out[sid] = shrink_q(q_ind.get(sid), q_p, n_ind.get(sid, 0) if n_ind.get(sid, 0) >= INDUSTRY_MIN_FOR_LAMBDA else 0)
    return out
