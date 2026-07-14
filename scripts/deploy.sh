#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"
umask 077

trap 'echo "Deployment failed at line ${LINENO}." >&2' ERR

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not installed." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running or is not accessible." >&2
    exit 1
fi
compose_version="$(docker compose version --short | sed 's/^v//')"
IFS=. read -r compose_major compose_minor _compose_patch <<<"$compose_version"
if [ "${compose_major:-0}" -lt 2 ] || {
    [ "${compose_major:-0}" -eq 2 ] && [ "${compose_minor:-0}" -lt 24 ];
}; then
    echo "Docker Compose 2.24 or newer is required; found ${compose_version}." >&2
    exit 1
fi
if [ ! -f .env ]; then
    echo ".env is missing. Copy .env.example to .env and configure it first." >&2
    exit 1
fi
chmod 600 .env

if ! grep -q '^ALLOWED_HOSTS=' .env && [ -z "${ALLOWED_HOSTS+x}" ]; then
    echo "This .env predates the ALLOWED_HOSTS security setting." >&2
    echo "Add ALLOWED_HOSTS= for local-only access, or list every public reverse-proxy domain before deploying." >&2
    exit 1
fi

env_value() {
    local key="$1"
    awk -v key="$key" '
        index($0, key "=") == 1 {
            value = substr($0, length(key) + 2)
            sub(/\r$/, "", value)
            if (length(value) >= 2) {
                first = substr(value, 1, 1)
                last = substr(value, length(value), 1)
                if ((first == "\"" && last == "\"") ||
                    (first == "\047" && last == "\047")) {
                    value = substr(value, 2, length(value) - 2)
                }
            }
        }
        END { print value }
    ' .env
}

compose_env_boolean_value() {
    python3 - "$1" <<'PY'
import re
import sys
from pathlib import Path


key = sys.argv[1]
pattern = re.compile(rf"^{re.escape(key)}\s*=(.*)$")
value = ""


def parse_value(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] not in {"'", '"'}:
        comment = re.search(r"\s+#", raw)
        if comment:
            raw = raw[: comment.start()]
        return raw.strip()

    quote = raw[0]
    decoded = []
    escaped = False
    for index, character in enumerate(raw[1:], start=1):
        if quote == '"' and character == "\\" and not escaped:
            escaped = True
            continue
        if character == quote and not escaped:
            remainder = raw[index + 1 :].strip()
            if remainder and not remainder.startswith("#"):
                return raw
            return "".join(decoded)
        decoded.append(character)
        escaped = False
    return raw


for original in Path(".env").read_text(encoding="utf-8").splitlines():
    line = original.lstrip()
    if line.startswith("export "):
        line = line[7:].lstrip()
    match = pattern.match(line)
    if match:
        value = parse_value(match.group(1))

print(value)
PY
}

boolean_source_value() {
    local key="$1"
    local environment_value=""
    if environment_value="$(printenv "$key")" && [ -n "$environment_value" ]; then
        printf '%s\n' "$environment_value"
        return 0
    fi
    compose_env_boolean_value "$key"
}

normalize_boolean() {
    local key="$1"
    local raw="$2"
    local default_value="$3"
    local normalized=""
    normalized="$(
        printf '%s\n' "$raw" \
            | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
            | tr '[:upper:]' '[:lower:]'
    )"
    case "$normalized" in
        '') printf '%s\n' "$default_value" ;;
        1|true|yes|on) printf 'true\n' ;;
        0|false|no|off) printf 'false\n' ;;
        *)
            echo "${key} must be a recognized boolean value." >&2
            return 1
            ;;
    esac
}

configuration_boolean() {
    local key="$1"
    local default_value="$2"
    local raw=""
    if ! raw="$(boolean_source_value "$key")"; then
        echo "Unable to read ${key} from .env." >&2
        return 1
    fi
    normalize_boolean "$key" "$raw" "$default_value"
}

is_truthy() {
    [ "$1" = "true" ]
}

