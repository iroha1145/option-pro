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
if [ "${1:-}" = "exec" ] && [[ " $* " == *" focus-context-producer "*" --healthcheck "* ]]; then
    sequence_file=".fake-focus-health-sequence"
    count_file=".fake-focus-health-count"
    if [ -f "$sequence_file" ]; then
        count=0
        if [ -f "$count_file" ]; then
            count="$(cat "$count_file")"
        fi
        count=$((count + 1))
        printf '%s\\n' "$count" > "$count_file"
        payload="$(sed -n "${count}p" "$sequence_file")"
        if [ -z "$payload" ]; then
            payload="$(tail -n 1 "$sequence_file")"
        fi
        if [ "${FAKE_FOCUS_HEALTH_HANG:-false}" = "true" ]; then
            exec /bin/sleep 60
        fi
        printf '%s\\n' "$payload"
    else
        printf '{"healthy":true,"status":"disabled","enabled":false,"ready_dependency":false}\\n'
    fi
    exit 0
fi
if [ "${1:-}" = "exec" ] && [[ " $* " == *" catalyst-sync-worker "*" --request-refresh "* ]]; then
    printf '%s\\n' 'requested' >> .fake-catalyst-refresh-log
    printf '{"status":"refresh_requested","streams":["health","feed"],"remote_checked":false}\\n'
    exit 0
fi
if [ "${1:-}" = "exec" ] && [[ " $* " == *" catalyst-sync-worker "*" --healthcheck "* ]]; then
    enabled="$(awk -F= '$1 == "MACROLENS_ENABLED" {print $2}' .env | tail -n 1)"
    case "$enabled" in
        1|true|TRUE|yes|YES)
            printf '{"healthy":true,"status":"ok","enabled":true,"contract":{"valid":true}}\\n'
            ;;
        *)
            printf '{"healthy":true,"status":"disabled","enabled":false,"contract":{"valid":null}}\\n'
            ;;
    esac
    exit 0
