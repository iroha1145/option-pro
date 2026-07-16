from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _copy_personal_validator(root: Path) -> None:
    for relative in (
        "backend/app/__init__.py",
        "backend/app/access.py",
        "backend/app/deployment_boundary.py",
        "backend/app/personal_config.py",
        "backend/app/runtime_environment.py",
        "backend/app/services/__init__.py",
        "backend/app/services/request_security.py",
        "backend/app/tools/__init__.py",
        "backend/app/tools/validate_personal_deployment.py",
        "config/personal.toml",
    ):
        source = ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


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
if [ "${1:-}" = "build" ]; then
    printf '%s\\n' "$*" >> .fake-build-log
    touch .fake-backend-image-built
    printf '%s\\n' 'build' >> .fake-deploy-order-log
    exit 0
fi
if [ "${1:-}" = "up" ]; then
    printf '%s\\n' 'up' >> .fake-deploy-order-log
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
if [ "${1:-}" = "exec" ] && [[ " $* " == *" backend python -c "* ]] && [[ " $* " == *"deployment_access_probe"* ]]; then
    printf '%s\n' "$*" > .fake-deployment-access-probe-log
    exit "${FAKE_DEPLOYMENT_ACCESS_PROBE_EXIT:-0}"
fi
if { [ "${1:-}" = "exec" ] || [ "${1:-}" = "run" ]; } && \
   { [[ " $* " == *" backend python - seed"* ]] || \
     [[ " $* " == *" backend python - validate"* ]] || \
     [[ " $* " == *" backend python - wait"* ]]; }; then
    container_mode="$1"
    shift
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -T|--rm|--no-deps)
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
    if [ "${1:-}" != "python" ] || [ "${2:-}" != "-" ] || [ "$#" -lt 3 ]; then
        exit 2
    fi
    shift 2
    watchlist_action="${1:-}"
    case "$watchlist_action" in
        seed|validate|wait)
            ;;
        *)
            exit 2
            ;;
    esac
    if [ -n "${WATCHLIST_SNAPSHOT_ACTION:-}" ] && [ "$WATCHLIST_SNAPSHOT_ACTION" != "$watchlist_action" ]; then
        exit 2
    fi
    if [ "$container_mode" = "exec" ] && [ "$watchlist_action" = "seed" ] && [ "${FAKE_WATCHLIST_EXEC_UNAVAILABLE:-false}" = "true" ]; then
        printf '%s\n' "exec:${watchlist_action}:unavailable" >> .fake-watchlist-container-log
        printf '%s\n' "exec:${watchlist_action}:unavailable" >> .fake-deploy-order-log
        exit 1
    fi
    if [ "$container_mode" = "run" ] && [ ! -f .fake-backend-image-built ]; then
        printf '%s\n' "run:${watchlist_action}:before-build" >> .fake-watchlist-container-log
        printf '%s\n' "run:${watchlist_action}:before-build" >> .fake-deploy-order-log
        exit 1
    fi
    if [ -n "${FAKE_WATCHLIST_SNAPSHOT_PATH:-}" ]; then
        export WATCHLIST_SNAPSHOT_PATH="$FAKE_WATCHLIST_SNAPSHOT_PATH"
    fi
    printf '%s\n' "${container_mode}:${watchlist_action}" >> .fake-watchlist-container-log
    printf '%s\n' "${container_mode}:${watchlist_action}" >> .fake-deploy-order-log
    PYTHONPATH="${FAKE_CATALYST_PYTHONPATH:?}" python3 - "$@"
    exit $?
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
    _copy_personal_validator(root)
    shutil.copy2(
        ROOT / "scripts" / "watchlist_snapshot.py",
        scripts / "watchlist_snapshot.py",
    )
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
import urllib.error
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


