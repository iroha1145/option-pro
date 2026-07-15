from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


WORKER_HEALTH = (
    '{"healthy":true,"schema_version":"optix-worker-v1","tasks":['
    '{"task_name":"breakout"},{"task_name":"catalyst_sync"},'
    '{"task_name":"focus"},{"task_name":"ai_jobs"},'
    '{"task_name":"maintenance"}]}'
)


def _isolated_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            environment.pop(line.split("=", 1)[0], None)
    for key in tuple(environment):
        if key.startswith("COMPOSE_") or key.startswith("FAKE_"):
            environment.pop(key, None)
    environment.pop("APP_COMMIT", None)
    environment.pop("APP_VERSION", None)
    return environment


def _fake_tools(bin_dir: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        f"""#!/usr/bin/env bash
set -Eeuo pipefail

log() {{ printf '%s\\n' "$1" >> .fake-order; }}

if [ "${{1:-}}" = "info" ]; then exit 0; fi

if [ "${{1:-}}" = "ps" ]; then
    if [ "${{FAKE_LEGACY:-false}}" = "true" ]; then
        case "$*" in
            *service=ai-worker*) printf 'legacy-ai\\n' ;;
            *service=catalyst-sync-worker*) printf 'legacy-catalyst\\n' ;;
            *service=focus-context-producer*) printf 'legacy-focus\\n' ;;
            *service=breakout-worker*) printf 'legacy-breakout\\n' ;;
        esac
    fi
    exit 0
fi

if [ "${{1:-}}" = "stop" ]; then
    log "stop:$*"
    : > .fake-legacy-stopped
    exit 0
fi

if [ "${{1:-}}" = "inspect" ]; then
    if [ "${{FAKE_LEGACY_STILL_RUNNING:-false}}" = "true" ]; then
        printf 'true\\n'
    else
        printf 'false\\n'
    fi
    exit 0
fi

if [ "${{1:-}}" != "compose" ]; then exit 2; fi
shift

case "${{1:-}}" in
    version)
        printf '2.24.0\\n'
        ;;
    config)
        if [[ " $* " == *" --format json "* ]]; then
            printf '{{"name":"option-pro-test"}}\\n'
        fi
        ;;
    build)
        log build
        ;;
    up)
        if [ "${{FAKE_LEGACY:-false}}" = "true" ] && [ ! -f .fake-legacy-stopped ]; then
            exit 9
        fi
        log up
        ;;
    exec)
        if [[ " $* " == *" backend python -"* ]]; then
            log verify-backend
            printf '{{"status":"ready","app_commit":"unknown","frontend":{{"ready":true}}}}\\n'
        elif [[ " $* " == *" worker python -m app.worker --healthcheck"* ]]; then
            log verify-worker
            worker_health='{WORKER_HEALTH}'
            printf '%s\\n' "${{FAKE_WORKER_HEALTH:-$worker_health}}"
        else
            exit 2
        fi
        ;;
    port)
        printf '127.0.0.1:2000\\n'
        ;;
    ps|logs|down)
        ;;
    *)
        exit 2
        ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)


def _deployment_root(tmp_path: Path, environment_text: str | None = None) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "option-pro"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "deploy.sh", scripts / "deploy.sh")
    shutil.copy2(ROOT / "docker-compose.yml", root / "docker-compose.yml")
    (root / ".env").write_text(
        environment_text
        if environment_text is not None
        else (ROOT / ".env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tools(bin_dir)
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


def _replace(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    assert replaced, key
    return "\n".join(lines) + "\n"


def test_deploy_stops_every_legacy_worker_before_starting_unified_worker(tmp_path: Path) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["FAKE_LEGACY"] = "true"

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    order = (root / ".fake-order").read_text(encoding="utf-8").splitlines()
    assert order[0] == "build"
    assert order[1].startswith("stop:stop --time 2100")
    assert "legacy-ai" in order[1]
    assert "legacy-catalyst" in order[1]
    assert "legacy-focus" in order[1]
    assert "legacy-breakout" in order[1]
    assert order.index("up") > 1
    assert order[-2:] == ["verify-backend", "verify-worker"]


def test_deploy_refuses_to_start_when_a_legacy_worker_remains_live(tmp_path: Path) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["FAKE_LEGACY"] = "true"
    environment["FAKE_LEGACY_STILL_RUNNING"] = "true"

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "is still running" in result.stderr
    assert "up" not in (root / ".fake-order").read_text(encoding="utf-8").splitlines()


def test_deploy_requires_all_five_unified_task_types(tmp_path: Path) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["FAKE_WORKER_HEALTH"] = (
        '{"healthy":true,"schema_version":"optix-worker-v1",'
        '"tasks":[{"task_name":"breakout"}]}'
    )

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "all five task types" in result.stderr


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"HOST_BIND": "0.0.0.0", "APP_AUTH_TOKEN": ""},
            "Refusing non-loopback HOST_BIND",
        ),
        (
            {"PUBLIC_READ_API_ENABLED": "true", "APP_AUTH_TOKEN": ""},
            "requires APP_AUTH_TOKEN",
        ),
        (
            {"TRUST_PROXY_HEADERS": "true", "TRUSTED_PROXY_CIDRS": ""},
            "requires TRUSTED_PROXY_CIDRS",
        ),
        (
            {"MACROLENS_BASE_URL": "https://news.example", "MACROLENS_INTERNAL_TOKEN": ""},
            "requires MACROLENS_INTERNAL_TOKEN",
        ),
        (
            {"MACROLENS_BASE_URL": "", "MACROLENS_INTERNAL_TOKEN": "token"},
            "requires MACROLENS_BASE_URL",
        ),
        (
            {
                "MACROLENS_BASE_URL": "http://127.0.0.1:9000",
                "MACROLENS_INTERNAL_TOKEN": "token",
            },
            "must use HTTPS",
        ),
        (
            {"PUBLIC_READ_API_ENABLED": "sometimes"},
            "must be true or false",
        ),
    ],
)
def test_deploy_rejects_unsafe_or_incomplete_environment(
    tmp_path: Path,
    changes: dict[str, str],
    message: str,
) -> None:
    configured = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key, value in changes.items():
        configured = _replace(configured, key, value)
    root, environment = _deployment_root(tmp_path, configured)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert message in result.stderr
    assert not (root / ".fake-order").exists()


def test_setup_writes_one_literal_environment_file_without_model_prompts(tmp_path: Path) -> None:
    root = tmp_path / "option-pro"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for source, destination in (
        (ROOT / "setup.sh", root / "setup.sh"),
        (ROOT / ".env.example", root / ".env.example"),
        (ROOT / "docker-compose.yml", root / "docker-compose.yml"),
        (ROOT / "scripts" / "deploy.sh", scripts / "deploy.sh"),
    ):
        shutil.copy2(source, destination)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tools(bin_dir)
    environment = _isolated_environment()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    secret = "proxy-${HOME} # 'quoted' \\ tail"

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input=f"{secret}\n\nlocal-auth-token\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    generated = (root / ".env").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY='" in generated
    assert "APP_AUTH_TOKEN='local-auth-token'" in generated
    assert "OPENAI_MODEL=" not in generated
    assert "OPENAI_REASONING=" not in generated
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600


def test_shell_entrypoints_stay_small_and_syntax_is_valid() -> None:
    for relative in ("setup.sh", "scripts/deploy.sh"):
        path = ROOT / relative
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 300
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
