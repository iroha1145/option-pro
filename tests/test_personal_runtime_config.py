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
    assert config.ai.model == "claude-haiku-5-5"
    assert config.ai.reasoning == "xhigh"
    assert config.ai.max_concurrency == 4
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
        AIConfig(max_concurrency=5)
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

    assert settings.openai_model == "claude-haiku-5-5"
    assert settings.openai_reasoning == "xhigh"
    assert settings.openai_max_concurrency == 4
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


@pytest.mark.parametrize("source", ["environ", "dotenv"])
def test_retired_strength_settings_do_not_block_startup(tmp_path, monkeypatch, source):
    retired = (
        "FINNHUB_ENRICH_LIMIT", "YAHOO_OPTIONS_ENABLED", "YAHOO_OPTIONS_ENRICH_LIMIT",
        "YAHOO_OPTION_TARGET_DTE", "YAHOO_OPTION_MIN_DTE", "YAHOO_OPTION_MAX_DTE",
        "YAHOO_OPTION_STRIKE_WINDOW_PCT", "YAHOO_OPTIONS_FAILURE_LIMIT",
        "MARKETDATA_OPTIONS_ENRICH_LIMIT", "MARKETDATA_OPTION_DTE",
        "MARKETDATA_OPTION_STRIKE_LIMIT", "MARKETDATA_OPTION_MODE",
    )
    env_file = None
    if source == "environ":
        for key in retired:
            monkeypatch.setenv(key, "retired-invalid-value")
    else:
        env_file = tmp_path / "retired.env"
        env_file.write_text("\n".join(f"{key}=retired-invalid-value" for key in retired))
    settings = Settings(_env_file=env_file, YAHOO_OPTION_MAX_IN_FLIGHT=2)
    assert settings.yahoo_option_max_in_flight == 2
    assert not ({name.lower() for name in retired} & settings.model_dump().keys())


def test_claude_secret_is_independent_and_redacted(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-claude-secret-sentinel")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old-openai-secret-sentinel")
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-claude-secret-sentinel"
    assert settings.openai_api_key.get_secret_value() == "sk-old-openai-secret-sentinel"
    assert "sk-ant-claude-secret-sentinel" not in repr(settings)
    assert "sk-old-openai-secret-sentinel" not in repr(settings)


def test_claude_endpoint_environment_override_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [("claude-haiku-5-5", "max"), ("gpt-5.6-terra", "xhigh")],
)
def test_settings_reject_mixed_model_reasoning(model, reasoning) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_model=model, openai_reasoning=reasoning)


@pytest.mark.parametrize("concurrency", [1, 2, 3, 4])
def test_runtime_settings_accept_claude_parallelism(concurrency) -> None:
    assert Settings(_env_file=None, openai_max_concurrency=concurrency).openai_max_concurrency == concurrency


@pytest.mark.parametrize("concurrency", [0, 5])
def test_runtime_settings_reject_out_of_range_parallelism(concurrency) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_max_concurrency=concurrency)


def test_legacy_runtime_settings_require_single_concurrency() -> None:
    settings = Settings(_env_file=None, openai_model="gpt-5.6-terra", openai_reasoning="max", openai_max_concurrency=1)
    assert settings.openai_max_concurrency == 1
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_model="gpt-5.6-terra", openai_reasoning="max", openai_max_concurrency=4)
