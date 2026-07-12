from __future__ import annotations

from app.services.technical.range_persistence import build_range_persistence_shadow


def test_shadow_mode_never_changes_production_score_or_rank() -> None:
    payload = build_range_persistence_shadow(
        mode="shadow",
        production_score=72.0,
        hypothetical_score=75.5,
        production_rank=10,
        hypothetical_rank=7,
        effective_weight=0.15,
    )
    assert payload["selected_score"] == 72.0
    assert payload["production_unchanged"] is True
    assert payload["score_delta"] == 3.5
    assert payload["rank_delta"] == -3
    assert payload["effective_weight"] == 0.04


def test_enabled_mode_is_explicit_and_weight_remains_capped() -> None:
    payload = build_range_persistence_shadow(
        mode="enabled",
        production_score=72.0,
        hypothetical_score=73.0,
        effective_weight=0.03,
    )
    assert payload["selected_score"] == 73.0
    assert payload["production_unchanged"] is False
    assert payload["effective_weight"] == 0.03


def test_missing_feature_does_not_create_neutral_contribution() -> None:
    payload = build_range_persistence_shadow(
        mode="shadow",
        production_score=64.0,
        hypothetical_score=None,
        effective_weight=0.0,
    )
    assert payload["selected_score"] == 64.0
    assert payload["hypothetical_score"] is None
    assert payload["score_delta"] is None
    assert payload["effective_weight"] == 0.0
