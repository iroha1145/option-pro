from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.personal_config import AIConfig, AccessConfig, load_personal_config


def test_repository_personal_config_freezes_paid_runtime() -> None:
    config = load_personal_config()

    assert config.ai.model == "gpt-5.6-terra"
    assert config.ai.reasoning == "max"
    assert config.ai.max_concurrency == 1
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "gpt-5.6-luna"),
        ("reasoning", "high"),
        ("max_concurrency", 2),
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