focus_snapshot_state() {
    FOCUS_WORKER_HEALTH="$1" python3 -c '
import json
import os

try:
    payload = json.loads(os.environ["FOCUS_WORKER_HEALTH"])
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
if not isinstance(payload, dict):
    raise SystemExit(1)
database = payload.get("database")
contract = payload.get("contract")
if not isinstance(database, dict) or not isinstance(contract, dict):
    raise SystemExit(1)
common_health = (
    payload.get("healthy") is True
    and payload.get("enabled") is True
    and payload.get("ready_dependency") is False
    and payload.get("status") in {"ok", "degraded"}
    and contract.get("valid") is True
    and database.get("heartbeat_fresh") is True
    and database.get("lock_live") is True
)
if (
    common_health
    and database.get("latest_snapshot") is not None
    and database.get("snapshot_fresh") is True
):
    raise SystemExit(0)
if (
    common_health
    and payload.get("status") == "degraded"
    and database.get("startup_in_progress") is True
    and database.get("latest_snapshot") is None
    and database.get("snapshot_fresh") is False
):
    raise SystemExit(75)
raise SystemExit(1)
'
}

focus_worker_healthcheck() {
    python3 - "$1" <<'PY'
import subprocess
import sys

try:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "focus-context-producer",
            "python",
            "-m",
            "app.services.catalysts.focus_worker",
            "--healthcheck",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=max(1, int(sys.argv[1])),
    )
except (OSError, subprocess.TimeoutExpired):
    raise SystemExit(124)
if completed.returncode != 0:
    sys.stderr.write(completed.stderr)
    raise SystemExit(completed.returncode)
sys.stdout.write(completed.stdout)
PY
}

file_sha256() {
    python3 - "$1" <<'PY'
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(1)
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY
}

is_loopback_bind() {
    case "$1" in
        localhost|127.*|::1|'[::1]') return 0 ;;
        *) return 1 ;;
    esac
}

is_loopback_url() {
    python3 - "$1" <<'PY'
import ipaddress
import sys
from urllib.parse import urlsplit

try:
    hostname = (urlsplit(sys.argv[1]).hostname or "").rstrip(".").lower()
except ValueError:
    raise SystemExit(1)
if hostname == "localhost" or hostname.endswith(".localhost"):
    raise SystemExit(0)
try:
    address = ipaddress.ip_address(hostname)
except ValueError:
    raise SystemExit(1)
mapped = getattr(address, "ipv4_mapped", None)
raise SystemExit(0 if address.is_loopback or (mapped and mapped.is_loopback) else 1)
PY
}

host_bind="${HOST_BIND:-$(env_value HOST_BIND)}"
host_bind="${host_bind:-127.0.0.1}"
auth_token="${APP_AUTH_TOKEN:-$(env_value APP_AUTH_TOKEN)}"
allow_insecure="$(configuration_boolean ALLOW_INSECURE_PUBLIC_BIND false)"
trust_proxy_headers="$(configuration_boolean TRUST_PROXY_HEADERS false)"
trusted_proxy_cidrs="${TRUSTED_PROXY_CIDRS:-$(env_value TRUSTED_PROXY_CIDRS)}"
allowed_hosts="${ALLOWED_HOSTS:-$(env_value ALLOWED_HOSTS)}"
breakout_enabled="$(configuration_boolean BREAKOUT_RADAR_ENABLED false)"
deploy_require_breakout="$(configuration_boolean DEPLOY_REQUIRE_BREAKOUT false)"
openai_api_key="${OPENAI_API_KEY:-$(env_value OPENAI_API_KEY)}"
deploy_require_ai="$(configuration_boolean DEPLOY_REQUIRE_AI false)"
range_mode="${RANGE_PERSISTENCE_MODE:-$(env_value RANGE_PERSISTENCE_MODE)}"
catalyst_mode="${CATALYST_MODE:-$(env_value CATALYST_MODE)}"
catalyst_mode="${catalyst_mode:-display}"
macrolens_enabled="$(configuration_boolean MACROLENS_ENABLED false)"
macrolens_base_url="${MACROLENS_BASE_URL:-$(env_value MACROLENS_BASE_URL)}"
macrolens_verify_tls="$(configuration_boolean MACROLENS_VERIFY_TLS true)"
macrolens_read_key_id="${MACROLENS_READ_KEY_ID:-$(env_value MACROLENS_READ_KEY_ID)}"
macrolens_read_secret="${MACROLENS_READ_SECRET:-$(env_value MACROLENS_READ_SECRET)}"
macrolens_action_key_id="${MACROLENS_ACTION_KEY_ID:-$(env_value MACROLENS_ACTION_KEY_ID)}"
macrolens_action_secret="${MACROLENS_ACTION_SECRET:-$(env_value MACROLENS_ACTION_SECRET)}"
macrolens_schema_sha256="${MACROLENS_SCHEMA_SHA256:-$(env_value MACROLENS_SCHEMA_SHA256)}"
deploy_require_catalyst="$(configuration_boolean DEPLOY_REQUIRE_CATALYST false)"
deploy_require_catalyst_actions="$(configuration_boolean DEPLOY_REQUIRE_CATALYST_ACTIONS false)"
focus_producer_enabled="$(configuration_boolean FOCUS_PRODUCER_ENABLED false)"
focus_producer_snapshot_grace_seconds="${FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS:-$(env_value FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS)}"
focus_producer_snapshot_grace_seconds="${focus_producer_snapshot_grace_seconds:-120}"
deploy_require_focus="$(configuration_boolean DEPLOY_REQUIRE_FOCUS_PRODUCER false)"

