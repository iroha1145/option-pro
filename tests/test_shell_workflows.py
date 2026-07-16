from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.tools import personal_secrets


ROOT = Path(__file__).resolve().parents[1]

WORKER_HEALTH = (
    '{"healthy":true,"schema_version":"optix-worker-v2","tasks":['
    '{"task_name":"breakout"},{"task_name":"catalyst_sync"},'
    '{"task_name":"focus"},{"task_name":"ai_jobs"},'
    '{"task_name":"maintenance"},{"task_name":"focus_refresh"},'
    '{"task_name":"strength_refresh"},{"task_name":"breakout_refresh"},'
    '{"task_name":"retention"}]}'
)
REMOVED_RUNTIME_KEYS = (
    "ACCESS_MODE",
    "APP_AUTH_TOKEN",
    "PUBLIC_READ_API_ENABLED",
    "ALLOW_INSECURE_PUBLIC_BIND",
    "MACROLENS_BASE_URL",
    "MACROLENS_INTERNAL_TOKEN",
    "OPENAI_JOB_DB_PATH",
    "MACROLENS_CACHE_DB_PATH",
    "BREAKOUT_DB_PATH",
    "OPTIX_WORKER_DB_PATH",
    "OPTIX_WORKER_LOCK_PATH",
    "WATCHLIST_SNAPSHOT_PATH",
    "OPTION_PRO_RUNTIME_SETTINGS_PATH",
)


def _template_keys(path: str) -> set[str]:
    keys: set[str] = set()
    for raw_line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0])
    return keys


def _isolated_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in (
        _template_keys(".env.example")
        | _template_keys("machine.env.example")
        | _template_keys("secrets.env.example")
        | set(REMOVED_RUNTIME_KEYS)
    ):
        environment.pop(key, None)
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

log() {{ printf '%s\n' "$1" >> .fake-order; }}

if [ "${{1:-}}" = "info" ] || [ "${{1:-}}" = "ps" ]; then exit 0; fi
if [ "${{1:-}}" != "compose" ]; then exit 2; fi
shift
if [ "${{1:-}}" != version ] && [ -n "${{FAKE_COMPOSE_ENV_LOG:-}}" ]; then
    printf '%s\n' "${{COMPOSE_ENV_FILES:-}}" >> "$FAKE_COMPOSE_ENV_LOG"
fi

case "${{1:-}}" in
    version)
        printf '2.24.0\n'
        ;;
    config)
        if [[ " $* " == *" --format json "* ]]; then
            printf '{{"name":"option-pro"}}\n'
        fi
        ;;
    build)
        log build
        ;;
    run)
        if [[ " $* " == *" --rm --no-deps -T backend python -m app.tools.validate_personal_deployment "* ]]; then
            log validate
            PYTHONPATH="$PWD/backend${{PYTHONPATH:+:${{PYTHONPATH}}}}" \
                "$FAKE_CONTAINER_PYTHON" -m app.tools.validate_personal_deployment
        else
            exit 2
        fi
        ;;
    up)
        log up
        ;;
    exec)
        if [[ " $* " == *" backend python -"* ]]; then
            log verify-backend
            printf '{{"status":"ready","app_commit":"unknown","frontend":{{"ready":true}}}}\n'
        elif [[ " $* " == *" worker python -m app.worker --healthcheck"* ]]; then
            log verify-worker
            worker_health='{WORKER_HEALTH}'
            printf '%s\n' "${{FAKE_WORKER_HEALTH:-$worker_health}}"
        else
            exit 2
        fi
        ;;
    port)
        printf '127.0.0.1:2000\n'
        ;;
    ps|logs|down|restart)
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


