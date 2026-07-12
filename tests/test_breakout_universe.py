from __future__ import annotations

from app.services.breakouts.adapters.universe import ThemeCanonicalUniverseAdapter


def test_theme_membership_is_not_misreported_as_authoritative_primary_sector() -> None:
    universe = ThemeCanonicalUniverseAdapter()
    assert len(universe.memberships("NVDA")) > 1
    assert universe.primary_sector("NVDA") is None
    assert universe.sector_benchmark("NVDA", "Technology") == "XLK"
    assert universe.sector_benchmark("JPM") == "XLF"
    assert universe.sector_benchmark("NVDA") is None
    assert all(not ticker.endswith(".PA") for ticker in universe._ticker_sectors)


def test_provider_sector_mapping_is_exact_and_reviewable() -> None:
    universe = ThemeCanonicalUniverseAdapter()

    assert universe.sector_benchmark("PFE", "Health Technology") == "XLV"
    assert universe.sector_benchmark("FCX", "Non-Energy Minerals") == "XLB"
    assert universe.sector_benchmark("NVDA", "Electronic Technology") == "XLK"
    assert universe.sector_benchmark("NVDA", "Unknown Technology Lab") is None
    assert universe.sector_benchmark("JPM", "Miscellaneous") is None
