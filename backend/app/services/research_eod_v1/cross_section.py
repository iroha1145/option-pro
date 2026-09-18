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


def _finite_members(signed: dict[str, float | None], members: Iterable[str]) -> int:
    return sum(1 for sid in members if signed.get(sid) is not None)


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
    for sid in signed:
        track = tracks.get(sid, "stock")
        by_track[track].append(sid)
        if industry.get(sid):
            by_industry[(track, industry[sid] or "")].append(sid)
        if parent.get(sid):
            by_parent[(track, parent[sid] or "")].append(sid)
    q_track: dict[str, float | None] = {}
    for _track, members in by_track.items():
        if _finite_members(signed, members) < 2:
            for sid in members:
                q_track[sid] = None
        else:
            q_track.update(ranked_q(signed, members))
    q_parent: dict[str, float | None] = {}
    for _key, members in by_parent.items():
        if _finite_members(signed, members) >= PARENT_MIN_FOR_Q:
            q_parent.update(ranked_q(signed, members))
    q_ind: dict[str, float | None] = {}
    n_ind: dict[str, int] = {}
    for _key, members in by_industry.items():
        ranks = ranked_q(signed, members)
        for sid in members:
            q_ind[sid] = ranks[sid]
            n_ind[sid] = _finite_members(signed, members)
    for sid in signed:
        q_p = q_parent.get(sid)
        if q_p is None:
            q_p = q_track.get(sid)
        n_industry = n_ind.get(sid, 0)
        out[sid] = shrink_q(
            q_ind.get(sid),
            q_p,
            n_industry if n_industry >= INDUSTRY_MIN_FOR_LAMBDA else 0,
        )
    return out