def _replace(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            return "\n".join(lines) + "\n"
    raise AssertionError(key)


def _personal_config(mode: str = "private_network") -> str:
    source = (ROOT / "config" / "personal.toml").read_text(encoding="utf-8")
    lines = source.splitlines()
    in_access = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[access]":
            in_access = True
            continue
        if stripped.startswith("["):
            in_access = False
        if in_access and stripped.startswith("mode ="):
            lines[index] = f'mode = "{mode}"'
            return "\n".join(lines) + "\n"
    raise AssertionError("[access].mode")


def _copy_deployment_validator(root: Path) -> None:
    backend = root / "backend" / "app"
    (backend / "tools").mkdir(parents=True)
    (backend / "services").mkdir()
    for relative in (
        "__init__.py",
        "access.py",
        "config.py",
        "data_paths.py",
        "deployment_boundary.py",
        "legacy_env_adapter.py",
        "personal_config.py",
        "runtime_environment.py",
        "services/__init__.py",
        "services/request_security.py",
        "tools/__init__.py",
        "tools/migrate_legacy_machine_environment.py",
        "tools/validate_personal_deployment.py",
    ):
        shutil.copy2(ROOT / "backend" / "app" / relative, backend / relative)


def _deployment_root(
    tmp_path: Path,
    machine_text: str | None = None,
    secrets_text: str | None = None,
    *,
    access_mode: str = "private_network",
) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "option-pro"
    scripts = root / "scripts"
    config = root / "config"
    scripts.mkdir(parents=True)
    config.mkdir()
    shutil.copy2(ROOT / "scripts" / "deploy.sh", scripts / "deploy.sh")
    shutil.copy2(ROOT / "docker-compose.yml", root / "docker-compose.yml")
    (root / ".env").write_text(
        (ROOT / ".env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "machine.env").write_text(
        machine_text
        if machine_text is not None
        else (ROOT / "machine.env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "secrets.env").write_text(
        secrets_text
        if secrets_text is not None
        else (ROOT / "secrets.env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (config / "personal.toml").write_text(
        _personal_config(access_mode),
        encoding="utf-8",
    )
    _copy_deployment_validator(root)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tools(bin_dir)
    environment = _isolated_environment()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CONTAINER_PYTHON"] = sys.executable
    return root, environment


def _run_deploy(
    root: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/deploy.sh"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _commit_deployment_root(root: Path) -> None:
    commands = (
        ["git", "init", "--quiet"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Optix Test",
            "-c",
            "user.email=optix-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "test fixture",
        ],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr


def test_deploy_builds_only_current_services_and_verifies_both(tmp_path: Path) -> None:
    root, environment = _deployment_root(tmp_path)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
        "up",
        "verify-backend",
        "verify-worker",
    ]
    script = (root / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "docker compose build --pull backend" in script
    assert "docker compose up -d --no-build --force-recreate" in script
    assert "Stopping legacy workers before the unified worker starts." in script


def test_deploy_validates_with_built_runtime_when_host_python_lacks_dependencies(
    tmp_path: Path,
) -> None:
    root, environment = _deployment_root(tmp_path)
    unavailable_host_python = tmp_path / "host-python"
    unavailable_host_python.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"ModuleNotFoundError: No module named 'pydantic'\" >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    unavailable_host_python.chmod(0o755)
    environment["PYTHON_BIN"] = str(unavailable_host_python)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
        "up",
        "verify-backend",
        "verify-worker",
    ]
    assert "ModuleNotFoundError" not in result.stdout + result.stderr


@pytest.mark.parametrize("staged", (False, True))
def test_deploy_allows_only_personal_config_content_changes(
    tmp_path: Path,
    staged: bool,
) -> None:
    root, environment = _deployment_root(tmp_path)
    _commit_deployment_root(root)
    personal_config = root / "config" / "personal.toml"
    personal_config.write_text(
        personal_config.read_text(encoding="utf-8")
        + "\n# Local owner configuration.\n",
        encoding="utf-8",
    )
    if staged:
        completed = subprocess.run(
            ["git", "add", "config/personal.toml"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("state", ("tracked", "staged", "untracked"))
def test_deploy_rejects_every_other_worktree_change(
    tmp_path: Path,
    state: str,
) -> None:
    root, environment = _deployment_root(tmp_path)
    _commit_deployment_root(root)
    if state == "untracked":
        (root / "local-note.txt").write_text("not part of release\n", encoding="utf-8")
    else:
        compose = root / "docker-compose.yml"
        compose.write_text(
            compose.read_text(encoding="utf-8") + "\n# unexpected change\n",
            encoding="utf-8",
        )
        if state == "staged":
            completed = subprocess.run(
                ["git", "add", "docker-compose.yml"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert completed.returncode == 0, completed.stderr

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "Refusing to deploy a dirty working tree." in result.stderr
    assert not (root / ".fake-order").exists()


@pytest.mark.parametrize("replacement", ("missing", "symlink"))
def test_deploy_requires_personal_config_to_remain_a_regular_file(
    tmp_path: Path,
    replacement: str,
) -> None:
    root, environment = _deployment_root(tmp_path)
    _commit_deployment_root(root)
    personal_config = root / "config" / "personal.toml"
    personal_config.unlink()
    if replacement == "symlink":
        personal_config.symlink_to(root / ".env")

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "config/personal.toml must remain a regular file." in result.stderr
    assert not (root / ".fake-order").exists()


def test_deploy_fails_closed_when_worktree_status_cannot_be_read(
    tmp_path: Path,
) -> None:
    root, environment = _deployment_root(tmp_path)
    _commit_deployment_root(root)
    real_git = shutil.which("git")
    assert real_git is not None
    fake_git = Path(environment["PATH"].split(os.pathsep, maxsplit=1)[0]) / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "for argument in \"$@\"; do\n"
        "    if [ \"$argument\" = \":(top,exclude)config/personal.toml\" ]; then\n"
        "        exit 42\n"
        "    fi\n"
        "done\n"
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "Unable to inspect the working tree." in result.stderr
    assert not (root / ".fake-order").exists()


def test_deploy_selects_machine_file_without_caller_exports(tmp_path: Path) -> None:
    root, environment = _deployment_root(tmp_path)
    capture = tmp_path / "compose-env-files"
    environment["FAKE_COMPOSE_ENV_LOG"] = str(capture)
    assert "COMPOSE_ENV_FILES" not in environment
    assert "HOST_BIND" not in environment
    assert "PORT" not in environment

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    selected = capture.read_text(encoding="utf-8").splitlines()
    assert selected
    assert set(selected) == {".env,machine.env"}


def test_deploy_requires_all_nine_unified_task_types(tmp_path: Path) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["FAKE_WORKER_HEALTH"] = (
        '{"healthy":true,"schema_version":"optix-worker-v2",'
        '"tasks":[{"task_name":"breakout"}]}'
    )

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "all nine task types" in result.stderr


@pytest.mark.parametrize(
    "host_bind",
    ("127.0.0.1", "10.7.0.8", "192.168.50.20", "100.64.12.4", "::1"),
)
def test_private_network_accepts_only_explicit_allowed_bindings(
    tmp_path: Path,
    host_bind: str,
) -> None:
    configured = _replace(
        (ROOT / "machine.env.example").read_text(encoding="utf-8"),
        "HOST_BIND",
        host_bind,
    )
    root, environment = _deployment_root(tmp_path, configured)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert '"access_mode": "private_network"' in result.stdout


@pytest.mark.parametrize("host_bind", ("0.0.0.0", "8.8.8.8", "example.com"))
def test_private_network_rejects_wildcard_public_and_hostname_bindings(
    tmp_path: Path,
    host_bind: str,
) -> None:
    configured = _replace(
        (ROOT / "machine.env.example").read_text(encoding="utf-8"),
        "HOST_BIND",
        host_bind,
    )
    root, environment = _deployment_root(tmp_path, configured)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "private_network" in result.stderr
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
    ]


def test_stale_secret_file_cannot_mask_the_machine_access_boundary(
    tmp_path: Path,
) -> None:
    configured = _replace(
        (ROOT / "machine.env.example").read_text(encoding="utf-8"),
        "HOST_BIND",
        "0.0.0.0",
    )
    secrets = (
        (ROOT / "secrets.env.example").read_text(encoding="utf-8")
        + "HOST_BIND=127.0.0.1\n"
        + "DATA_DIR=/outside-volume\n"
    )
    root, environment = _deployment_root(tmp_path, configured, secrets)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "private_network" in result.stderr
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
    ]


def test_private_network_rejects_a_public_cidr_even_when_explicitly_listed(
    tmp_path: Path,
) -> None:
    configured = _replace(
        (ROOT / "machine.env.example").read_text(encoding="utf-8"),
        "HOST_BIND",
        "8.8.8.8",
    )
    root, environment = _deployment_root(tmp_path, configured)
    personal_config = root / "config" / "personal.toml"
    personal_config.write_text(
        personal_config.read_text(encoding="utf-8").replace(
            'allowed_private_cidrs = ["127.0.0.0/8", "::1/128", '
            '"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", '
            '"100.64.0.0/10"]',
            'allowed_private_cidrs = ["8.8.8.0/24"]',
        ),
        encoding="utf-8",
    )

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "private access networks" in result.stderr
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
    ]


def test_password_mode_requires_only_configured_password_hash(tmp_path: Path) -> None:
    configured = _replace(
        (ROOT / "machine.env.example").read_text(encoding="utf-8"),
        "HOST_BIND",
        "0.0.0.0",
    )
    root, environment = _deployment_root(
        tmp_path,
        configured,
        access_mode="password",
    )

    missing = _run_deploy(root, environment)
    assert missing.returncode != 0
    assert "requires a valid APP_PASSWORD_HASH" in missing.stderr
    assert "APP_PASSWORD_HASH=" not in missing.stdout + missing.stderr

    password_hash = personal_secrets.hash_owner_password(
        "owner-password-for-deployment-test"
    )
    secrets = _replace(
        (ROOT / "secrets.env.example").read_text(encoding="utf-8"),
        "APP_PASSWORD_HASH",
        password_hash,
    )
    (root / "secrets.env").write_text(secrets, encoding="utf-8")
    configured_result = _run_deploy(root, environment)
    assert configured_result.returncode == 0, configured_result.stderr
    assert password_hash not in (
        configured_result.stdout + configured_result.stderr
    )
    assert '"access_mode": "password"' in configured_result.stdout


def test_access_mode_environment_cannot_override_personal_toml(tmp_path: Path) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["ACCESS_MODE"] = "password"

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert '"access_mode": "private_network"' in result.stdout


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"TRUST_PROXY_HEADERS": "true", "TRUSTED_PROXY_CIDRS": ""},
            "proxied deployments must use password mode",
        ),
        (
            {
                "TRUST_PROXY_HEADERS": "true",
                "TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
                "ALLOWED_HOSTS": "",
            },
            "proxied deployments must use password mode",
        ),
    ],
)
def test_deploy_rejects_incomplete_proxy_boundary(
    tmp_path: Path,
    changes: dict[str, str],
    message: str,
) -> None:
    configured = (ROOT / "machine.env.example").read_text(encoding="utf-8")
    for key, value in changes.items():
        configured = _replace(configured, key, value)
    root, environment = _deployment_root(tmp_path, configured)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert message in result.stderr
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
    ]


@pytest.mark.parametrize(
    ("url", "token", "message"),
    [
        ("https://news.example", "", "MACROLENS_URL requires INTERNAL_API_TOKEN"),
        ("", "internal-token", "INTERNAL_API_TOKEN requires MACROLENS_URL"),
        (
            "http://127.0.0.1:9000",
            "internal-token",
            "MACROLENS_URL must be an absolute HTTPS origin",
        ),
    ],
)
def test_deploy_validates_the_single_macrolens_pair(
    tmp_path: Path,
    url: str,
    token: str,
    message: str,
) -> None:
    configured = _replace(
        (ROOT / "machine.env.example").read_text(encoding="utf-8"),
        "MACROLENS_URL",
        url,
    )
    secrets = _replace(
        (ROOT / "secrets.env.example").read_text(encoding="utf-8"),
        "INTERNAL_API_TOKEN",
        token,
    )
    root, environment = _deployment_root(tmp_path, configured, secrets)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert message in result.stderr
    if token:
        assert token not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("url", "token", "message"),
    [
        (
            "https://news.example:not-a-port",
            "valid-internal-token",
            "MACROLENS_URL contains an invalid port",
        ),
        (
            "https://news.example:70000",
            "valid-internal-token",
            "MACROLENS_URL contains an invalid port",
        ),
        (
            "https://news.example:0",
            "valid-internal-token",
            "MACROLENS_URL contains an invalid port",
        ),
        (
            "https://news.example:",
            "valid-internal-token",
            "MACROLENS_URL contains an invalid port",
        ),
        (
            " https://news.example",
            "valid-internal-token",
            "MACROLENS_URL must not contain whitespace",
        ),
        (
            "https://news.example\n",
            "valid-internal-token",
            "MACROLENS_URL must not contain whitespace",
        ),
        (
            "https://owner:password@news.example",
            "valid-internal-token",
            "MACROLENS_URL must not contain credentials",
        ),
        (
            "https://news.example/internal/v1",
            "valid-internal-token",
            "MACROLENS_URL must contain only scheme, host, and port",
        ),
        (
            "https://news.example?target=other",
            "valid-internal-token",
            "MACROLENS_URL must contain only scheme, host, and port",
        ),
        (
            "https://news.example#fragment",
            "valid-internal-token",
            "MACROLENS_URL must contain only scheme, host, and port",
        ),
        (
            "https://" + "a" * 493,
            "valid-internal-token",
            "at most 500 characters",
        ),
        (
            "https://news.example",
            " token-with-leading-space",
            "INTERNAL_API_TOKEN contains unsupported whitespace",
        ),
        (
            "https://news.example",
            "t" * 4097,
            "INTERNAL_API_TOKEN is too long",
        ),
    ],
)
def test_deploy_rejects_invalid_macrolens_values_without_echo(
    tmp_path: Path,
    url: str,
    token: str,
    message: str,
) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["MACROLENS_URL"] = url
    environment["INTERNAL_API_TOKEN"] = token

    result = _run_deploy(root, environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert message in output
    assert url not in output
    assert token not in output
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
    ]


