from __future__ import annotations

import pytest

from app.services.breakouts.config import BreakoutSettings


def test_enabled_range_persistence_requires_matching_validation_version() -> None:
    with pytest.raises(ValueError, match="RANGE_PERSISTENCE_VALIDATION_VERSION"):
        BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="enabled",
            RANGE_PERSISTENCE_VERSION="range-persistence-v2",
            RANGE_PERSISTENCE_VALIDATION_VERSION="",
        )

    with pytest.raises(ValueError, match="RANGE_PERSISTENCE_VALIDATION_VERSION"):
        BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="enabled",
            RANGE_PERSISTENCE_VERSION="range-persistence-v2",
            RANGE_PERSISTENCE_VALIDATION_VERSION="range-persistence-v1",
        )


def test_enabled_range_persistence_accepts_matching_validation_version() -> None:
    settings = BreakoutSettings(
        _env_file=None,
        RANGE_PERSISTENCE_MODE="enabled",
        RANGE_PERSISTENCE_VERSION="range-persistence-v2",
        RANGE_PERSISTENCE_VALIDATION_VERSION="range-persistence-v2",
    )

    assert settings.range_persistence_mode == "enabled"
    assert settings.range_persistence_validation_version == "range-persistence-v2"


@pytest.mark.parametrize("mode", ["shadow", "disabled"])
def test_non_production_range_modes_do_not_require_validation_version(mode: str) -> None:
    settings = BreakoutSettings(
        _env_file=None,
        RANGE_PERSISTENCE_MODE=mode,
        RANGE_PERSISTENCE_VALIDATION_VERSION="",
    )

    assert settings.range_persistence_mode == mode
    assert settings.range_persistence_validation_version == ""
