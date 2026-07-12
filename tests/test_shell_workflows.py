from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _isolated_environment() -> dict[str, str]:
    """Return a host environment that cannot override the fixture dotenv."""
    environment = os.environ.copy()
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            environment.pop(key, None)
    environment.pop("APP_COMMIT", None)
    environment.pop("APP_VERSION", None)
    for key in tuple(environment):
        if key.startswith("COMPOSE_"):
            environment.pop(key, None)
    return environment


def _fake_docker(bin_dir: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
if [ "${1:-}" = "info" ]; then
    exit 0
fi
if [ "${1:-}" != "compose" ]; then
    exit 2
fi
shift
if [ "${1:-}" = "version" ]; then
    printf '2.24.0\\n'
    exit 0
fi
if [ "${1:-}" = "exec" ] && [[ " $* " == *" breakout-worker "*" --healthcheck "* ]]; then
    enabled="$(awk -F= '$1 == "BREAKOUT_RADAR_ENABLED" {print $2}' .env | tail -n 1)"
    enabled="${enabled%\\\"}"; enabled="${enabled#\\\"}"
    enabled="${enabled%\\'}"; enabled="${enabled#\\'}"
    case "$enabled" in
        1|true|TRUE|yes|YES) printf '{"healthy":true,"status":"active"}\\n' ;;
        *) printf '{"healthy":true,"status":"disabled"}\\n' ;;
    esac
fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)