@pytest.mark.parametrize(
    "data_directory",
    ("relative/data", "/srv/optix-data", "/data/../etc"),
)
def test_deploy_rejects_data_directory_outside_the_container_volume(
    tmp_path: Path,
    data_directory: str,
) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["DATA_DIR"] = data_directory

    result = _run_deploy(root, environment)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "DATA_DIR must be" in output
    assert data_directory not in output
    assert (root / ".fake-order").read_text(encoding="utf-8").splitlines() == [
        "build",
        "validate",
    ]


def test_deploy_accepts_a_nested_directory_inside_the_container_volume(
    tmp_path: Path,
) -> None:
    root, environment = _deployment_root(tmp_path)
    environment["DATA_DIR"] = "/data/option-pro"

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr


def test_secret_health_url_uses_machine_file_over_stale_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "MACROLENS_URL=https://stale.example\n",
        encoding="utf-8",
    )
    (tmp_path / "machine.env").write_text(
        "MACROLENS_URL=https://machine.example:9443/\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(personal_secrets, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.delenv("MACROLENS_URL", raising=False)

    assert personal_secrets._macrolens_health_url() == (
        "https://machine.example:9443/internal/v1/health",
        None,
    )
    monkeypatch.setenv("MACROLENS_URL", "")
    assert personal_secrets._macrolens_health_url() == (
        None,
        "macrolens_url_missing",
    )


def _setup_root(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "option-pro"
    (root / "scripts").mkdir(parents=True)
    (root / "config").mkdir()
    for source, destination in (
        (ROOT / "setup.sh", root / "setup.sh"),
        (ROOT / "personal.sh", root / "personal.sh"),
        (ROOT / ".env.example", root / ".env.example"),
        (ROOT / "machine.env.example", root / "machine.env.example"),
        (ROOT / "secrets.env.example", root / "secrets.env.example"),
        (ROOT / "docker-compose.yml", root / "docker-compose.yml"),
        (ROOT / "scripts" / "deploy.sh", root / "scripts" / "deploy.sh"),
        (ROOT / "config" / "personal.toml", root / "config" / "personal.toml"),
    ):
        shutil.copy2(source, destination)
    _copy_deployment_validator(root)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tools(bin_dir)
    environment = _isolated_environment()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    environment["FAKE_CONTAINER_PYTHON"] = sys.executable
    return root, environment


def test_setup_rejects_unsafe_secret_without_expansion_or_echo(tmp_path: Path) -> None:
    root, environment = _setup_root(tmp_path)
    secret = "proxy-${HOME} $ # 'quoted' \"double\" \\ tail"

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input=f"{secret}\n\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    assert "接口密钥包含不支持的字符" in result.stderr
    assert dotenv_values(root / "secrets.env")["OPENAI_API_KEY"] in {None, ""}
    assert not (root / ".fake-order").exists()


def test_setup_separates_and_preserves_a_safe_service_secret(
    tmp_path: Path,
) -> None:
    root, environment = _setup_root(tmp_path)
    secret = "sk-safe_token-1234567890"

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input=f"{secret}\n\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    deployment = (root / ".env").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in deployment
    assert "INTERNAL_API_TOKEN" not in deployment
    assert "APP_PASSWORD_HASH=" not in deployment
    machine = dotenv_values(root / "machine.env")
    assert machine["MACROLENS_URL"] in {None, ""}
    assert dotenv_values(root / "secrets.env")["OPENAI_API_KEY"] == secret
    assert secret not in result.stdout + result.stderr
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "machine.env").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "secrets.env").stat().st_mode) == 0o600


