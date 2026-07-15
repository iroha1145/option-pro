from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.personal_config import AIConfig, PersonalConfig, load_personal_config


ROOT = Path(__file__).resolve().parents[1]


def test_personal_runtime_loads_the_committed_toml() -> None:
    config = load_personal_config(ROOT / "config" / "personal.toml")

    assert config.features.breakout_enabled is True
    assert config.features.catalyst_mode == "manual"
    assert config.ai.model == "gpt-5.6-terra"
    assert config.ai.reasoning == "max"
    assert config.ai.max_concurrency == 1
    assert config.ai.daily_max_jobs == 4
    assert config.ai.daily_budget_usd == 2.0
    assert config.ai.execution_mode == "background"
    assert config.catalyst.sync_seconds == 120
    assert config.catalyst.focus_seconds == 1800
    assert config.catalyst.scheduled_times_et == ["08:00", "12:00", "16:00"]
    assert config.catalyst.manual_force_reanalysis is True
    assert config.catalyst.manual_refresh_cooldown_seconds == 30
    assert config.storage.retention_days == 90


def test_personal_ai_limits_are_fixed() -> None:
    assert AIConfig().daily_max_jobs == 4
    with pytest.raises(ValidationError):
        AIConfig(model="legacy-model")
    with pytest.raises(ValidationError):
        AIConfig(reasoning="high")
    with pytest.raises(ValidationError):
        AIConfig(max_concurrency=2)
    with pytest.raises(ValidationError):
        AIConfig(daily_max_jobs=5)


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