if ! is_loopback_bind "$host_bind" && [ -z "$auth_token" ] && ! is_truthy "$allow_insecure"; then
    echo "Refusing non-loopback HOST_BIND without APP_AUTH_TOKEN." >&2
    echo "Use localhost, set a strong token, or explicitly set ALLOW_INSECURE_PUBLIC_BIND=true for a protected private network." >&2
    exit 1
fi
if is_truthy "$trust_proxy_headers" && [ -z "$trusted_proxy_cidrs" ]; then
    echo "TRUST_PROXY_HEADERS=true requires TRUSTED_PROXY_CIDRS." >&2
    exit 1
fi
if is_truthy "$trust_proxy_headers" && [ -z "$allowed_hosts" ]; then
    echo "TRUST_PROXY_HEADERS=true requires explicit ALLOWED_HOSTS." >&2
    exit 1
fi

if is_truthy "$deploy_require_breakout" && ! is_truthy "$breakout_enabled"; then
    echo "DEPLOY_REQUIRE_BREAKOUT=true requires BREAKOUT_RADAR_ENABLED=true." >&2
    exit 1
fi
if is_truthy "$deploy_require_ai" && [ -z "$openai_api_key" ]; then
    echo "DEPLOY_REQUIRE_AI=true requires OPENAI_API_KEY." >&2
    exit 1
fi
if is_truthy "$deploy_require_ai" && [ -z "$auth_token" ]; then
    echo "DEPLOY_REQUIRE_AI=true requires APP_AUTH_TOKEN for paid-action authentication." >&2
    exit 1
fi
if [ "$range_mode" != "disabled" ] && [ "$range_mode" != "shadow" ] && [ "$range_mode" != "enabled" ]; then
    echo "RANGE_PERSISTENCE_MODE must be disabled, shadow, or enabled." >&2
    exit 1
fi
if [ "$catalyst_mode" != "disabled" ] && [ "$catalyst_mode" != "display" ] && [ "$catalyst_mode" != "shadow" ] && [ "$catalyst_mode" != "enabled" ]; then
    echo "CATALYST_MODE must be disabled, display, shadow, or enabled." >&2
    exit 1
fi
if is_truthy "$deploy_require_catalyst" && \
    ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?DEPLOY_REQUIRE_CATALYST_ACTIONS[[:space:]]*=' .env && \
    [ -z "${DEPLOY_REQUIRE_CATALYST_ACTIONS+x}" ]; then
    echo "This .env predates the explicit Catalyst action deployment gate." >&2
    echo "Add DEPLOY_REQUIRE_CATALYST_ACTIONS=false for read-only rollout or true for action rollout." >&2
    exit 1
fi
if is_truthy "$deploy_require_catalyst_actions" && ! is_truthy "$deploy_require_catalyst"; then
    echo "DEPLOY_REQUIRE_CATALYST_ACTIONS=true requires DEPLOY_REQUIRE_CATALYST=true." >&2
    exit 1