def _next_watchlist() -> str:
    sequence_path = os.environ.get("FAKE_WATCHLIST_SEQUENCE", "")
    states = ["fresh"]
    if sequence_path:
        states = [
            line.strip()
            for line in Path(sequence_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not states:
            raise RuntimeError("fake watchlist sequence is empty")
    count_path = Path(
        os.environ.get(
            "FAKE_WATCHLIST_COUNT_FILE",
            str(Path(sequence_path).with_suffix(".count"))
            if sequence_path
            else ".fake-watchlist-count",
        )
    )
    count = int(count_path.read_text(encoding="utf-8")) if count_path.exists() else 0
    count += 1
    count_path.write_text(str(count), encoding="utf-8")
    state = states[min(count - 1, len(states) - 1)]
    if state == "error":
        raise urllib.error.URLError("watchlist unavailable")
    if state == "invalid":
        return __import__("json").dumps(
            {"groups": [], "attempted": 1, "succeeded": 0, "_stale": False}
        )
    if state not in {"fresh", "stale"}:
        raise RuntimeError(f"unknown fake watchlist state: {state}")
    return __import__("json").dumps(
        {
            "groups": [
                {
                    "id": "tech",
                    "name": "科技",
                    "stocks": [
                        {
                            "ticker": "AAPL",
                            "name": "苹果",
                            "price": 200.0,
                            "change_percent": 1.0,
                            "spark": [198.0, 200.0],
                        }
                    ],
                }
            ],
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "_stale": state == "stale",
            "as_of": datetime.now(timezone.utc).isoformat(),
        },
        separators=(",", ":"),
    )


def _urlopen(request, *args, **kwargs):
    url = getattr(request, "full_url", str(request))
    if url == "http://127.0.0.1:8000/api/catalysts/status":
        return io.StringIO(_next_status())
    if url == "http://127.0.0.1:8000/api/stocks/watchlist":
        return io.StringIO(_next_watchlist())
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


@pytest.mark.parametrize("configured_value", ["treu", "on", "off", "enabled"])
def test_removed_public_read_switch_no_longer_changes_owner_access(
    tmp_path: Path,
    configured_value: str,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    legacy = template.replace(
        "PUBLIC_READ_API_ENABLED=false",
        f"PUBLIC_READ_API_ENABLED={configured_value}",
        1,
    )
    root, environment = _deployment_root(tmp_path, legacy)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("app_auth_token", ["", '"   "'])
def test_removed_browser_token_is_not_a_deployment_gate(
    tmp_path: Path,
    app_auth_token: str,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    public_without_token = (
        template.replace(
            "PUBLIC_READ_API_ENABLED=false",
            "PUBLIC_READ_API_ENABLED=true",
            1,
        ).replace("APP_AUTH_TOKEN=", f"APP_AUTH_TOKEN={app_auth_token}", 1)
    )
    root, environment = _deployment_root(tmp_path, public_without_token)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert (root / ".fake-deployment-access-probe-log").exists()


@pytest.mark.parametrize(
    "public_value",
    ["true", '"YES"', "false"],
)
def test_deployment_runs_anonymous_access_probes_for_public_and_private_routes(
    tmp_path: Path,
    public_value: str,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    configured = template.replace(
        "PUBLIC_READ_API_ENABLED=false",
        f"PUBLIC_READ_API_ENABLED={public_value}",
        1,
    ).replace("APP_AUTH_TOKEN=", "APP_AUTH_TOKEN=deployment-token", 1)
    root, environment = _deployment_root(tmp_path, configured)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    probe = (root / ".fake-deployment-access-probe-log").read_text(encoding="utf-8")
    assert "EXPECTED_ACCESS_MODE=private_network" in probe
    assert "EXPECTED_PUBLIC_READ_ENABLED" not in probe
    assert "EXPECTED_AUTH_CONFIGURED" not in probe
    for path in (
        "/api/market/status",
        "/api/breakouts/status",
        "/api/catalysts/status",
    ):
        assert path in probe
    assert '("POST", "/api/ai/jobs/earnings-impact", b"{}")' in probe
    assert 'if access_mode == "password"' in probe


def test_deployment_fails_when_anonymous_access_probe_fails(tmp_path: Path) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    configured = template.replace(
        "PUBLIC_READ_API_ENABLED=false",
        "PUBLIC_READ_API_ENABLED=true",
        1,
    ).replace("APP_AUTH_TOKEN=", "APP_AUTH_TOKEN=deployment-token", 1)
    root, environment = _deployment_root(tmp_path, configured)
    environment["FAKE_DEPLOYMENT_ACCESS_PROBE_EXIT"] = "9"

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert (root / ".fake-deployment-access-probe-log").exists()


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


def test_required_breakout_gate_uses_personal_config_and_allows_interaction_off(
    tmp_path: Path,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    required_disabled = template.replace(
        "DEPLOY_REQUIRE_BREAKOUT=false", "DEPLOY_REQUIRE_BREAKOUT=true"
    )
    root, environment = _deployment_root(tmp_path, required_disabled)
    accepted_with_legacy_false = _run_deploy(root, environment)
    assert accepted_with_legacy_false.returncode == 0, accepted_with_legacy_false.stderr

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
    ["True", "YES", "1", "true # required for production"],
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


@pytest.mark.parametrize("configured_value", ["on", "off"])
def test_runtime_unsupported_boolean_forms_fail_closed(
    tmp_path: Path,
    configured_value: str,
) -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    invalid = template.replace(
        "TRUST_PROXY_HEADERS=false",
        f"TRUST_PROXY_HEADERS={configured_value}",
        1,
    )
    root, environment = _deployment_root(tmp_path, invalid)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "TRUST_PROXY_HEADERS must be a recognized boolean value" in result.stderr


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
        ("action-key", "action-secret", "", None),
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


def _warm_watchlist_environment(tmp_path: Path) -> tuple[str, Path]:
    snapshot = tmp_path / "watchlist-snapshot-v1.json"
    configured = (ROOT / ".env.example").read_text(encoding="utf-8").replace(
        "DEPLOY_WARM_WATCHLIST=false",
        "DEPLOY_WARM_WATCHLIST=true",
        1,
    )
    return configured, snapshot


def test_optional_watchlist_warmup_is_bounded_and_validates_real_quotes() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    helper = (ROOT / "scripts" / "watchlist_snapshot.py").read_text(
        encoding="utf-8"
    )
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "DEPLOY_WARM_WATCHLIST=false" in example
    assert "WATCHLIST_SNAPSHOT_PATH=/data/watchlist-snapshot-v1.json" in example
    assert 'configuration_boolean DEPLOY_WARM_WATCHLIST false' in script
    assert 'if is_truthy "$deploy_warm_watchlist"' in script
    assert 'snapshot_script="${SCRIPT_DIR}/watchlist_snapshot.py"' in script
    assert "backend python - seed" in script
    assert "backend python - validate" in script
    assert "backend python - wait --timeout 120" in script
    assert "docker compose run --rm --no-deps -T" in script
    assert "backend_image_built=false" in script
    assert "build_backend_image()" in script
    assert 'if [ "$backend_image_built" = "true" ]' in script
    assert "inside the shared /data volume" in script
    assert 'WATCHLIST_URL = "http://127.0.0.1:8000/api/stocks/watchlist"' in helper
    assert "fetch_watchlist(timeout=120)" in helper
    assert "succeeded <= 0" in helper
    assert '"watchlist_seed_snapshot": True' in helper
    assert "deadline = time.monotonic() + timeout_seconds" in helper
    assert "attempts = max(1, timeout_seconds // 5 + 1)" in helper
    assert "the bounded snapshot remains active" in helper


def test_watchlist_warm_deploy_seeds_before_build_then_waits_for_fresh_data(
    tmp_path: Path,
) -> None:
    configured, snapshot = _warm_watchlist_environment(tmp_path)
    root, environment = _deployment_root(tmp_path, configured)
    sequence = tmp_path / "watchlist-sequence"
    sequence.write_text("fresh\nstale\nfresh\n", encoding="utf-8")
    count = tmp_path / "watchlist-count"
    environment["FAKE_WATCHLIST_SEQUENCE"] = str(sequence)
    environment["FAKE_WATCHLIST_COUNT_FILE"] = str(count)
    environment["FAKE_WATCHLIST_SNAPSHOT_PATH"] = str(snapshot)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert snapshot.is_file()
    saved = json.loads(snapshot.read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert saved["payload"]["succeeded"] == 1
    assert count.read_text(encoding="utf-8") == "3"
    assert '"watchlist_warm":true' in result.stdout
    assert (root / ".fake-build-log").read_text(encoding="utf-8").splitlines() == [
        "build --pull backend"
    ]
    assert (root / ".fake-deploy-order-log").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "exec:seed",
        "build",
        "up",
        "exec:wait",
    ]


def test_watchlist_warm_deploy_uses_shared_snapshot_when_old_backend_is_unavailable(
    tmp_path: Path,
) -> None:
    configured, snapshot = _warm_watchlist_environment(tmp_path)
    root, environment = _deployment_root(tmp_path, configured)
    snapshot.write_text(
        json.dumps(
            {
                "version": 1,
                "saved_at": time.time(),
                "payload": {
                    "groups": [
                        {
                            "id": "tech",
                            "name": "科技",
                            "stocks": [
                                {
                                    "ticker": "AAPL",
                                    "name": "苹果",
                                    "price": 200.0,
                                    "change_percent": 1.0,
                                    "spark": [198.0, 200.0],
                                }
                            ],
                        }
                    ],
                    "attempted": 1,
                    "succeeded": 1,
                    "failed": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    sequence = tmp_path / "watchlist-sequence"
    sequence.write_text("fresh\n", encoding="utf-8")
    count = tmp_path / "watchlist-count"
    environment["FAKE_WATCHLIST_SEQUENCE"] = str(sequence)
    environment["FAKE_WATCHLIST_COUNT_FILE"] = str(count)
    environment["FAKE_WATCHLIST_SNAPSHOT_PATH"] = str(snapshot)
    environment["FAKE_WATCHLIST_EXEC_UNAVAILABLE"] = "true"

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert snapshot.is_file()
    assert count.read_text(encoding="utf-8") == "1"
    assert (root / ".fake-build-log").read_text(encoding="utf-8").splitlines() == [
        "build --pull backend"
    ]
    assert (root / ".fake-watchlist-container-log").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "exec:seed:unavailable",
        "run:validate",
        "exec:wait",
    ]
    assert (root / ".fake-deploy-order-log").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "exec:seed:unavailable",
        "build",
        "run:validate",
        "up",
        "exec:wait",
    ]
    assert '"source": "existing"' in result.stdout
    assert '"watchlist_warm":true' in result.stdout


def test_watchlist_warm_deploy_stops_before_traffic_switch_without_a_safe_snapshot(
    tmp_path: Path,
) -> None:
    configured, snapshot = _warm_watchlist_environment(tmp_path)
    root, environment = _deployment_root(tmp_path, configured)
    sequence = tmp_path / "watchlist-sequence"
    sequence.write_text("error\n", encoding="utf-8")
    environment["FAKE_WATCHLIST_SEQUENCE"] = str(sequence)
    environment["FAKE_WATCHLIST_SNAPSHOT_PATH"] = str(snapshot)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert not snapshot.exists()
    assert "traffic has not switched" in result.stderr
    assert (root / ".fake-build-log").read_text(encoding="utf-8").splitlines() == [
        "build --pull backend"
    ]
    assert (root / ".fake-deploy-order-log").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "exec:seed",
        "build",
        "run:validate",
    ]


def test_watchlist_warm_deploy_requires_a_shared_volume_snapshot_path(
    tmp_path: Path,
) -> None:
    configured, _snapshot = _warm_watchlist_environment(tmp_path)
    configured = configured.replace(
        "WATCHLIST_SNAPSHOT_PATH=/data/watchlist-snapshot-v1.json",
        "WATCHLIST_SNAPSHOT_PATH=/tmp/watchlist-snapshot-v1.json",
        1,
    )
    root, environment = _deployment_root(tmp_path, configured)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "inside the shared /data volume" in result.stderr
    assert not (root / ".fake-build-log").exists()


def test_watchlist_warm_deploy_rejects_boolean_snapshot_before_traffic_switch(
    tmp_path: Path,
) -> None:
    configured, snapshot = _warm_watchlist_environment(tmp_path)
    root, environment = _deployment_root(tmp_path, configured)
    snapshot.write_text(
        json.dumps(
            {
                "version": True,
                "saved_at": time.time(),
                "payload": {
                    "groups": [
                        {
                            "id": "tech",
                            "name": "科技",
                            "stocks": [
                                {
                                    "ticker": "AAPL",
                                    "name": "苹果",
                                    "price": 200.0,
                                    "change_percent": 1.0,
                                    "spark": [198.0, 200.0],
                                }
                            ],
                        }
                    ],
                    "attempted": 1,
                    "succeeded": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    sequence = tmp_path / "watchlist-sequence"
    sequence.write_text("error\n", encoding="utf-8")
    environment["FAKE_WATCHLIST_SEQUENCE"] = str(sequence)
    environment["FAKE_WATCHLIST_SNAPSHOT_PATH"] = str(snapshot)

    result = _run_deploy(root, environment)

    assert result.returncode != 0
    assert "traffic has not switched" in result.stderr
    assert (root / ".fake-build-log").read_text(encoding="utf-8").splitlines() == [
        "build --pull backend"
    ]
    assert (root / ".fake-deploy-order-log").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "exec:seed",
        "build",
        "run:validate",
    ]


def test_watchlist_post_switch_refresh_failure_keeps_the_seeded_snapshot(
    tmp_path: Path,
) -> None:
    configured, snapshot = _warm_watchlist_environment(tmp_path)
    root, environment = _deployment_root(tmp_path, configured)
    sequence = tmp_path / "watchlist-sequence"
    sequence.write_text("fresh\nerror\n", encoding="utf-8")
    count = tmp_path / "watchlist-count"
    environment["FAKE_WATCHLIST_SEQUENCE"] = str(sequence)
    environment["FAKE_WATCHLIST_COUNT_FILE"] = str(count)
    environment["FAKE_WATCHLIST_SNAPSHOT_PATH"] = str(snapshot)

    result = _run_deploy(root, environment)

    assert result.returncode == 0, result.stderr
    assert snapshot.is_file()
    assert count.read_text(encoding="utf-8") == "26"
    assert "bounded snapshot remains active" in result.stderr
    assert (root / ".fake-build-log").read_text(encoding="utf-8").splitlines() == [
        "build --pull backend"
    ]


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

    legacy_public_read = "\n".join(
        line
        for line in template.splitlines()
        if not line.startswith("PUBLIC_READ_API_ENABLED=")
    )
    (root / ".env").write_text(legacy_public_read, encoding="utf-8")
    result = _run_deploy(root, environment)
    assert result.returncode == 0, result.stderr

    incomplete_proxy = template.replace(
        "TRUST_PROXY_HEADERS=false", "TRUST_PROXY_HEADERS=true"
    )
    (root / ".env").write_text(incomplete_proxy, encoding="utf-8")
    result = _run_deploy(root, environment)
    assert result.returncode != 0
    assert "private_network requires TRUST_PROXY_HEADERS=false" in result.stderr


def test_setup_copies_full_template_and_writes_secrets_as_literal_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "option-pro"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "setup.sh", root / "setup.sh")
    shutil.copy2(ROOT / ".env.example", root / ".env.example")
    shutil.copy2(ROOT / "scripts" / "deploy.sh", scripts / "deploy.sh")
    _copy_personal_validator(root)
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
