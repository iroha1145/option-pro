"""Red/green parent-group quantile. Full-track fallback is not the parent rank."""

from __future__ import annotations

from app.services.research_eod_v1.cross_section import q_star
from app.services.research_eod_v1.mathutil import midrank_percentiles, shrink_q


def _two_parent_inputs():
    values = {}
    parent = {}
    industry = {}
    tracks = {}
    for i in range(20):
        sid = f"A{i:02d}"
        values[sid] = float(i)
        parent[sid] = "P1"
        industry[sid] = None
        tracks[sid] = "stock"
    for i in range(20):
        sid = f"B{i:02d}"
        values[sid] = float(i + 10)
        parent[sid] = "P2"
        industry[sid] = None
        tracks[sid] = "stock"
    return values, parent, industry, tracks


def test_parent_rank_uses_same_track_parent_not_full_pool() -> None:
    values, parent, industry, tracks = _two_parent_inputs()
    ranks = q_star(values, industry=industry, parent=parent, tracks=tracks)
    assert ranks["A10"] == 52.6315789474
    assert ranks["B00"] == 0.0
    full = midrank_percentiles(list(values.values()))
    assert full[list(values).index("A10")] == 26.9230769231
    assert full[list(values).index("B00")] == 26.9230769231
    assert ranks["A10"] != ranks["B00"]


def test_parent_rank_falls_back_when_finite_members_below_20() -> None:
    values, parent, industry, tracks = _two_parent_inputs()
    values["A19"] = None
    ranks = q_star(values, industry=industry, parent=parent, tracks=tracks)
    fallback = q_star(values, industry=industry, parent={sid: None for sid in values}, tracks=tracks)
    assert ranks["A10"] == fallback["A10"]
    assert ranks["A10"] != 52.6315789474
    assert ranks["B00"] == 0.0


def test_unknown_parent_and_etf_isolation() -> None:
    values, parent, industry, tracks = _two_parent_inputs()
    orphan = dict(parent)
    orphan["A10"] = None
    ranks = q_star(values, industry=industry, parent=orphan, tracks=tracks)
    assert ranks["A10"] == 26.9230769231
    etf_values = {f"E{i:02d}": float(i) for i in range(20)}
    etf_parent = {sid: "P1" for sid in etf_values}
    etf_tracks = {sid: "etf" for sid in etf_values}
    mixed_values = {**values, **etf_values}
    mixed_parent = {**parent, **etf_parent}
    mixed_tracks = {**tracks, **etf_tracks}
    mixed_industry = {sid: None for sid in mixed_values}
    mixed = q_star(mixed_values, industry=mixed_industry, parent=mixed_parent, tracks=mixed_tracks)
    assert mixed["E10"] == 52.6315789474
    assert mixed["A10"] == 52.6315789474


def test_invert_and_industry_shrink_toward_parent() -> None:
    values, parent, industry, tracks = _two_parent_inputs()
    inverted = q_star(values, industry=industry, parent=parent, tracks=tracks, invert=True)
    assert inverted["A00"] == 100.0
    assert inverted["A19"] == 0.0
    for sid in values:
        if sid.startswith("A"):
            industry[sid] = "I1" if int(sid[1:]) < 5 else "IA"
        else:
            industry[sid] = "IB"
    shrunk = q_star(values, industry=industry, parent=parent, tracks=tracks)
    parent_rank = 52.6315789474
    industry_rank = midrank_percentiles([float(i) for i in range(5, 20)])[5]
    expected = shrink_q(industry_rank, parent_rank, 15)
    assert shrunk["A10"] == expected
    assert abs(shrunk["A10"] - parent_rank) < abs(shrunk["A10"] - 26.9230769231)
