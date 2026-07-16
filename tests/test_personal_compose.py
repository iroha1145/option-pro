from __future__ import annotations

import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_personal_compose_has_only_web_worker_and_one_persistent_volume() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.personal.yml").read_text(encoding="utf-8")
    )

    assert set(compose["services"]) == {"backend", "worker"}
    assert set(compose["volumes"]) == {"optix-data"}
    for service in compose["services"].values():
        assert "optix-data:/data" in service["volumes"]
        assert service["read_only"] is True
        assert service["restart"] == "unless-stopped"
        assert service["env_file"] == [
            {
                "path": "${PERSONAL_MACHINE_FILE:-machine.env}",
                "required": False,
            },
            {
                "path": "${PERSONAL_SECRETS_FILE:-config/migrated/secrets.env}",
                "required": False,
            }
        ]
        assert "OPENAI_MODEL" not in service["environment"]
        assert "OPENAI_REASONING" not in service["environment"]
        assert "OPENAI_MAX_CONCURRENCY" not in service["environment"]
        assert "OPENAI_DAILY_MAX_JOBS" not in service["environment"]

    personal_config = tomllib.loads(
        (ROOT / "config" / "personal.toml").read_text(encoding="utf-8")
    )
    assert personal_config["ai"] == {
        "model": "gpt-5.6-terra",
        "reasoning": "max",
        "max_concurrency": 1,
        "daily_max_jobs": 4,
        "daily_budget_usd": 2.0,
        "execution_mode": "background",
    }


def test_personal_worker_command_and_healthcheck_match_unified_cli() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.personal.yml").read_text(encoding="utf-8")
    )
    worker = compose["services"]["worker"]

    assert worker["command"] == ["python", "-m", "app.worker"]
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "app.worker",
        "--healthcheck",
    ]
    assert worker["environment"]["OPTIX_WORKER_DB_PATH"] == (
        "${OPTIX_WORKER_DB_PATH:-/data/optix-worker.db}"
    )
    assert worker["environment"]["OPTIX_WORKER_LOCK_PATH"] == (
        "${OPTIX_WORKER_LOCK_PATH:-/data/optix-worker.lock}"
    )
    assert worker["stop_grace_period"] == "2100s"


def test_personal_backend_binds_to_loopback_by_default() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.personal.yml").read_text(encoding="utf-8")
    )
    assert compose["services"]["backend"]["ports"] == [
        "${HOST_BIND:-127.0.0.1}:${PORT:-2000}:8000"
    ]
