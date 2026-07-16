#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"
umask 077

trap 'echo "Deployment failed at line ${LINENO}." >&2' ERR

fail() {
    echo "$1" >&2
    exit 1
}

require_tools() {
    command -v docker >/dev/null 2>&1 || fail "Docker is not installed."
    command -v python3 >/dev/null 2>&1 || fail "Python 3 is not installed."
    docker info >/dev/null 2>&1 || fail "Docker is not running or is not accessible."
    local version major minor
    version="$(docker compose version --short | sed 's/^v//')"
    IFS=. read -r major minor _ <<<"$version"
    if [ "${major:-0}" -lt 2 ] || {
        [ "${major:-0}" -eq 2 ] && [ "${minor:-0}" -lt 24 ];
    }; then
        fail "Docker Compose 2.24 or newer is required; found ${version}."
    fi
}

prepare_runtime_files() {
    [ -f .env ] || fail ".env is missing. Copy .env.example to .env first."
    [ -f machine.env ] ||
        fail "machine.env is missing. Copy machine.env.example to machine.env first."
    chmod 600 .env
    chmod 600 machine.env
    export COMPOSE_ENV_FILES=".env,machine.env"
    if [ -f secrets.env ]; then
        chmod 600 secrets.env
    fi
}

validate_runtime_boundary() {
    local report
    report=""
    if ! report="$(
        docker compose run --rm --no-deps -T backend \
            python -m app.tools.validate_personal_deployment
    )"; then
        printf '%s\n' "$report" >&2
        fail "Personal deployment boundary validation failed."
    fi
    printf '%s\n' "$report"
}

release_identity() {
    if git rev-parse --verify HEAD >/dev/null 2>&1; then
        local personal_status worktree_status
        git ls-files --error-unmatch -- config/personal.toml >/dev/null 2>&1 ||
            fail "config/personal.toml must remain tracked."
        [ -f config/personal.toml ] && [ ! -L config/personal.toml ] ||
            fail "config/personal.toml must remain a regular file."
        if ! personal_status="$(
            git status --porcelain=v1 --untracked-files=normal -- \
                config/personal.toml
        )"; then
            fail "Unable to inspect config/personal.toml."
        fi
        case "$personal_status" in
            ""|" M config/personal.toml"|"M  config/personal.toml"|"MM config/personal.toml") ;;
            *) fail "Only content changes are allowed in config/personal.toml." ;;
        esac
        if ! worktree_status="$(
            git status --porcelain=v1 --untracked-files=normal -- \
                . ':(top,exclude)config/personal.toml'
        )"; then
            fail "Unable to inspect the working tree."
        fi
        [ -z "$worktree_status" ] ||
            fail "Refusing to deploy a dirty working tree."
        APP_COMMIT="$(git rev-parse --verify HEAD)"
    else
        APP_COMMIT=unknown
    fi
    APP_VERSION="${APP_VERSION:-${APP_COMMIT:0:12}}"
    export APP_COMMIT APP_VERSION
}

stop_legacy_workers() {
    local project_name service container_id running
    local -a legacy_services legacy_ids
    legacy_services=(ai-worker catalyst-sync-worker focus-context-producer breakout-worker)
    project_name="$(
        docker compose config --format json |
            python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])'
    )"
    legacy_ids=()
    for service in "${legacy_services[@]}"; do
        while IFS= read -r container_id; do
            [ -n "$container_id" ] && legacy_ids+=("$container_id")
        done < <(
            docker ps --quiet \
                --filter "label=com.docker.compose.project=${project_name}" \
                --filter "label=com.docker.compose.service=${service}"
        )
    done
    if [ "${#legacy_ids[@]}" -eq 0 ]; then
        return
    fi
    echo "Stopping legacy workers before the unified worker starts."
    docker stop --time 2100 "${legacy_ids[@]}" >/dev/null
    for container_id in "${legacy_ids[@]}"; do
        running="$(
            docker inspect --format '{{.State.Running}}' "$container_id" \
                2>/dev/null || printf 'false'
        )"
        [ "$running" != true ] || fail "Legacy worker ${container_id} is still running."
    done
}

verify_backend() {
    docker compose exec -T -e "EXPECTED_APP_COMMIT=${APP_COMMIT}" backend python - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=5) as response:
    payload = json.load(response)
if payload.get("app_commit") != os.environ["EXPECTED_APP_COMMIT"]:
    raise SystemExit("deployed commit does not match the requested release")
if payload.get("status") != "ready" or not payload.get("frontend", {}).get("ready"):
    raise SystemExit("backend or frontend is not ready")
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
PY
}

worker_payload_is_ready() {
    WORKER_HEALTH="$1" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["WORKER_HEALTH"])
expected = {
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
actual = {item.get("task_name") for item in payload.get("tasks", [])}
if payload.get("healthy") is not True:
    raise SystemExit(1)
if payload.get("schema_version") != "optix-worker-v2":
    raise SystemExit(1)
if actual != expected:
    raise SystemExit(1)
PY
}

verify_worker() {
    local attempt payload=""
    for attempt in $(seq 1 60); do
        if payload="$(
            docker compose exec -T worker python -m app.worker --healthcheck \
                2>/dev/null
        )" && worker_payload_is_ready "$payload"; then
            printf '%s\n' "$payload"
            return
        fi
        sleep 2
    done
    printf '%s\n' "$payload" >&2
    fail "Unified worker did not report all nine task types."
}

main() {
    require_tools
    prepare_runtime_files
    release_identity
    docker compose config -q

    echo "Building Optix Pro ${APP_VERSION} (${APP_COMMIT})."
    docker compose build --pull backend
    validate_runtime_boundary
    stop_legacy_workers

    if ! docker compose up -d --no-build --force-recreate --remove-orphans --wait --wait-timeout 180; then
        docker compose ps >&2 || true
        docker compose logs --tail=200 backend worker >&2 || true
        exit 1
    fi

    verify_backend
    verify_worker
    echo "Deployment verified: backend and unified worker are ready."
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