fi
if is_truthy "$deploy_require_catalyst"; then
    if ! is_truthy "$macrolens_enabled"; then
        echo "DEPLOY_REQUIRE_CATALYST=true requires MACROLENS_ENABLED=true." >&2
        exit 1
    fi
    if [ "$catalyst_mode" != "display" ]; then
        echo "DEPLOY_REQUIRE_CATALYST=true requires CATALYST_MODE=display." >&2
        exit 1
    fi
    case "$macrolens_base_url" in
        https://*) ;;
        *)
            echo "DEPLOY_REQUIRE_CATALYST=true requires an HTTPS MACROLENS_BASE_URL." >&2
            exit 1
            ;;
    esac
    if is_loopback_url "$macrolens_base_url"; then
        echo "DEPLOY_REQUIRE_CATALYST=true requires a non-loopback MACROLENS_BASE_URL." >&2
        exit 1
    fi
    if ! is_truthy "$macrolens_verify_tls"; then
        echo "DEPLOY_REQUIRE_CATALYST=true requires MACROLENS_VERIFY_TLS=true." >&2
        exit 1
    fi
    if [ -z "$macrolens_read_key_id" ] || [ -z "$macrolens_read_secret" ]; then
        echo "DEPLOY_REQUIRE_CATALYST=true requires MacroLens read credentials." >&2
        exit 1
    fi
    contract_path="${ROOT_DIR}/contracts/macrolens-option-pro-v2.json"
    if ! reviewed_contract_sha256="$(file_sha256 "$contract_path")"; then
        echo "The reviewed MacroLens integration contract is missing or unreadable." >&2
        exit 1
    fi
    if [ "$macrolens_schema_sha256" != "$reviewed_contract_sha256" ]; then
        echo "MACROLENS_SCHEMA_SHA256 does not match the reviewed integration contract." >&2
        exit 1
    fi
fi
if is_truthy "$deploy_require_catalyst_actions"; then
    if [ -z "$macrolens_action_key_id" ] || [ -z "$macrolens_action_secret" ]; then
        echo "DEPLOY_REQUIRE_CATALYST_ACTIONS=true requires MacroLens action credentials." >&2
        exit 1
    fi
    if [ -z "$auth_token" ]; then
        echo "DEPLOY_REQUIRE_CATALYST_ACTIONS=true requires APP_AUTH_TOKEN for analysis actions." >&2
        exit 1
    fi
fi
if is_truthy "$deploy_require_focus" && ! is_truthy "$focus_producer_enabled"; then
    echo "DEPLOY_REQUIRE_FOCUS_PRODUCER=true requires FOCUS_PRODUCER_ENABLED=true." >&2
    exit 1
fi
case "$focus_producer_snapshot_grace_seconds" in
    ''|*[!0-9]*)
        echo "FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS must be an integer from 30 to 900." >&2
        exit 1
        ;;
esac
if [ "$focus_producer_snapshot_grace_seconds" -lt 30 ] || [ "$focus_producer_snapshot_grace_seconds" -gt 900 ]; then
    echo "FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS must be from 30 to 900." >&2
    exit 1
fi

if git rev-parse --verify HEAD >/dev/null 2>&1; then
    if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
        echo "Refusing to label a dirty working tree as a released Git commit." >&2
        echo "Commit or remove local source changes before deployment." >&2
        exit 1
    fi
    APP_COMMIT="$(git rev-parse --verify HEAD)"
else
    APP_COMMIT="unknown"
fi
APP_VERSION="${APP_VERSION:-${APP_COMMIT:0:12}}"
export APP_COMMIT APP_VERSION

echo "Building Optix Pro ${APP_VERSION} (${APP_COMMIT})"
docker compose build --pull backend

# The old container keeps serving during the build. With frontend files baked
# into the same versioned image, the recreate switches backend and frontend as
# one deployment unit instead of exposing a mixed checkout/image version.
if ! docker compose up -d --no-build --force-recreate --remove-orphans --wait --wait-timeout 180; then
    docker compose ps >&2 || true
    docker compose logs --tail=200 backend ai-worker catalyst-sync-worker focus-context-producer breakout-worker >&2 || true
    exit 1
