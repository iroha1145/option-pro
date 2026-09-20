from __future__ import annotations

import pytest

from app.services.algorithm_modes import (
    A0_ALGORITHM,
    DEFAULT_SCREENER_ALGORITHM,
    EOD_DEFAULT_TIMEFRAME,
    EOD_LIMITED_V1,
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


def test_unspecified_request_uses_system_default_eod() -> None:
    resolution = resolve_screener_algorithm()
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.admin_default == DEFAULT_SCREENER_ALGORITHM
    assert resolution.source == "system_default"
    assert resolution.fallback_reason is None
    assert resolution.resolved_timeframe == EOD_DEFAULT_TIMEFRAME


def test_explicit_production_is_not_overwritten_by_admin_a0() -> None:
    resolution = resolve_screener_algorithm(
        requested=PRODUCTION_ALGORITHM,
        admin_default=A0_ALGORITHM,
        explicit_request=True,
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.source == "request"


def test_follow_default_uses_admin_screener_default() -> None:
    resolution = resolve_screener_algorithm(
        requested=FOLLOW_DEFAULT,
        admin_default=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.source == "admin_default"


def test_explicit_follow_default_skips_saved_user_production() -> None:
    resolution = resolve_screener_algorithm(
        requested=FOLLOW_DEFAULT,
        user_choice=PRODUCTION_ALGORITHM,
        admin_default=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.source == "admin_default"
    assert resolution.requested == FOLLOW_DEFAULT


def test_omitted_request_still_uses_saved_user_production() -> None:
    resolution = resolve_screener_algorithm(
        user_choice=PRODUCTION_ALGORITHM,
        admin_default=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == EOD_LIMITED_V1
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
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.source == "user_preference"


def test_explicit_a0_uses_replacement_engine_for_mid_view() -> None:
    resolution = resolve_screener_algorithm(
        requested=A0_ALGORITHM, timeframe="mid", profile="balanced", explicit_request=True,
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.resolved_timeframe == "mid"


def test_admin_a0_with_incompatible_old_client_falls_back() -> None:
    resolution = resolve_screener_algorithm(
        admin_default=A0_ALGORITHM,
        timeframe="short",
        profile="aggressive",
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.fallback_reason is None
    assert resolution.resolved_timeframe == "short"


def test_explicit_eod_with_all_timeframe_uses_mid() -> None:
    resolution = resolve_screener_algorithm(
        requested=EOD_LIMITED_V1, timeframe="all", profile="balanced", explicit_request=True,
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.resolved_timeframe == "mid"


def test_follow_default_production_executes_replacement_engine() -> None:
    resolution = resolve_screener_algorithm(
        requested=FOLLOW_DEFAULT,
        admin_default=PRODUCTION_ALGORITHM,
        timeframe="mid",
        profile="balanced",
    )
    assert resolution.effective == EOD_LIMITED_V1


def test_implicit_eod_all_timeframe_remaps_to_mid() -> None:
    resolution = resolve_screener_algorithm(
        admin_default=EOD_LIMITED_V1,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.resolved_timeframe == EOD_DEFAULT_TIMEFRAME
    assert resolution.fallback_reason is None


def test_follow_default_all_timeframe_remaps_to_mid() -> None:
    resolution = resolve_screener_algorithm(
        requested=FOLLOW_DEFAULT,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.source == "system_default"
    assert resolution.resolved_timeframe == EOD_DEFAULT_TIMEFRAME


def test_omitted_timeframe_uses_eod_mid() -> None:
    resolution = resolve_screener_algorithm(timeframe=None, timeframe_omitted=True)
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.resolved_timeframe == EOD_DEFAULT_TIMEFRAME


def test_explicit_eod_mid_resolves() -> None:
    resolution = resolve_screener_algorithm(
        requested=EOD_LIMITED_V1,
        timeframe="mid",
        profile="balanced",
        explicit_request=True,
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.version == "eod-limited-v1.1"
    assert resolution.resolved_timeframe == "mid"


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


def test_unspecified_admin_defaults_keep_public_production_name() -> None:
    defaults = admin_algorithm_defaults(None)
    assert defaults["screener_ranking_algorithm"] == PRODUCTION_ALGORITHM
    assert defaults["radar_sort_algorithm"] == PRODUCTION_ALGORITHM


def test_explicit_t1_request_is_independent_of_screener_default() -> None:
    radar = resolve_radar_algorithm(requested=T1_ALGORITHM, admin_default=PRODUCTION_ALGORITHM)
    assert radar.effective == T1_ALGORITHM
    assert radar.version == "t1-daily-priority-v1"