def test_setup_migrates_legacy_machine_values_without_template_overrides(
    tmp_path: Path,
) -> None:
    root, environment = _setup_root(tmp_path)
    token = "legacy-internal-token-never-printed"
    legacy_values = {
        "HOST_BIND": "10.24.5.6",
        "PORT": "3201",
        "MACROLENS_URL": "https://macrolens.example:9443",
        "ALLOWED_HOSTS": "10.24.5.6",
        "TRUST_PROXY_HEADERS": "false",
        "TRUSTED_PROXY_CIDRS": "",
        "DATA_DIR": "/data/legacy-option-pro",
    }
    (root / ".env").write_text(
        "HOST_BIND='10.24.5.6'\n"
        'PORT="3201"\n'
        "MACROLENS_URL='https://macrolens.example:9443'\n"
        "ALLOWED_HOSTS=10.24.5.6\n"
        "TRUST_PROXY_HEADERS=false\n"
        "TRUSTED_PROXY_CIDRS=\n",
        encoding="utf-8",
    )
    (root / "secrets.env").write_text(
        f"INTERNAL_API_TOKEN={token}\n"
        "DATA_DIR=/data/legacy-option-pro\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input="",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert dotenv_values(root / "machine.env") == legacy_values
    assert "旧运行配置中的主机设置已迁移" in result.stdout
    assert "沿用原配置" not in result.stdout
    assert token not in result.stdout + result.stderr
    for name in (".env", "machine.env", "secrets.env"):
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o600


def test_setup_migrates_legacy_url_alias_and_secret_data_directory(
    tmp_path: Path,
) -> None:
    root, environment = _setup_root(tmp_path)
    token = "legacy-owner-token-never-printed"
    (root / ".env").write_text(
        "MACROLENS_BASE_URL=https://legacy-macrolens.example\n",
        encoding="utf-8",
    )
    (root / "secrets.env").write_text(
        f"INTERNAL_API_TOKEN={token}\n"
        "DATA_DIR=/data/legacy-database\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input="",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    machine = dotenv_values(root / "machine.env")
    assert list(machine) == [
        "HOST_BIND",
        "PORT",
        "MACROLENS_URL",
        "ALLOWED_HOSTS",
        "TRUST_PROXY_HEADERS",
        "TRUSTED_PROXY_CIDRS",
        "DATA_DIR",
    ]
    assert machine["HOST_BIND"] == "127.0.0.1"
    assert machine["MACROLENS_URL"] == "https://legacy-macrolens.example"
    assert machine["DATA_DIR"] == "/data/legacy-database"
    assert token not in result.stdout + result.stderr


def test_setup_rejects_conflicting_legacy_macrolens_aliases(
    tmp_path: Path,
) -> None:
    root, environment = _setup_root(tmp_path)
    (root / ".env").write_text(
        "MACROLENS_URL=https://canonical.example\n"
        "MACROLENS_BASE_URL=https://legacy.example\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input="",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert "无法安全迁移旧 .env 中的 MACROLENS_URL" in result.stderr
    assert "canonical.example" not in result.stdout + result.stderr
    assert "legacy.example" not in result.stdout + result.stderr
    assert not (root / "machine.env").exists()


def test_setup_fails_closed_when_a_legacy_machine_value_cannot_be_preserved(
    tmp_path: Path,
) -> None:
    root, environment = _setup_root(tmp_path)
    unsafe = "${UNTRUSTED_HOST_BIND}"
    (root / ".env").write_text(
        f"HOST_BIND={unsafe}\nPORT=3201\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "setup.sh"],
        cwd=root,
        env=environment,
        input="",
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode != 0
    assert "无法安全迁移旧 .env 中的 HOST_BIND" in result.stderr
    assert unsafe not in result.stdout + result.stderr
    assert not (root / "machine.env").exists()
    assert not (root / ".fake-order").exists()


@pytest.mark.parametrize(
    ("key", "expected_services"),
    [
        ("OPENAI_API_KEY", "backend worker"),
        ("FINNHUB_API_KEY", "backend worker"),
        ("MARKETDATA_TOKEN", "backend worker"),
        ("INTERNAL_API_TOKEN", "backend worker"),
        ("APP_PASSWORD_HASH", "backend"),
    ],
)
def test_secret_cli_recreates_only_affected_running_services(
    tmp_path: Path,
    key: str,
    expected_services: str,
) -> None:
    root = tmp_path / "option-pro"
    root.mkdir()
    shutil.copy2(ROOT / "personal.sh", root / "personal.sh")
    shutil.copy2(ROOT / "docker-compose.yml", root / "docker-compose.yml")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > \"$FAKE_PYTHON_ARGS\"\ncat >/dev/null\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[ "$1" = compose ] || exit 2
shift
if [ "${1:-}" = -f ]; then shift 2; fi
case "${1:-}" in
    ps) printf 'running-container\n' ;;
    up) shift; printf '%s\n' "$*" > "$FAKE_RECREATE_LOG" ;;
    *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = _isolated_environment()
    environment.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
            "PYTHON_BIN": str(python),
            "FAKE_PYTHON_ARGS": str(tmp_path / "python-args"),
            "FAKE_RECREATE_LOG": str(tmp_path / "recreate-log"),
        }
    )
    secret = "secret-value-never-in-arguments"

    result = subprocess.run(
        ["bash", "personal.sh", "secrets", "set", key],
        cwd=root,
        env=environment,
        input=f"{secret}\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "recreate-log").read_text(encoding="utf-8").strip() == (
        f"-d --no-deps --force-recreate {expected_services}"
    )
    assert secret not in (tmp_path / "python-args").read_text(encoding="utf-8")
    assert secret not in result.stdout + result.stderr


def test_watchlist_snapshot_uses_the_shared_data_directory_without_auth_tokens(
    tmp_path: Path,
) -> None:
    script = ROOT / "scripts" / "watchlist_snapshot.py"
    source = script.read_text(encoding="utf-8")
    assert "APP_AUTH_TOKEN" not in source
    assert "Authorization" not in source
    assert "WATCHLIST_SNAPSHOT_PATH" not in source
    assert 'os.environ.get("DATA_DIR"' in source
    assert 'data_dir / "watchlist-snapshot-v1.json"' in source

    result = subprocess.run(
        [sys.executable, str(script), "validate"],
        env={**_isolated_environment(), "DATA_DIR": "relative/data"},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "DATA_DIR must be an absolute path" in result.stderr


def test_shell_entrypoints_stay_small_and_syntax_is_valid() -> None:
    for relative in ("setup.sh", "scripts/deploy.sh", "personal.sh"):
        path = ROOT / relative
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 320
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
