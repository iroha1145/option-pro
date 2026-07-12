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

is_truthy() {
    case "$1" in
        1|true|TRUE|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

is_loopback_bind() {
    case "$1" in
        localhost|127.*|::1|'[::1]') return 0 ;;
        *) return 1 ;;
    esac
}

host_bind="${HOST_BIND:-$(env_value HOST_BIND)}"
host_bind="${host_bind:-127.0.0.1}"
auth_token="${APP_AUTH_TOKEN:-$(env_value APP_AUTH_TOKEN)}"
allow_insecure="${ALLOW_INSECURE_PUBLIC_BIND:-$(env_value ALLOW_INSECURE_PUBLIC_BIND)}"
trust_proxy_headers="${TRUST_PROXY_HEADERS:-$(env_value TRUST_PROXY_HEADERS)}"
trusted_proxy_cidrs="${TRUSTED_PROXY_CIDRS:-$(env_value TRUSTED_PROXY_CIDRS)}"
allowed_hosts="${ALLOWED_HOSTS:-$(env_value ALLOWED_HOSTS)}"
breakout_enabled="${BREAKOUT_RADAR_ENABLED:-$(env_value BREAKOUT_RADAR_ENABLED)}"
deploy_require_breakout="${DEPLOY_REQUIRE_BREAKOUT:-$(env_value DEPLOY_REQUIRE_BREAKOUT)}"
deploy_require_breakout="${deploy_require_breakout:-false}"
range_mode="${RANGE_PERSISTENCE_MODE:-$(env_value RANGE_PERSISTENCE_MODE)}"

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
if [ "$range_mode" != "disabled" ] && [ "$range_mode" != "shadow" ] && [ "$range_mode" != "enabled" ]; then
    echo "RANGE_PERSISTENCE_MODE must be disabled, shadow, or enabled." >&2
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
    docker compose logs --tail=200 backend >&2 || true
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

echo "Deployment passed readiness, version, and configured Breakout Radar checks."
