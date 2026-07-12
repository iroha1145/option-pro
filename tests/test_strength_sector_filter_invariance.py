from __future__ import annotations

from app.services.strength import scanner


def test_sector_filter_reuses_ranks_from_full_canonical_distribution() -> None:
    canonical = [
        {"ticker": "AAA", "primary_sector_id": "software", "intrinsic_score": 90.0},
        {"ticker": "BBB", "primary_sector_id": "software", "intrinsic_score": 60.0},
        {"ticker": "CCC", "primary_sector_id": "energy", "intrinsic_score": 75.0},
    ]
    scanner._attach_canonical_ranks(canonical)
    expected = {
        row["ticker"]: (row["global_rank_percentile"], row["sector_rank_percentile"])
        for row in canonical
    }
    filtered = [row for row in canonical if row["primary_sector_id"] == "software"]
    assert {
        row["ticker"]: (row["global_rank_percentile"], row["sector_rank_percentile"])
        for row in filtered
    } == {ticker: expected[ticker] for ticker in ("AAA", "BBB")}
