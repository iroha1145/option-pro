from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.personal_config import (
    AIConfig,
    AccessConfig,
    MarketBriefConfig,
    load_personal_config,
)


def test_repository_personal_config_freezes_paid_runtime() -> None:
    config = load_personal_config()

    assert config.ai.model == "claude-haiku-5-5"
    assert config.ai.reasoning == "xhigh"
    assert config.ai.max_concurrency == 4
    assert config.ai.daily_max_jobs == 0
    assert config.ai.daily_budget_usd == 0.0
    assert config.ai.daily_token_limit == 10_000_000
    assert config.ai.execution_mode == "background"
    assert config.access.mode == "private_network"
    assert config.public_home.poll_seconds == 30
    assert config.public_home.watchlist_seconds == 1800
    assert config.public_home.indices_seconds == 300
    assert config.public_home.overview_seconds == 300
    assert config.public_home.chart_seconds == 300
    assert config.public_home.signals_seconds == 900
    assert config.public_home.earnings_seconds == 21_600
    assert config.public_home.unusual_seconds == 1800


def test_repository_market_brief_section_pins_model_and_maps_to_runtime_types() -> None:
    config = load_personal_config().market_brief

    assert config == MarketBriefConfig()
    assert config.model == "claude-opus-5-5"
    assert config.effort == "xhigh"
    assert config.daily_max_runs == 6

    # 映射是逐字段同名的：两侧任何一边改名，这里都会失败。
    run_config = config.to_run_config()
    for name in (
        "model",
        "effort",
        "max_output_tokens",
        "max_continuations",
        "output_token_ceiling",
        "web_search_max_uses",
        "web_fetch_max_uses",
        "web_fetch_max_content_tokens",
        "code_execution_tool",
        "refusal_fallback",
        "request_timeout_seconds",
        "evidence_max_bytes",
    ):
        assert getattr(run_config, name) == getattr(config, name), name
    schedule = config.to_schedule()
    for name in (
        "pre_open_time_et",
        "post_close_offset_minutes",
        "post_close_fallback_time_et",
        "grace_minutes",
    ):
        assert getattr(schedule, name) == getattr(config, name), name


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "claude-sonnet-5"},
        {"effort": "minimal"},
        {"pre_open_time_et": "8:40"},
        {"pre_open_time_et": "24:00"},
        # 开盘前那份必须真的在开盘前生成。
        {"pre_open_time_et": "09:30"},
        # 兜底时刻不能早于收盘后窗口的开启时刻。
        {"post_close_fallback_time_et": "16:15"},
        {"post_close_offset_minutes": 241},
        {"daily_max_runs": 0},
        {"max_output_tokens": 64_000, "output_token_ceiling": 32_000},
        {"request_timeout_seconds": 3_600.0},
        {"unknown_switch": True},
    ],
)
def test_market_brief_config_rejects_drift(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        MarketBriefConfig.model_validate({**MarketBriefConfig().model_dump(), **overrides})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "gpt-5.6-luna"),
        ("reasoning", "high"),
        ("max_concurrency", 5),
        ("execution_mode", "worker_sync"),
    ],
)
def test_paid_runtime_rejects_personal_edition_drift(field: str, value: object) -> None:
    payload = AIConfig().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        AIConfig.model_validate(payload)


@pytest.mark.parametrize(
    "network",
    ["0.0.0.0/0", "203.0.113.0/24", "8.8.8.0/24", "2001:db8::/32"],
)
def test_private_access_networks_reject_public_or_ambiguous_ranges(network: str) -> None:
    with pytest.raises(ValidationError):
        AccessConfig(allowed_private_cidrs=[network])



def test_old_personal_ai_configuration_remains_readable_without_rewriting(tmp_path: Path) -> None:
    path = tmp_path / "personal.toml"
    path.write_text('[ai]\nmodel = "gpt-5.6-terra"\nreasoning = "max"\nmax_concurrency = 1\n', encoding="utf-8")
    config = load_personal_config(path).ai
    assert config.model == "gpt-5.6-terra"
    assert config.reasoning == "max"
    assert config.max_concurrency == 1


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [("claude-haiku-5-5", "max"), ("gpt-5.6-terra", "xhigh")],
)
def test_personal_ai_rejects_mixed_provider_reasoning(model, reasoning) -> None:
    with pytest.raises(ValidationError):
        AIConfig(model=model, reasoning=reasoning)


@pytest.mark.parametrize("concurrency", [1, 2, 3, 4])
def test_claude_personal_configuration_accepts_bounded_parallelism(concurrency) -> None:
    assert AIConfig(max_concurrency=concurrency).max_concurrency == concurrency


@pytest.mark.parametrize("concurrency", [0, 5])
def test_claude_personal_configuration_rejects_out_of_range_parallelism(concurrency) -> None:
    with pytest.raises(ValidationError):
        AIConfig(max_concurrency=concurrency)


@pytest.mark.parametrize("concurrency", [2, 3, 4])
def test_legacy_openai_personal_configuration_keeps_concurrency_one(concurrency) -> None:
    with pytest.raises(ValidationError):
        AIConfig(model="gpt-5.6-terra", reasoning="max", max_concurrency=concurrency)


@pytest.mark.parametrize(
    ("model", "reasoning", "expected_concurrency"),
    [("gpt-5.6-terra", "max", 1), ("claude-haiku-5-5", "xhigh", 4)],
)
def test_personal_ai_omitted_concurrency_uses_provider_default(
    tmp_path: Path, model: str, reasoning: str, expected_concurrency: int,
) -> None:
    path = tmp_path / "personal.toml"
    original = f'[ai]\nmodel = "{model}"\nreasoning = "{reasoning}"\n'
    path.write_text(original, encoding="utf-8")
    config = load_personal_config(path).ai
    assert config.model == model
    assert config.reasoning == reasoning
    assert config.max_concurrency == expected_concurrency
    assert path.read_text(encoding="utf-8") == original