fi

docker compose exec -T -e "EXPECTED_APP_COMMIT=${APP_COMMIT}" backend python -c '
import json
import http.client
import os
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=5) as response:
    payload = json.load(response)
expected = os.environ["EXPECTED_APP_COMMIT"]
actual = payload.get("app_commit")
if actual != expected:
    raise SystemExit(f"deployed commit mismatch: expected {expected}, got {actual}")
if not payload.get("frontend", {}).get("ready"):
    raise SystemExit("frontend integrity check failed")
for configured_host in os.environ.get("ALLOWED_HOSTS", "").split(","):
    configured_host = configured_host.strip()
    if not configured_host:
        continue
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=5)
    try:
        connection.request("GET", "/ready", headers={"Host": configured_host})
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise SystemExit(
                f"configured public Host {configured_host!r} failed readiness: "
                f"HTTP {response.status}"
            )
    finally:
        connection.close()
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
'

ai_worker_health="$(
    docker compose exec -T ai-worker \
        python -m app.services.ai_jobs.worker --healthcheck
)"
docker compose exec -T \
    -e "AI_WORKER_HEALTH=${ai_worker_health}" \
    -e "DEPLOY_REQUIRE_AI=${deploy_require_ai}" \
    backend python -c '
import json, os, urllib.request
p = json.loads(os.environ["AI_WORKER_HEALTH"])
assert p["healthy"] is True
assert p["model"] == "gpt-5.6-terra"
assert p["reasoning"] == "max"
assert p["execution_mode"] in {"background", "worker_sync"}
assert p["sdk_capability_supported"] is True
assert all(p["methods"].get(name) is True for name in ("create", "retrieve", "cancel"))
headers = {}
token = os.environ.get("APP_AUTH_TOKEN", "").strip()
if token:
    headers["Authorization"] = f"Bearer {token}"
request = urllib.request.Request("http://127.0.0.1:8000/api/ai/status", headers=headers)
with urllib.request.urlopen(request, timeout=5) as response:
    status = json.load(response)
assert status["model"] == "gpt-5.6-terra"
assert status["reasoning"] == "max"
assert status["execution_mode"] in {"background", "worker_sync"}
assert status["sdk_capability_supported"] is True
required = os.environ["DEPLOY_REQUIRE_AI"].lower() in {"1", "true", "yes"}
if required:
    assert p["configured"] is True
    assert p["provider_capability_supported"] is True
    assert p["status"] == "supported"
    assert status["enabled"] is True
    assert status["status"] == "supported"
    assert status["provider_capability_supported"] is True
'

expected_breakout_enabled="$breakout_enabled"
expected_range_mode="$range_mode"
docker compose exec -T \
    -e "EXPECTED_BREAKOUT_ENABLED=${expected_breakout_enabled}" \
    -e "EXPECTED_RANGE_MODE=${expected_range_mode}" \
    backend python -c '
import json
import os
import urllib.request
from app.services.strength.market_shape import MARKET_SHAPE_VERSION

headers = {}
token = os.environ.get("APP_AUTH_TOKEN", "").strip()
if token:
    headers["Authorization"] = f"Bearer {token}"
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/breakouts/status",
    headers=headers,
)
with urllib.request.urlopen(request, timeout=5) as response:
    payload = json.load(response)
expected_enabled = os.environ["EXPECTED_BREAKOUT_ENABLED"].lower() in {
    "1", "true", "yes"
}
if bool(payload.get("enabled")) is not expected_enabled:
    raise SystemExit("breakout enabled state does not match deployment config")
if payload.get("range_persistence_mode") != os.environ["EXPECTED_RANGE_MODE"]:
    raise SystemExit("range persistence mode does not match deployment config")
if payload.get("versions", {}).get("market_shape_version") != MARKET_SHAPE_VERSION:
    raise SystemExit(f"{MARKET_SHAPE_VERSION} is not active")
if payload.get("market_shape_adapter", {}).get("status") != "available":
    raise SystemExit("market shape adapter is unavailable")
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
'

