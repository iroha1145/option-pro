from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 and older local verification.
    import tomli as tomllib


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
        assert service["read_only"] is True
        assert service["restart"] == "unless-stopped"
        assert service["env_file"] == [
            {
                "path": ".env",
                "required": False,
            },
            {
                "path": "machine.env",
                "required": False,
            },
            {
                "path": "secrets.env",
                "required": False,
            }
        ]
        assert "optix-data:/data" in service["volumes"]
        assert service["environment"]["DATA_DIR"] == "${DATA_DIR:-/data}"
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
    compatibility_keys = _environment_keys()
    machine_keys = _environment_keys("machine.env.example")
    secret_keys = _environment_keys("secrets.env.example")

    assert compatibility_keys == []
    assert machine_keys == [
        "HOST_BIND",
        "PORT",
        "MACROLENS_URL",
        "ALLOWED_HOSTS",
        "TRUST_PROXY_HEADERS",
        "TRUSTED_PROXY_CIDRS",
        "DATA_DIR",
    ]
    assert secret_keys == [
        "OPENAI_API_KEY",
        "FINNHUB_API_KEY",
        "MARKETDATA_TOKEN",
        "INTERNAL_API_TOKEN",
        "APP_PASSWORD_HASH",
    ]
    all_keys = machine_keys + secret_keys
    assert len(machine_keys) == 7
    assert len(secret_keys) == 5
    assert len(all_keys) == 12
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
    assert "ACCESS_MODE" not in all_keys

    config = tomllib.loads(
        (ROOT / "config" / "personal.toml").read_text(encoding="utf-8")
    )
    assert config["ai"]["model"] == "gpt-5.6-terra"
    assert config["ai"]["reasoning"] == "max"
    assert config["ai"]["daily_budget_usd"] == 2.0
    assert config["features"]["catalyst_mode"] == "manual"
    assert config["access"]["mode"] in {"private_network", "password"}

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "text.count('catalyst_mode = \"manual\"') != 1" in workflow
    assert (
        "text.replace('catalyst_mode = \"manual\"', "
        "'catalyst_mode = \"off\"', 1)"
    ) in workflow
    health_line = next(
        line
        for line in workflow.splitlines()
        if 'actual={x.get("task_name")' in line
    )
    expected_text = health_line.split("expected={", 1)[1].split("}; actual", 1)[0]
    expected_tasks = {
        item.strip().strip('"') for item in expected_text.split(",")
    }
    assert expected_tasks == {
        "breakout",
        "catalyst_sync",
        "focus",
        "ai_jobs",
        "maintenance",
        "focus_refresh",
        "strength_refresh",
        "breakout_refresh",
        "retention",
    }
    assert 'p.get("schema_version")=="optix-worker-v2"' in health_line


def test_compose_and_templates_have_no_legacy_services_or_independent_paths() -> None:
    sources = "\n".join(
        (
            (ROOT / "docker-compose.yml").read_text(encoding="utf-8"),
            (ROOT / ".env.example").read_text(encoding="utf-8"),
            (ROOT / "machine.env.example").read_text(encoding="utf-8"),
            (ROOT / "secrets.env.example").read_text(encoding="utf-8"),
        )
    )
    for legacy_service in (
        "ai-worker",
        "catalyst-sync-worker",
        "focus-context-producer",
        "breakout-worker",
    ):
        assert legacy_service not in sources
    for legacy_path in (
        "OPENAI_JOB_DB_PATH",
        "MACROLENS_CACHE_DB_PATH",
        "BREAKOUT_DB_PATH",
        "OPTIX_WORKER_DB_PATH",
        "OPTIX_WORKER_LOCK_PATH",
        "WATCHLIST_SNAPSHOT_PATH",
        "OPTION_PRO_RUNTIME_SETTINGS_PATH",
    ):
        assert legacy_path not in sources


def test_runtime_services_keep_the_container_security_baseline() -> None:
    for service in _compose()["services"].values():
        assert service["read_only"] is True
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cap_drop"] == ["ALL"]
        assert service["pids_limit"] == 256
        assert "/tmp:rw,noexec,nosuid,size=134217728,mode=1777" in service["tmpfs"]


@pytest.mark.parametrize(
    ("exported_data_dir", "expected_data_dir"),
    (
        (None, "/data/machine"),
        ("/data/caller-export", "/data/caller-export"),
    ),
)
def test_data_directory_cannot_split_across_exports_or_runtime_files(
    tmp_path: Path,
    exported_data_dir: str | None,
    expected_data_dir: str,
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is unavailable")

    shutil.copy2(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    (tmp_path / ".env").write_text(
        "HOST_BIND=127.0.0.9\nPORT=2999\nDATA_DIR=/stale-data\n",
        encoding="utf-8",
    )
    (tmp_path / "machine.env").write_text(
        "HOST_BIND=127.0.0.2\n"
        "PORT=2444\n"
        "MACROLENS_URL=https://macrolens.example\n"
        "ALLOWED_HOSTS=127.0.0.2\n"
        "TRUST_PROXY_HEADERS=false\n"
        "TRUSTED_PROXY_CIDRS=\n"
        "DATA_DIR=/data/machine\n",
        encoding="utf-8",
    )
    (tmp_path / "secrets.env").write_text(
        "HOST_BIND=0.0.0.0\n"
        "PORT=8999\n"
        "MACROLENS_URL=https://wrong-secret.example\n"
        "DATA_DIR=/stale-secret-data\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    for key in (
        "HOST_BIND",
        "PORT",
        "MACROLENS_URL",
        "ALLOWED_HOSTS",
        "TRUST_PROXY_HEADERS",
        "TRUSTED_PROXY_CIDRS",
    ):
        environment.pop(key, None)
    if exported_data_dir is not None:
        environment["DATA_DIR"] = exported_data_dir
    environment["COMPOSE_ENV_FILES"] = ".env,machine.env"
    environment["COMPOSE_PROJECT_NAME"] = "optix-config-contract"

    result = subprocess.run(
        [docker, "compose", "config", "--format", "json"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    backend = payload["services"]["backend"]
    worker = payload["services"]["worker"]
    published = backend["ports"][0]
    assert published["host_ip"] == "127.0.0.2"
    assert str(published["published"]) == "2444"
    for service in (backend, worker):
        assert service["environment"]["HOST_BIND"] == "127.0.0.2"
        assert service["environment"]["PORT"] == "2444"
        assert service["environment"]["DATA_DIR"] == expected_data_dir
        assert service["environment"]["MACROLENS_URL"] == (
            "https://macrolens.example"
        )
        assert any(
            volume["source"] == "optix-data"
            and volume["target"] == "/data"
            for volume in service["volumes"]
        )


def test_deploy_uses_the_unified_runtime_loader_and_validator() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "resolve_container_data_dir" not in script
    assert "dotenv_values" not in script
    assert "    validate_runtime_boundary\n" in script
    assert (
        "docker compose run --rm --no-deps -T backend \\\n"
        "            python -m app.tools.validate_personal_deployment"
    ) in script
    assert 'PYTHONPATH="${ROOT_DIR}/backend' not in script
