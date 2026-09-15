from __future__ import annotations

import pytest

from app.services.algorithm_modes import (
    A0_ALGORITHM,
    FOLLOW_DEFAULT,
    INCOMPATIBLE_VIEW,
    PRODUCTION_ALGORITHM,
    T1_ALGORITHM,
    ConflictingAlgorithmError,
    UnknownAlgorithmError,
    admin_algorithm_defaults,
    resolve_radar_algorithm,
    resolve_screener_algorithm,
)


def test_unspecified_request_uses_system_default_production() -> None:
    resolution = resolve_screener_algorithm()
    assert resolution.effective == PRODUCTION_ALGORITHM
    assert resolution.source == "system_default"
    assert resolution.fallback_reason is None


def test_explicit_production_is_not_overwritten_by_admin_a0() -> None:
    resolution = resolve_screener_algorithm(
        requested=PRODUCTION_ALGORITHM,
        admin_default=A0_ALGORITHM,
        explicit_request=True,
    )
    assert resolution.effective == PRODUCTION_ALGORITHM
    assert resolution.source == "request"


def test_follow_default_uses_admin_screener_default() -> None:
    resolution = resolve_screener_algorithm(
        requested=FOLLOW_DEFAULT,
        admin_default=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == A0_ALGORITHM
    assert resolution.source == "admin_default"


def test_explicit_follow_default_skips_saved_user_production() -> None:
    resolution = resolve_screener_algorithm(
        requested=FOLLOW_DEFAULT,
        user_choice=PRODUCTION_ALGORITHM,
        admin_default=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == A0_ALGORITHM
    assert resolution.source == "admin_default"
    assert resolution.requested == FOLLOW_DEFAULT


def test_omitted_request_still_uses_saved_user_production() -> None:
    resolution = resolve_screener_algorithm(
        user_choice=PRODUCTION_ALGORITHM,
        admin_default=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == PRODUCTION_ALGORITHM
    assert resolution.source == "user_preference"


def test_explicit_follow_default_skips_saved_user_radar() -> None:
    radar = resolve_radar_algorithm(
        requested=FOLLOW_DEFAULT,
        user_choice=PRODUCTION_ALGORITHM,
        admin_default=T1_ALGORITHM,
    )
    assert radar.effective == T1_ALGORITHM
    assert radar.source == "admin_default"


def test_saved_user_production_survives_admin_default_change() -> None:
    resolution = resolve_screener_algorithm(
        user_choice=PRODUCTION_ALGORITHM,
        admin_default=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == PRODUCTION_ALGORITHM
    assert resolution.source == "user_preference"


def test_explicit_a0_with_incompatible_view_raises() -> None:
    with pytest.raises(ConflictingAlgorithmError):
        resolve_screener_algorithm(
            requested=A0_ALGORITHM,
            timeframe="mid",
            profile="balanced",
            explicit_request=True,
        )


def test_admin_a0_with_incompatible_old_client_falls_back() -> None:
    resolution = resolve_screener_algorithm(
        admin_default=A0_ALGORITHM,
        timeframe="short",
        profile="aggressive",
    )
    assert resolution.effective == PRODUCTION_ALGORITHM
    assert resolution.fallback_reason == INCOMPATIBLE_VIEW


def test_unknown_screener_algorithm_is_rejected() -> None:
    with pytest.raises(UnknownAlgorithmError):
        resolve_screener_algorithm(requested="c0_sector_cap")


def test_radar_and_screener_defaults_are_independent() -> None:
    defaults = admin_algorithm_defaults(
        {
            "algorithms": {
                "screener_ranking_algorithm": A0_ALGORITHM,
                "radar_sort_algorithm": PRODUCTION_ALGORITHM,
            }
        }
    )
    assert defaults["screener_ranking_algorithm"] == A0_ALGORITHM
    assert defaults["radar_sort_algorithm"] == PRODUCTION_ALGORITHM
    radar = resolve_radar_algorithm(admin_default=defaults["radar_sort_algorithm"])
    assert radar.effective == PRODUCTION_ALGORITHM


def test_explicit_t1_request_is_independent_of_screener_default() -> None:
    radar = resolve_radar_algorithm(requested=T1_ALGORITHM, admin_default=PRODUCTION_ALGORITHM)
    assert radar.effective == T1_ALGORITHM
    assert radar.version == "t1-daily-priority-v1"
