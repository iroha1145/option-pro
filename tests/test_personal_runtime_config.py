from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.personal_config import AIConfig
from app.services.breakouts import config as breakout_config
from app.services.breakouts.config import BreakoutSettings
from app.services.catalysts.config import CatalystSettings
from app.services.catalysts.focus_config import FocusContextSettings


READ_SECRET = "read-secret-0123456789abcdef-0001"


def test_personal_ai_daily_limit_cannot_exceed_four() -> None:
    assert AIConfig(daily_max_jobs=4).daily_max_jobs == 4
    with pytest.raises(ValidationError):
        AIConfig(daily_max_jobs=5)


def test_personal_toml_supplies_runtime_defaults(monkeypatch) -> None:
    for name in (
        "OPENAI_MODEL",
        "OPENAI_REASONING",
        "OPENAI_MAX_CONCURRENCY",
        "OPENAI_DAILY_MAX_JOBS",
        "OPENAI_EXECUTION_MODE",
        "BREAKOUT_RADAR_ENABLED",
        "BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS",
        "BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS",
        "BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS",
        "BREAKOUT_SCAN_RETENTION_DAYS",
        "RANGE_PERSISTENCE_MODE",
        "MACROLENS_ENABLED",
        "CATALYST_MODE",
        "MACROLENS_FEED_INTERVAL_SECONDS",
        "FOCUS_CONTEXT_REFRESH_SECONDS",
        "FOCUS_PRODUCER_INTERVAL_SECONDS",
        "FOCUS_DAILY_STRENGTH_RETENTION_DAYS",
        "FOCUS_SNAPSHOT_RETENTION_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)
    assert settings.openai_model == "gpt-5.6-terra"
    assert settings.openai_reasoning == "max"
    assert settings.openai_max_concurrency == 1
    assert settings.openai_daily_max_jobs == 4
    assert settings.openai_daily_budget_usd == 2.0
    assert settings.openai_execution_mode == "background"

    breakout = BreakoutSettings(_env_file=None)
    assert breakout.enabled is True
    assert breakout.scan_interval_regular_seconds == 300
    assert breakout.scan_interval_premarket_seconds == 600
    assert breakout.scan_interval_closed_seconds == 1800
    assert breakout.scan_retention_days == 90
    assert breakout.range_persistence_mode == "shadow"

    catalyst = CatalystSettings(
        _env_file=None,
        MACROLENS_BASE_URL="http://localhost:9876",
        MACROLENS_ALLOW_LOCAL_HTTP=True,
        MACROLENS_READ_KEY_ID="read-key",
        MACROLENS_READ_SECRET=READ_SECRET,
    )
    assert catalyst.enabled is True
    assert catalyst.catalyst_mode == "display"
    assert catalyst.feed_interval_seconds == 120
    assert catalyst.model == "gpt-5.6-terra"
    assert catalyst.reasoning == "max"

    focus = FocusContextSettings(_env_file=None)
    assert focus.refresh_seconds == 1800
    assert focus.producer_interval_seconds == 1800
    assert focus.daily_strength_retention_days == 90
    assert focus.snapshot_retention_days == 90


def test_legacy_environment_cannot_override_fixed_ai_or_disable_breakout(
    monkeypatch,
) -> None:
    overrides = {
        "OPENAI_MODEL": "legacy-model",
        "OPENAI_REASONING": "high",
        "OPENAI_MAX_CONCURRENCY": "2",
        "OPENAI_DAILY_MAX_JOBS": "9",
        "OPENAI_EXECUTION_MODE": "worker_sync",
        "BREAKOUT_RADAR_ENABLED": "false",
        "BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS": "420",
        "BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS": "720",
        "BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS": "2400",
        "BREAKOUT_SCAN_RETENTION_DAYS": "45",
        "RANGE_PERSISTENCE_MODE": "disabled",
        "MACROLENS_ENABLED": "false",
        "CATALYST_MODE": "disabled",
        "MACROLENS_FEED_INTERVAL_SECONDS": "240",
        "FOCUS_CONTEXT_REFRESH_SECONDS": "3600",
        "FOCUS_PRODUCER_INTERVAL_SECONDS": "3600",
        "FOCUS_DAILY_STRENGTH_RETENTION_DAYS": "60",
        "FOCUS_SNAPSHOT_RETENTION_DAYS": "60",
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    for name in (
        "OPENAI_MODEL",
        "OPENAI_REASONING",
        "OPENAI_MAX_CONCURRENCY",
        "OPENAI_DAILY_MAX_JOBS",
        "OPENAI_EXECUTION_MODE",
    ):
        monkeypatch.delenv(name)
    settings = Settings(_env_file=None)
    assert settings.openai_model == "gpt-5.6-terra"
    assert settings.openai_reasoning == "max"
    assert settings.openai_max_concurrency == 1
    assert settings.openai_daily_max_jobs == 4
    assert settings.openai_execution_mode == "background"

    monkeypatch.setattr(breakout_config, "_LEGACY_BREAKOUT_WARNING_EMITTED", False)
    with pytest.warns(DeprecationWarning, match="personal.toml takes precedence"):
        breakout = BreakoutSettings(_env_file=None)
    assert breakout.enabled is True
    assert breakout.scan_interval_regular_seconds == 420
    assert breakout.scan_interval_premarket_seconds == 720
    assert breakout.scan_interval_closed_seconds == 2400
    assert breakout.scan_retention_days == 45
    assert breakout.range_persistence_mode == "disabled"

    catalyst = CatalystSettings(_env_file=None)
    assert catalyst.enabled is False
    assert catalyst.catalyst_mode == "disabled"
    assert catalyst.feed_interval_seconds == 240
    assert catalyst.model == "gpt-5.6-terra"
    assert catalyst.reasoning == "max"

    focus = FocusContextSettings(_env_file=None)
    assert focus.refresh_seconds == 3600
    assert focus.producer_interval_seconds == 3600
    assert focus.daily_strength_retention_days == 60
    assert focus.snapshot_retention_days == 60