worker_health="$(
    docker compose exec -T breakout-worker \
        python -m app.services.breakouts.worker --healthcheck
)"
docker compose exec -T \
    -e "EXPECTED_BREAKOUT_ENABLED=${expected_breakout_enabled}" \
    -e "DEPLOY_REQUIRE_BREAKOUT=${deploy_require_breakout}" \
    -e "WORKER_HEALTH=${worker_health}" \
    backend python -c '
import json, os
p = json.loads(os.environ["WORKER_HEALTH"])
enabled = os.environ["EXPECTED_BREAKOUT_ENABLED"].lower() in {"1", "true", "yes"}
required = os.environ["DEPLOY_REQUIRE_BREAKOUT"].lower() in {"1", "true", "yes"}
assert p["healthy"] is True
if required:
    assert enabled and p["status"] != "disabled"
elif not enabled:
    assert p["status"] == "disabled"
'

catalyst_deploy_not_before_epoch="0"
if is_truthy "$deploy_require_catalyst"; then
    catalyst_deploy_not_before_epoch="$(
        python3 -c 'import time; print(f"{time.time_ns() / 1_000_000_000:.9f}")'
    )"
    docker compose exec -T catalyst-sync-worker \
        python -m app.services.catalysts.worker --request-refresh
fi

catalyst_worker_health="$(
    docker compose exec -T catalyst-sync-worker \
        python -m app.services.catalysts.worker --healthcheck
)"
docker compose exec -T \
    -e "CATALYST_WORKER_HEALTH=${catalyst_worker_health}" \
    -e "DEPLOY_REQUIRE_CATALYST=${deploy_require_catalyst}" \
    -e "DEPLOY_REQUIRE_CATALYST_ACTIONS=${deploy_require_catalyst_actions}" \
    -e "EXPECTED_CATALYST_ENABLED=${macrolens_enabled}" \
    -e "CATALYST_DEPLOY_NOT_BEFORE_EPOCH=${catalyst_deploy_not_before_epoch}" \
    backend python -c '
import json
import os
import time
import urllib.request
from app.services.catalysts.worker import deployment_status_ready

worker = json.loads(os.environ["CATALYST_WORKER_HEALTH"])
read_required = os.environ["DEPLOY_REQUIRE_CATALYST"].lower() in {"1", "true", "yes"}
actions_required = os.environ["DEPLOY_REQUIRE_CATALYST_ACTIONS"].lower() in {
    "1", "true", "yes"
}
enabled = os.environ["EXPECTED_CATALYST_ENABLED"].lower() in {"1", "true", "yes"}
assert not actions_required or read_required
assert worker["healthy"] is True
if read_required:
    assert enabled is True
    assert worker["enabled"] is True
    assert worker["contract"]["valid"] is True
elif not enabled:
    assert worker["status"] == "disabled"

headers = {}
token = os.environ.get("APP_AUTH_TOKEN", "").strip()
if token:
    headers["Authorization"] = f"Bearer {token}"
payload = None
required_after_epoch = float(os.environ["CATALYST_DEPLOY_NOT_BEFORE_EPOCH"])
attempts = 60 if read_required else 1
for attempt in range(attempts):
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/catalysts/status",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.load(response)
    if not read_required or deployment_status_ready(
        payload,
        required_after_epoch=required_after_epoch,
        actions_required=actions_required,
    ):
        break
    if attempt + 1 < attempts:
        time.sleep(2)

assert payload is not None
assert payload["schema_version"] == "macrolens-option-pro-v2"
assert payload["expected_model"] == "gpt-5.6-terra"
assert payload["expected_reasoning"] == "max"
if read_required:
    assert deployment_status_ready(
        payload,
        required_after_epoch=required_after_epoch,
        actions_required=actions_required,
    )
    assert payload["enabled"] is True
    assert payload["status"] == "active"
    assert payload["remote_status"] in {"ok", "active"}
    assert payload["last_sync_at"] is not None
    assert payload["snapshot_id"] is not None
    assert payload["resync_required"] is False
if actions_required:
    assert payload["analysis_trigger_enabled"] is True
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["reasoning"] == "max"
    assert payload["execution_mode"] in {"background", "worker_sync"}
if not enabled:
    assert payload["status"] == "disabled"
    assert payload["enabled"] is False
