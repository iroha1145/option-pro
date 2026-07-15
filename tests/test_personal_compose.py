from __future__ import annotations

import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _compose() -> dict[str, object]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _environment_keys(path: str = ".env.example") -> list[str]:
    keys: list[str] = []
    for raw_line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, _value = line.partition("=")
        assert separator, raw_line
        keys.append(key)
    return keys


def test_formal_compose_has_only_backend_worker_and_one_volume() -> None:
    compose = _compose()

    assert set(compose["services"]) == {"backend", "worker"}
    assert set(compose["volumes"]) == {"optix-data"}
    assert not (ROOT / "docker-compose.personal.yml").exists()

    for service in compose["services"].values():
        assert service["image"] == "option-pro:${APP_COMMIT:-local}"
        assert "optix-data:/data" in service["volumes"]
        assert service["read_only"] is True
        assert service["restart"] == "unless-stopped"
        assert service["env_file"] == [
            {
                "path": "secrets.env",
                "required": False,
            }
        ]
        assert service["environment"]["DATA_DIR"] == "/data"
        assert "OPTIX_WORKER_DB_PATH" not in service["environment"]
        assert "OPTIX_WORKER_LOCK_PATH" not in service["environment"]
        assert "OPTION_PRO_RUNTIME_SETTINGS_PATH" not in service["environment"]
        for legacy_setting in (
            "OPENAI_MODEL",
            "OPENAI_REASONING",
            "OPENAI_MAX_CONCURRENCY",
            "OPENAI_DAILY_MAX_JOBS",
        ):
            assert legacy_setting not in service["environment"]


def test_unified_worker_command_healthcheck_and_shutdown_window() -> None:
    worker = _compose()["services"]["worker"]

    assert worker["command"] == ["python", "-m", "app.worker"]
    assert worker["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "app.worker",
        "--healthcheck",
    ]
    assert worker["stop_grace_period"] == "2100s"
    assert "ports" not in worker


def test_backend_is_loopback_only_by_default() -> None:
    backend = _compose()["services"]["backend"]
    assert backend["ports"] == [
        "${HOST_BIND:-127.0.0.1}:${PORT:-2000}:8000"
    ]


def test_environment_templates_separate_secrets_from_machine_edges() -> None:
    deployment_keys = _environment_keys()
    secret_keys = _environment_keys("secrets.env.example")

    assert deployment_keys == [
        "HOST_BIND",
        "PORT",
        "MACROLENS_URL",
        "ALLOWED_HOSTS",
        "TRUST_PROXY_HEADERS",
        "TRUSTED_PROXY_CIDRS",
    ]
    assert set(secret_keys) == {
        "OPENAI_API_KEY",
        "INTERNAL_API_TOKEN",
        "APP_PASSWORD_HASH",
        "FINNHUB_API_KEY",
        "DATA_DIR",
    }
    all_keys = deployment_keys + secret_keys
    assert len(all_keys) == len(set(all_keys))
    assert not any(key.startswith("DEPLOY_" + "REQUIRE") for key in all_keys)
    assert not any(key.startswith("FOCUS_PRODUCER") for key in all_keys)
    assert "OPENAI_MODEL" not in all_keys
    assert "OPENAI_REASONING" not in all_keys
    assert "MACROLENS_ALLOW_" + "LOCAL_HTTP" not in all_keys
    assert "APP_AUTH_TOKEN" not in all_keys
    assert "PUBLIC_READ_API_ENABLED" not in all_keys
    assert "MACROLENS_INTERNAL_TOKEN" not in all_keys
    assert "MACROLENS_BASE_URL" not in all_keys

    config = tomllib.loads(
        (ROOT / "config" / "personal.toml").read_text(encoding="utf-8")
    )
    assert config["ai"]["model"] == "gpt-5.6-terra"
    assert config["ai"]["reasoning"] == "max"
    assert config["ai"]["daily_budget_usd"] == 2.0
    assert config["features"]["catalyst_mode"] == "manual"


def test_runtime_services_keep_the_container_security_baseline() -> None:
    for service in _compose()["services"].values():
        assert service["read_only"] is True
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cap_drop"] == ["ALL"]
        assert service["pids_limit"] == 256
        assert "/tmp:rw,noexec,nosuid,size=134217728,mode=1777" in service["tmpfs"]