fi
if [ "${1:-}" = "exec" ] && [[ " $* " == *" backend python -c "* ]] && [[ " $* " == *"deployment_status_ready"* ]]; then
    shift
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -T)
                shift
                ;;
            -e)
                export "$2"
                shift 2
                ;;
            backend)
                shift
                break
                ;;
            *)
                shift
                ;;
        esac
    done
    if [ "${1:-}" != "python" ] || [ "${2:-}" != "-c" ] || [ "$#" -ne 3 ]; then
        exit 2
    fi
    if [ -z "${FAKE_CATALYST_PYTHONPATH:-}" ]; then
        exit 0
    fi
    PYTHONPATH="${FAKE_CATALYST_PYTHONPATH:?}" python3 -c "$3"
    exit $?
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
    contracts = root / "contracts"
    contracts.mkdir()
    shutil.copy2(
        ROOT / "contracts" / "macrolens-option-pro-v2.json",
        contracts / "macrolens-option-pro-v2.json",
    )
    (root / ".env").write_text(env_text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_docker(bin_dir)
    environment = _isolated_environment()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    fake_python = tmp_path / "fake-python"
    fake_python.mkdir()
    (fake_python / "sitecustomize.py").write_text(
        """from __future__ import annotations

import io
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


_real_urlopen = urllib.request.urlopen


def _timestamp(offset_seconds: float) -> str:
    cutoff = float(os.environ.get("CATALYST_DEPLOY_NOT_BEFORE_EPOCH", "0"))
    value = datetime.fromtimestamp(cutoff + offset_seconds, tz=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _active_payload(state: str) -> str:
    health_at = _timestamp(1 if state in {"health_only", "ready"} else -1)
    feed_at = _timestamp(1 if state == "ready" else -1)
    return __import__("json").dumps(
        {
            "enabled": True,
            "status": "active",
            "remote_status": "ok",
            "last_sync_at": feed_at,
            "snapshot_id": "snapshot-current",
            "resync_required": False,
            "analysis_trigger_enabled": True,
            "model": "gpt-5.6-terra",
            "reasoning": "max",
            "execution_mode": "background",
            "expected_model": "gpt-5.6-terra",
            "expected_reasoning": "max",
            "schema_version": "macrolens-option-pro-v2",
            "streams": {
                "health": {"last_success_at": health_at},
                "feed": {"last_success_at": feed_at},
            },
        },
        separators=(",", ":"),
    )


def _disabled_payload() -> str:
    return __import__("json").dumps(
        {
            "enabled": False,
            "status": "disabled",
            "remote_status": None,
            "last_sync_at": None,
            "snapshot_id": None,
            "resync_required": False,
            "analysis_trigger_enabled": False,
            "model": None,
            "reasoning": None,
            "execution_mode": None,
            "expected_model": "gpt-5.6-terra",
            "expected_reasoning": "max",
            "schema_version": "macrolens-option-pro-v2",
            "streams": {},
        },
        separators=(",", ":"),
    )


def _next_status() -> str:
    sequence_path = os.environ.get("FAKE_CATALYST_STATUS_SEQUENCE", "")
    if not sequence_path:
        enabled = os.environ.get("EXPECTED_CATALYST_ENABLED", "false") == "true"
        return _active_payload("ready") if enabled else _disabled_payload()

    states = [
        line.strip()
        for line in Path(sequence_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not states:
        raise RuntimeError("fake Catalyst status sequence is empty")
    count_path = Path(
        os.environ.get(
            "FAKE_CATALYST_STATUS_COUNT_FILE",
            str(Path(sequence_path).with_suffix(".count")),
        )
    )
    count = int(count_path.read_text(encoding="utf-8")) if count_path.exists() else 0
    count += 1
    count_path.write_text(str(count), encoding="utf-8")
    return _active_payload(states[min(count - 1, len(states) - 1)])


def _urlopen(request, *args, **kwargs):
    url = getattr(request, "full_url", str(request))
    if url == "http://127.0.0.1:8000/api/catalysts/status":
        return io.StringIO(_next_status())
    return _real_urlopen(request, *args, **kwargs)


urllib.request.urlopen = _urlopen
if os.environ.get("FAKE_CATALYST_NO_SLEEP") == "true":
    time.sleep = lambda _seconds: None
""",
        encoding="utf-8",
    )
    environment["FAKE_CATALYST_PYTHONPATH"] = os.pathsep.join(
        (str(fake_python), str(ROOT / "backend"))
    )
    environment["FAKE_CATALYST_NO_SLEEP"] = "true"
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


def _required_focus_environment(grace_seconds: int = 30) -> str:
    return (
        (ROOT / ".env.example")
        .read_text(encoding="utf-8")
        .replace("FOCUS_PRODUCER_ENABLED=false", "FOCUS_PRODUCER_ENABLED=true")
        .replace(
            "FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS=120",
            f"FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS={grace_seconds}",
        )
        .replace(
            "DEPLOY_REQUIRE_FOCUS_PRODUCER=false",
            "DEPLOY_REQUIRE_FOCUS_PRODUCER=true",
        )
    )


def _required_catalyst_read_environment(
    base_url: str = "https://macro.example",
) -> str:
    return (
        (ROOT / ".env.example")
        .read_text(encoding="utf-8")
        .replace("MACROLENS_ENABLED=false", "MACROLENS_ENABLED=true")
        .replace("MACROLENS_BASE_URL=", f"MACROLENS_BASE_URL={base_url}", 1)
        .replace("MACROLENS_READ_KEY_ID=", "MACROLENS_READ_KEY_ID=read-key", 1)
        .replace("MACROLENS_READ_SECRET=", "MACROLENS_READ_SECRET=read-secret", 1)
        .replace("DEPLOY_REQUIRE_CATALYST=false", "DEPLOY_REQUIRE_CATALYST=true", 1)
    )


def _focus_health_payload(*, state: str) -> str:
    startup = state == "startup"
    ready = state == "ready"
    healthy = startup or ready
    return json.dumps(
        {
            "healthy": healthy,
            "status": "degraded" if startup else "ok" if ready else "unhealthy",
            "enabled": True,
            "ready_dependency": False,
            "contract": {"valid": True},
            "database": {
                "heartbeat_fresh": healthy,
                "lock_live": healthy,
                "startup_in_progress": startup,
                "latest_snapshot": {"revision": 1} if ready else None,
                "snapshot_fresh": ready,
            },
        },
        separators=(",", ":"),
    )


def _install_recording_sleep(root: Path, environment: dict[str, str]) -> Path:
    bin_dir = Path(environment["PATH"].split(os.pathsep, 1)[0])
    sleep_log = root / ".fake-sleep-log"
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"${FAKE_SLEEP_LOG:?}\"\n",
        encoding="utf-8",
    )
    fake_sleep.chmod(0o755)
    environment["FAKE_SLEEP_LOG"] = str(sleep_log)
    return sleep_log


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


