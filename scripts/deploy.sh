#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"
umask 077
operation_lock_dir="$ROOT_DIR/.personal-operation.lock"
operation_lock_owned=false

trap 'echo "Deployment failed at line ${LINENO}." >&2' ERR

release_operation_lock() {
    if [ "$operation_lock_owned" = true ]; then
        rmdir "$operation_lock_dir" 2>/dev/null || true
        operation_lock_owned=false
    fi
}

acquire_operation_lock() {
    if ! mkdir -m 700 "$operation_lock_dir" 2>/dev/null; then
        echo "Another deployment or Personal command is running. If none is active, remove the stale .personal-operation.lock directory." >&2
        return 1
    fi
    operation_lock_owned=true
}

trap release_operation_lock EXIT

fail() {
    echo "$1" >&2
    exit 1
}

compose() {
    "$ROOT_DIR/scripts/compose.sh" "$@"
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
    if [ -f secrets.env ]; then
        chmod 600 secrets.env
    fi
}

validate_runtime_boundary() {
    local report
    report=""
    if ! report="$(
        compose run --rm --no-deps -T backend \
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
        compose config --format json |
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
    compose exec -T -e "EXPECTED_APP_COMMIT=${APP_COMMIT}" backend python - <<'PY'
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
    "public_home",
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
            compose exec -T worker python -m app.worker --healthcheck \
                2>/dev/null
        )" && worker_payload_is_ready "$payload"; then
            printf '%s\n' "$payload"
            return
        fi
        sleep 2
    done
    printf '%s\n' "$payload" >&2
    fail "Unified worker did not report all ten task types."
}

verify_public_snapshots() {
    local attempt report=""
    for attempt in $(seq 1 450); do
        if report="$(compose exec -T worker python - <<'PY' 2>/dev/null
import json
import time

from app.personal_config import get_personal_config

if get_personal_config().access.mode != "password":
    print(
        json.dumps(
            {
                "required": False,
                "reason": "private_network",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    raise SystemExit(0)

from app.api.stocks import _read_watchlist_snapshot
from app.data_paths import get_data_paths
from app.public_home_snapshot import (
    PUBLIC_HOME_RESOURCE_ORDER,
    public_home_entry_is_servable,
    public_home_resource_parameters,
    read_public_home_entries,
)

now = time.time()
paths = get_data_paths()
entries = read_public_home_entries(paths.public_home_snapshot, now=now)
unavailable = [
    resource
    for resource in PUBLIC_HOME_RESOURCE_ORDER
    if not public_home_entry_is_servable(
        resource,
        entries.get(resource),
        parameters=public_home_resource_parameters(resource, now=now),
        now=now,
    )
]
watchlist = _read_watchlist_snapshot(paths.watchlist_snapshot, now=now)
if watchlist is None or unavailable:
    print(
        json.dumps(
            {
                "watchlist": watchlist is not None,
                "unavailable": unavailable,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    raise SystemExit(1)
print(
    json.dumps(
        {
            "watchlist": True,
            "available": ["watchlist", *PUBLIC_HOME_RESOURCE_ORDER],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
PY
)"; then
            printf '%s\n' "$report"
            return
        fi
        sleep 2
    done
    printf '%s\n' "$report" >&2
    fail "Public home snapshots were not generated and servable before the deployment deadline."
}

main() {
    acquire_operation_lock
    require_tools
    prepare_runtime_files
    release_identity
    compose config -q

    echo "Building Optix Pro ${APP_VERSION} (${APP_COMMIT})."
    compose build --pull backend
    validate_runtime_boundary
    stop_legacy_workers

    if ! compose up -d --no-build --force-recreate --remove-orphans --wait --wait-timeout 180; then
        compose ps >&2 || true
        compose logs --tail=200 backend worker >&2 || true
        exit 1
    fi

    verify_backend
    verify_worker
    verify_public_snapshots
    echo "Deployment verified: backend, unified worker, and access-mode data gates are ready."
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
