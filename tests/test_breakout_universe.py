from __future__ import annotations

from app.services.breakouts.adapters.universe import ThemeCanonicalUniverseAdapter


def test_theme_membership_is_not_misreported_as_authoritative_primary_sector() -> None:
    universe = ThemeCanonicalUniverseAdapter()
    assert len(universe.memberships("NVDA")) > 1
    assert universe.primary_sector("NVDA") is None
    assert all(not ticker.endswith(".PA") for ticker in universe._ticker_sectors)