def test_required_focus_gate_waits_for_first_fresh_snapshot(tmp_path: Path) -> None:
    root, environment = _deployment_root(
        tmp_path, _required_focus_environment(grace_seconds=30)
    )
    sleep_log = _install_recording_sleep(root, environment)
    (root / ".fake-focus-health-sequence").write_text(
        "\n".join(
            [
                _focus_health_payload(state="startup"),
                _focus_health_payload(state="ready"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    health_count = (root / ".fake-focus-health-count").read_text(encoding="utf-8")
    assert health_count.strip() == "2"
    assert sleep_log.read_text(encoding="utf-8").splitlines() == ["2"]


def test_required_focus_gate_times_out_at_the_configured_grace(tmp_path: Path) -> None:
    root, environment = _deployment_root(
        tmp_path, _required_focus_environment(grace_seconds=31)
    )
    sleep_log = _install_recording_sleep(root, environment)
    (root / ".fake-focus-health-sequence").write_text(
        _focus_health_payload(state="startup") + "\n",
        encoding="utf-8",
    )

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "did not publish a fresh snapshot within 31s" in result.stderr
    health_count = (root / ".fake-focus-health-count").read_text(encoding="utf-8")
    assert health_count.strip() == "16"
    assert sleep_log.read_text(encoding="utf-8").splitlines() == ["2"] * 15 + ["1"]


def test_required_focus_gate_rejects_non_startup_state_without_waiting(
    tmp_path: Path,
) -> None:
    root, environment = _deployment_root(
        tmp_path, _required_focus_environment(grace_seconds=30)
    )
    sleep_log = _install_recording_sleep(root, environment)
    (root / ".fake-focus-health-sequence").write_text(
        _focus_health_payload(state="unhealthy") + "\n",
        encoding="utf-8",
    )

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "reported a non-startup state" in result.stderr
    health_count = (root / ".fake-focus-health-count").read_text(encoding="utf-8")
    assert health_count.strip() == "1"
    assert not sleep_log.exists()


def test_required_focus_gate_times_out_a_hung_healthcheck(tmp_path: Path) -> None:
    root, environment = _deployment_root(
        tmp_path, _required_focus_environment(grace_seconds=30)
    )
    sleep_log = _install_recording_sleep(root, environment)
    environment["FAKE_FOCUS_HEALTH_HANG"] = "true"
    (root / ".fake-focus-health-sequence").write_text(
        _focus_health_payload(state="startup") + "\n",
        encoding="utf-8",
    )

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "healthcheck failed before the first fresh snapshot" in result.stderr
    health_count = (root / ".fake-focus-health-count").read_text(encoding="utf-8")
    assert health_count.strip() == "1"
    assert not sleep_log.exists()


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
    required = _required_catalyst_read_environment(base_url)
    root, environment = _deployment_root(tmp_path, required)
    result = _run_deploy(root, environment)
    assert result.returncode != 0
    assert "requires a non-loopback MACROLENS_BASE_URL" in result.stderr


def test_required_catalyst_read_gate_does_not_require_action_config(
    tmp_path: Path,
) -> None:
    required = _required_catalyst_read_environment()
    assert "MACROLENS_ACTION_KEY_ID=\n" in required
    assert "MACROLENS_ACTION_SECRET=\n" in required
    assert "APP_AUTH_TOKEN=\n" in required
    assert "DEPLOY_REQUIRE_CATALYST_ACTIONS=false" in required

    root, environment = _deployment_root(tmp_path, required)
    accepted = _run_deploy(root, environment)
    assert accepted.returncode == 0, accepted.stderr
    refresh_log = root / ".fake-catalyst-refresh-log"
    assert refresh_log.read_text(encoding="utf-8").splitlines() == ["requested"]


@pytest.mark.parametrize(
    "configured_value",
    ["True", "YES", "on", "true # required for production"],
)
def test_catalyst_read_gate_accepts_compose_boolean_forms_without_silent_disable(
    tmp_path: Path,
    configured_value: str,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    required_but_disabled = template.replace(
        "DEPLOY_REQUIRE_CATALYST=false",
        f"DEPLOY_REQUIRE_CATALYST={configured_value}",
        1,
    )
    root, environment = _deployment_root(tmp_path, required_but_disabled)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "requires MACROLENS_ENABLED=true" in result.stderr


@pytest.mark.parametrize(
    "key",
    [
        "DEPLOY_REQUIRE_CATALYST",
        "DEPLOY_REQUIRE_CATALYST_ACTIONS",
        "MACROLENS_ENABLED",
    ],
)
def test_unknown_catalyst_boolean_fails_closed(
    tmp_path: Path,
    key: str,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    invalid = template.replace(f"{key}=false", f"{key}=treu", 1)
    root, environment = _deployment_root(tmp_path, invalid)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert f"{key} must be a recognized boolean value" in result.stderr


def test_required_catalyst_actions_gate_requires_read_gate(tmp_path: Path) -> None:
    actions_only = (ROOT / ".env.example").read_text(encoding="utf-8").replace(
        "DEPLOY_REQUIRE_CATALYST_ACTIONS=false",
        "DEPLOY_REQUIRE_CATALYST_ACTIONS=true",
        1,
    )
    root, environment = _deployment_root(tmp_path, actions_only)
    rejected = _run_deploy(root, environment)
    assert rejected.returncode != 0
    assert (
        "DEPLOY_REQUIRE_CATALYST_ACTIONS=true requires "
        "DEPLOY_REQUIRE_CATALYST=true"
    ) in rejected.stderr


def test_required_catalyst_actions_gate_is_case_insensitive(tmp_path: Path) -> None:
    actions_only = (ROOT / ".env.example").read_text(encoding="utf-8").replace(
        "DEPLOY_REQUIRE_CATALYST_ACTIONS=false",
        "DEPLOY_REQUIRE_CATALYST_ACTIONS=True # explicit action rollout",
        1,
    )
    root, environment = _deployment_root(tmp_path, actions_only)
    rejected = _run_deploy(root, environment)
    assert rejected.returncode != 0
    assert (
        "DEPLOY_REQUIRE_CATALYST_ACTIONS=true requires "
        "DEPLOY_REQUIRE_CATALYST=true"
    ) in rejected.stderr


def test_required_catalyst_read_gate_requires_explicit_action_decision(
    tmp_path: Path,
) -> None:
    legacy = "\n".join(
        line
        for line in _required_catalyst_read_environment().splitlines()
        if not line.startswith("DEPLOY_REQUIRE_CATALYST_ACTIONS=")
    )
    root, environment = _deployment_root(tmp_path, legacy + "\n")
    rejected = _run_deploy(root, environment)
    assert rejected.returncode != 0
    assert "predates the explicit Catalyst action deployment gate" in rejected.stderr


@pytest.mark.parametrize(
    ("action_key_id", "action_secret", "app_auth_token", "expected_error"),
    [
        ("", "action-secret", "app-token", "requires MacroLens action credentials"),
        ("action-key", "", "app-token", "requires MacroLens action credentials"),
        ("action-key", "action-secret", "", "requires APP_AUTH_TOKEN"),
        ("action-key", "action-secret", "app-token", None),
    ],
)
def test_required_catalyst_actions_gate_validates_action_config(
    tmp_path: Path,
    action_key_id: str,
    action_secret: str,
    app_auth_token: str,
    expected_error: str | None,
) -> None:
    required = (
        _required_catalyst_read_environment()
        .replace(
            "DEPLOY_REQUIRE_CATALYST_ACTIONS=false",
            "DEPLOY_REQUIRE_CATALYST_ACTIONS=true",
            1,
        )
        .replace(
            "MACROLENS_ACTION_KEY_ID=",
            f"MACROLENS_ACTION_KEY_ID={action_key_id}",
            1,
        )
        .replace(
            "MACROLENS_ACTION_SECRET=",
            f"MACROLENS_ACTION_SECRET={action_secret}",
            1,
        )
        .replace("APP_AUTH_TOKEN=", f"APP_AUTH_TOKEN={app_auth_token}", 1)
    )
    root, environment = _deployment_root(tmp_path, required)
    result = _run_deploy(root, environment)
    if expected_error is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert expected_error in result.stderr


def test_required_catalyst_gate_uses_the_committed_contract_digest(
    tmp_path: Path,
) -> None:
    required = _required_catalyst_read_environment()
    root, environment = _deployment_root(tmp_path, required)
    accepted = _run_deploy(root, environment)
    assert accepted.returncode == 0, accepted.stderr

    mismatched = "\n".join(
        "MACROLENS_SCHEMA_SHA256=" + "0" * 64
        if line.startswith("MACROLENS_SCHEMA_SHA256=")
        else line
        for line in required.splitlines()
    )
    (root / ".env").write_text(mismatched + "\n", encoding="utf-8")
    rejected = _run_deploy(root, environment)
    assert rejected.returncode != 0
    assert "does not match the reviewed integration contract" in rejected.stderr


def test_required_catalyst_runtime_gate_requires_fresh_active_snapshot() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "--request-refresh" in script
    assert "CATALYST_DEPLOY_NOT_BEFORE_EPOCH" in script
    assert "deployment_status_ready" in script
    assert "required_after_epoch=required_after_epoch" in script


def test_catalyst_runtime_gate_waits_for_current_health_and_feed(
    tmp_path: Path,
) -> None:
    root, environment = _deployment_root(
        tmp_path,
        _required_catalyst_read_environment(),
    )
    sequence = root / ".fake-catalyst-status-sequence"
    sequence.write_text("old\nhealth_only\nready\n", encoding="utf-8")
    count_file = root / ".fake-catalyst-status-count"
    environment["FAKE_CATALYST_STATUS_SEQUENCE"] = str(sequence)
    environment["FAKE_CATALYST_STATUS_COUNT_FILE"] = str(count_file)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert count_file.read_text(encoding="utf-8") == "3"


def test_catalyst_runtime_gate_fails_after_sixty_stale_statuses(
    tmp_path: Path,
) -> None:
    root, environment = _deployment_root(
        tmp_path,
        _required_catalyst_read_environment(),
    )
    sequence = root / ".fake-catalyst-status-sequence"
    sequence.write_text("old\n", encoding="utf-8")
    count_file = root / ".fake-catalyst-status-count"
    environment["FAKE_CATALYST_STATUS_SEQUENCE"] = str(sequence)
    environment["FAKE_CATALYST_STATUS_COUNT_FILE"] = str(count_file)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert count_file.read_text(encoding="utf-8") == "60"


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
    assert "DEPLOY_REQUIRE_CATALYST=false" in generated
    assert "DEPLOY_REQUIRE_CATALYST_ACTIONS=false" in generated
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