'

focus_worker_health=""
if is_truthy "$deploy_require_focus"; then
    focus_poll_seconds=2
    focus_healthcheck_timeout_seconds=5
    focus_waited_seconds=0
    focus_wait_deadline=$((SECONDS + focus_producer_snapshot_grace_seconds))
    focus_snapshot_ready=false
    while true; do
        focus_logical_remaining=$((
            focus_producer_snapshot_grace_seconds - focus_waited_seconds
        ))
        focus_wall_remaining=$((focus_wait_deadline - SECONDS))
        if [ "$focus_logical_remaining" -le 0 ] || [ "$focus_wall_remaining" -le 0 ]; then
            break
        fi
        focus_probe_timeout="$focus_healthcheck_timeout_seconds"
        if [ "$focus_logical_remaining" -lt "$focus_probe_timeout" ]; then
            focus_probe_timeout="$focus_logical_remaining"
        fi
        if [ "$focus_wall_remaining" -lt "$focus_probe_timeout" ]; then
            focus_probe_timeout="$focus_wall_remaining"
        fi
        if ! focus_worker_health="$(
            focus_worker_healthcheck "$focus_probe_timeout"
        )"; then
            echo "Required Focus Producer healthcheck failed before the first fresh snapshot." >&2
            exit 1
        fi
        if focus_snapshot_state "$focus_worker_health"; then
            focus_snapshot_ready=true
            break
        else
            focus_snapshot_status=$?
        fi
        if [ "$focus_snapshot_status" -ne 75 ]; then
            echo "Required Focus Producer reported a non-startup state before the first fresh snapshot." >&2
            exit 1
        fi
        focus_logical_remaining=$((
            focus_producer_snapshot_grace_seconds - focus_waited_seconds
        ))
        focus_wall_remaining=$((focus_wait_deadline - SECONDS))
        if [ "$focus_logical_remaining" -le 0 ] || [ "$focus_wall_remaining" -le 0 ]; then
            break
        fi
        focus_sleep_seconds="$focus_poll_seconds"
        if [ "$focus_logical_remaining" -lt "$focus_sleep_seconds" ]; then
            focus_sleep_seconds="$focus_logical_remaining"
        fi
        if [ "$focus_wall_remaining" -lt "$focus_sleep_seconds" ]; then
            focus_sleep_seconds="$focus_wall_remaining"
        fi
        sleep "$focus_sleep_seconds"
        focus_waited_seconds=$((focus_waited_seconds + focus_sleep_seconds))
    done
    if ! is_truthy "$focus_snapshot_ready"; then
        echo "Required Focus Producer did not publish a fresh snapshot within ${focus_producer_snapshot_grace_seconds}s." >&2
        exit 1
    fi
else
    focus_worker_health="$(
        docker compose exec -T focus-context-producer \
            python -m app.services.catalysts.focus_worker --healthcheck
    )"
fi
docker compose exec -T \
    -e "FOCUS_WORKER_HEALTH=${focus_worker_health}" \
    -e "EXPECTED_FOCUS_ENABLED=${focus_producer_enabled}" \
    -e "DEPLOY_REQUIRE_FOCUS=${deploy_require_focus}" \
    backend python -c '
import json, os
p = json.loads(os.environ["FOCUS_WORKER_HEALTH"])
enabled = os.environ["EXPECTED_FOCUS_ENABLED"].lower() in {"1", "true", "yes"}
required = os.environ["DEPLOY_REQUIRE_FOCUS"].lower() in {"1", "true", "yes"}
assert p["ready_dependency"] is False
if required:
    assert enabled is True
    assert p["enabled"] is True
    assert p["healthy"] is True
    assert p["status"] in {"ok", "degraded"}
    assert p["contract"]["valid"] is True
    assert p["database"]["heartbeat_fresh"] is True
    assert p["database"]["latest_snapshot"] is not None
    assert p["database"]["snapshot_fresh"] is True
elif not enabled:
    assert p["status"] == "disabled"
    assert p["healthy"] is True
'

echo "Deployment passed readiness, version, AI, Catalyst, focus producer, and configured Breakout Radar checks."
