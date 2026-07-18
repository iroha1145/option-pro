from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.personal_config import (
    HOURLY_ANALYSIS_TIMES_ET,
    AIConfig,
    PersonalConfig,
    load_personal_config,
)
from app.services.breakouts.config import BreakoutSettings


ROOT = Path(__file__).resolve().parents[1]


def test_personal_runtime_loads_the_committed_toml() -> None:
    config = load_personal_config(ROOT / "config" / "personal.toml")

    assert config.features.breakout_enabled is True
    assert config.features.catalyst_mode == "scheduled"
    assert config.ai.model == "gpt-5.6-terra"
    assert config.ai.reasoning == "max"
    assert config.ai.max_concurrency == 1
    assert config.ai.daily_max_jobs == 0
    assert config.ai.daily_budget_usd == 0.0
    assert config.ai.daily_token_limit == 10_000_000
    assert config.ai.execution_mode == "background"
    assert config.catalyst.sync_seconds == 120
    assert config.catalyst.focus_seconds == 1800
    assert config.catalyst.scheduled_times_et == list(HOURLY_ANALYSIS_TIMES_ET)
    assert config.catalyst.manual_force_reanalysis is True
    assert config.catalyst.manual_refresh_cooldown_seconds == 30
    assert config.storage.retention_days == 90


def test_personal_ai_uses_a_daily_token_safety_limit() -> None:
    assert AIConfig().daily_max_jobs == 0
    assert AIConfig().daily_budget_usd == 0
    assert AIConfig().daily_token_limit == 10_000_000
    with pytest.raises(ValidationError):
        AIConfig(model="legacy-model")
    with pytest.raises(ValidationError):
        AIConfig(reasoning="high")
    with pytest.raises(ValidationError):
        AIConfig(max_concurrency=2)
    with pytest.raises(ValidationError):
        AIConfig(daily_token_limit=102_399)


def test_force_reanalysis_is_fixed_on_in_personal_configuration() -> None:
    with pytest.raises(ValidationError):
        PersonalConfig.model_validate(
            {"catalyst": {"manual_force_reanalysis": False}}
        )


def test_personal_configuration_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PersonalConfig.model_validate({"features": {}, "unknown": True})


def test_environment_template_does_not_duplicate_behavior_configuration() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    forbidden = (
        "OPENAI_MODEL=",
        "OPENAI_REASONING=",
        "OPENAI_MAX_CONCURRENCY=",
        "OPENAI_DAILY_MAX_JOBS=",
        "BREAKOUT_RADAR_ENABLED=",
        "CATALYST_MODE=",
        "MACROLENS_ENABLED=",
        "FOCUS_PRODUCER_ENABLED=",
        "APP_AUTH_TOKEN=",
        "PUBLIC_READ_API_ENABLED=",
        "MACROLENS_ACTION_SECRET=",
        "OPTIX_WORKER_DB_PATH=",
        "OPTION_PRO_RUNTIME_SETTINGS_PATH=",
    )
    assert all(item not in example for item in forbidden)


def test_legacy_environment_cannot_override_personal_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_values = {
        "OPENAI_MODEL": "legacy-model",
        "OPENAI_REASONING": "low",
        "OPENAI_MAX_CONCURRENCY": "8",
        "OPENAI_DAILY_MAX_JOBS": "2",
        "OPENAI_DAILY_BUDGET_USD": "9",
        "OPENAI_MANUAL_COOLDOWN_SECONDS": "900",
        "OPENAI_EXECUTION_MODE": "worker_sync",
        "BREAKOUT_RADAR_ENABLED": "false",
        "BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS": "61",
        "BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS": "62",
        "BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS": "301",
        "BREAKOUT_SCAN_RETENTION_DAYS": "1",
        "RANGE_PERSISTENCE_MODE": "disabled",
    }
    for key, value in legacy_values.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)
    breakout = BreakoutSettings(_env_file=None)

    assert settings.openai_model == "gpt-5.6-terra"
    assert settings.openai_reasoning == "max"
    assert settings.openai_max_concurrency == 1
    assert settings.openai_daily_max_jobs == 0
    assert settings.openai_daily_budget_usd == 0.0
    assert settings.openai_daily_token_limit == 10_000_000
    assert settings.openai_manual_cooldown_seconds == 30
    assert settings.openai_execution_mode == "background"
    assert breakout.enabled is True
    assert breakout.scan_interval_premarket_seconds == 600
    assert breakout.scan_interval_regular_seconds == 300
    assert breakout.scan_interval_closed_seconds == 1800
    assert breakout.scan_retention_days == 90
    assert breakout.range_persistence_mode == "shadow"