def _deployment_root(tmp_path: Path, env_text: str) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "option-pro"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "deploy.sh", scripts / "deploy.sh")
    (root / ".env").write_text(env_text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_docker(bin_dir)
    environment = _isolated_environment()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    return root, environment


def _run_deploy(root: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/deploy.sh"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_default_and_quoted_safe_breakout_config_pass_deployment_gate(tmp_path: Path) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    root, environment = _deployment_root(tmp_path, template)
    result = _run_deploy(root, environment)
    assert result.returncode == 0, result.stderr

    quoted = (
        template.replace("BREAKOUT_RADAR_ENABLED=false", 'BREAKOUT_RADAR_ENABLED="false"')
        .replace("DEPLOY_REQUIRE_BREAKOUT=false", "DEPLOY_REQUIRE_BREAKOUT='false'")
        .replace("RANGE_PERSISTENCE_MODE=shadow", 'RANGE_PERSISTENCE_MODE="shadow"')
    )
    (root / ".env").write_text(quoted, encoding="utf-8")
    result = _run_deploy(root, environment)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("enabled_value", "interaction_value", "expected_enabled", "expected_interaction"),
    [
        ('"true"', "'false'", "true", "false"),
        ("'false'", '"true"', "false", "true"),
    ],
)
def test_real_compose_preserves_quoted_boolean_values(
    tmp_path: Path,
    enabled_value: str,
    interaction_value: str,
    expected_enabled: str,
    expected_interaction: str,
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is unavailable")

    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    env_file = tmp_path / "quoted.env"
    env_file.write_text(
        template.replace(
            "BREAKOUT_RADAR_ENABLED=false",
            f"BREAKOUT_RADAR_ENABLED={enabled_value}",
        ).replace(
            "RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED=false",
            f"RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED={interaction_value}",
        ),
        encoding="utf-8",
    )
    compose_environment = _isolated_environment()
    rendered = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(ROOT / "docker-compose.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        env=compose_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert rendered.returncode == 0, rendered.stderr
    payload = json.loads(rendered.stdout)
    for service_name in ("backend", "breakout-worker"):
        environment = payload["services"][service_name]["environment"]
        assert environment["BREAKOUT_RADAR_ENABLED"] == expected_enabled
        assert (
            environment["RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED"]
            == expected_interaction
        )


def test_required_breakout_gate_rejects_disabled_but_allows_interaction_off(
    tmp_path: Path,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    required_disabled = template.replace(
        "DEPLOY_REQUIRE_BREAKOUT=false", "DEPLOY_REQUIRE_BREAKOUT=true"
    )
    root, environment = _deployment_root(tmp_path, required_disabled)
    rejected = _run_deploy(root, environment)
    assert rejected.returncode != 0
    assert "requires BREAKOUT_RADAR_ENABLED=true" in rejected.stderr

    required_enabled = required_disabled.replace(
        "BREAKOUT_RADAR_ENABLED=false", "BREAKOUT_RADAR_ENABLED=true"
    )
    assert "RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED=false" in required_enabled
    (root / ".env").write_text(required_enabled, encoding="utf-8")
    accepted = _run_deploy(root, environment)
    assert accepted.returncode == 0, accepted.stderr


@pytest.mark.parametrize(
    "base_url",
    [
        "https://localhost",
        "https://LOCALHOST.",
        "https://news.localhost",
        "https://127.0.0.1:8443",
        "https://127.255.255.254",
        "https://[::1]:8443",
        "https://[0:0:0:0:0:0:0:1]",
        "https://[::ffff:127.0.0.1]",
    ],
)
def test_required_catalyst_gate_rejects_loopback_remote_urls(
    tmp_path: Path, base_url: str
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = (
        template.replace("MACROLENS_ENABLED=false", "MACROLENS_ENABLED=true")
        .replace("MACROLENS_BASE_URL=", f"MACROLENS_BASE_URL={base_url}", 1)
        .replace("MACROLENS_READ_KEY_ID=", "MACROLENS_READ_KEY_ID=read-key", 1)
        .replace("MACROLENS_READ_SECRET=", "MACROLENS_READ_SECRET=read-secret", 1)
        .replace("MACROLENS_ACTION_KEY_ID=", "MACROLENS_ACTION_KEY_ID=action-key", 1)
        .replace("MACROLENS_ACTION_SECRET=", "MACROLENS_ACTION_SECRET=action-secret", 1)
        .replace("DEPLOY_REQUIRE_CATALYST=false", "DEPLOY_REQUIRE_CATALYST=true")
        .replace("APP_AUTH_TOKEN=", "APP_AUTH_TOKEN=app-token", 1)
    )
    root, environment = _deployment_root(tmp_path, required)
    result = _run_deploy(root, environment)
    assert result.returncode != 0
    assert "requires a non-loopback MACROLENS_BASE_URL" in result.stderr


def test_legacy_env_and_incomplete_trusted_proxy_config_fail_before_build(
    tmp_path: Path,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    legacy = "\n".join(
        line for line in template.splitlines() if not line.startswith("ALLOWED_HOSTS=")
    )
    root, environment = _deployment_root(tmp_path, legacy)
    result = _run_deploy(root, environment)
    assert result.returncode != 0
    assert "predates the ALLOWED_HOSTS security setting" in result.stderr

    incomplete_proxy = template.replace(
        "TRUST_PROXY_HEADERS=false", "TRUST_PROXY_HEADERS=true"
    )
    (root / ".env").write_text(incomplete_proxy, encoding="utf-8")
    result = _run_deploy(root, environment)
    assert result.returncode != 0
    assert "requires TRUSTED_PROXY_CIDRS" in result.stderr


def test_setup_copies_full_template_and_writes_secrets_as_literal_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "option-pro"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "setup.sh", root / "setup.sh")
    shutil.copy2(ROOT / ".env.example", root / ".env.example")
    shutil.copy2(ROOT / "scripts" / "deploy.sh", scripts / "deploy.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_docker(bin_dir)
    environment = _isolated_environment()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    secret = "proxy-${HOME} # 'quoted' \\ tail"
    answers = f"\n{secret}\n\n\n"
    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input=answers,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    generated = (root / ".env").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY='" in generated
    assert "BREAKOUT_RADAR_ENABLED=false" in generated
    assert "DEPLOY_REQUIRE_BREAKOUT=false" in generated
    assert "RANGE_PERSISTENCE_MODE=shadow" in generated
    assert "RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED=false" in generated
    assert secret not in result.stdout
    assert secret not in result.stderr

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is unavailable")
    probe = root / "compose-probe.yml"
    probe.write_text(
        """services:
  probe:
    image: scratch
    environment:
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
""",
        encoding="utf-8",
    )
    rendered = subprocess.run(
        [docker, "compose", "--env-file", ".env", "-f", str(probe), "config", "--format", "json"],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    payload = json.loads(rendered.stdout)
    rendered_value = payload["services"]["probe"]["environment"]["OPENAI_API_KEY"]
    # Compose renders a literal dollar as ``$$`` in the canonical model; the
    # container receives one dollar rather than expanding the host variable.
    assert rendered_value.replace("$$", "$") == secret
