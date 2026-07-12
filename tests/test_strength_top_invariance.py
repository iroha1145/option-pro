from __future__ import annotations

from app.services.strength import scanner


def test_top_slice_does_not_recompute_intrinsic_or_global_percentile() -> None:
    canonical = [
        {"ticker": "AAA", "primary_sector_id": "software", "intrinsic_score": 90.0},
        {"ticker": "BBB", "primary_sector_id": "software", "intrinsic_score": 70.0},
        {"ticker": "CCC", "primary_sector_id": "energy", "intrinsic_score": 50.0},
    ]
    scanner._attach_canonical_ranks(canonical)
    ranked = sorted(canonical, key=lambda row: row["intrinsic_score"], reverse=True)
    top_one = ranked[:1][0]
    top_three = next(row for row in ranked[:3] if row["ticker"] == top_one["ticker"])
    assert top_one["intrinsic_score"] == top_three["intrinsic_score"]
    assert top_one["global_rank_percentile"] == top_three["global_rank_percentile"]
