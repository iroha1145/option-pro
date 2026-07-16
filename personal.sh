#!/usr/bin/env bash
set -euo pipefail
umask 077

root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$root"
cli_identity="personal-cli"
operation_lock_dir="$root/.personal-operation.lock"
operation_lock_owned=false

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

build_cli_image() {
    # Never let a Secret waiting on stdin reach the image build process.
    APP_COMMIT="$cli_identity" APP_VERSION="$cli_identity" \
        "$root/scripts/compose.sh" build --quiet backend </dev/null >/dev/null
}

run_doctor() {
    APP_COMMIT="$cli_identity" APP_VERSION="$cli_identity" \
        "$root/scripts/compose.sh" run --pull never --rm --no-deps -T \
        backend python -m app.tools.validate_personal_deployment
}

resolve_secret_container_user() {
    local security_options
    security_options="$(docker info --format '{{json .SecurityOptions}}')"
    case "$security_options" in
        *name=rootless*) secret_container_user="0:0" ;;
        *name=userns*)
            echo "Secret file updates do not support Docker user namespace remapping. Use rootless Docker or a standard local daemon." >&2
            return 1
            ;;
        *) secret_container_user="$(id -u):$(id -g)" ;;
    esac
}

run_secret_python() {
    local mount_mode="$1"
    local terminal_mode="$2"
    shift
    shift
    local -a run_arguments
    run_arguments=(run --pull never --rm --no-deps)
    if [ "$terminal_mode" = "disabled" ]; then
        run_arguments+=(-T)
    fi
    run_arguments+=(
        --user "$secret_container_user"
        --volume "$root:/app:$mount_mode"
        --workdir /app/backend
        backend python -m "$@"
    )
    APP_COMMIT="$cli_identity" APP_VERSION="$cli_identity" \
        "$root/scripts/compose.sh" "${run_arguments[@]}"
}

select_affected_services() {
    case "$key" in
        OPENAI_API_KEY|FINNHUB_API_KEY|MARKETDATA_TOKEN|INTERNAL_API_TOKEN)
            services=(backend worker)
            ;;
        APP_PASSWORD_HASH) services=(backend) ;;
        *) services=() ;;
    esac
}

prepare_runtime_recreation() {
    running=()
    running_ids=()
    runtime_commit=""
    runtime_version=""
    runtime_image_id=""
    local service container_id image_reference image_id image_commit
    local image_version tagged_image_id
    for service in "${services[@]}"; do
        container_id="$(
            "$root/scripts/compose.sh" ps --status running -q "$service" \
                2>/dev/null
        )"
        [ -n "$container_id" ] || continue
        case "$container_id" in
            *$'\n'*)
                echo "Multiple running containers found for $service; refusing to change its image." >&2
                return 1
                ;;
        esac
        image_reference="$(docker inspect --format '{{.Config.Image}}' "$container_id")"
        image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
        image_commit="$(
            docker image inspect --format \
                '{{index .Config.Labels "org.opencontainers.image.revision"}}' \
                "$image_id"
        )"
        image_version="$(
            docker image inspect --format \
                '{{index .Config.Labels "org.opencontainers.image.version"}}' \
                "$image_id"
        )"
        tagged_image_id="$(
            docker image inspect --format '{{.Id}}' "$image_reference"
        )"
        case "$image_commit" in
            ""|*[!A-Za-z0-9._-]*)
                echo "Running $service has no safe immutable commit identity; refusing to replace it." >&2
                return 1
                ;;
        esac
        case "$image_version" in
            ""|*[!A-Za-z0-9._+-]*)
                echo "Running $service has no safe version identity; refusing to replace it." >&2
                return 1
                ;;
        esac
        if [ "$image_reference" != "option-pro:$image_commit" ] || \
            [ "$tagged_image_id" != "$image_id" ]; then
            echo "Running $service no longer matches its immutable local image; deploy before rotating Secrets." >&2
            return 1
        fi
        if [ -z "$runtime_commit" ]; then
            runtime_commit="$image_commit"
            runtime_version="$image_version"
            runtime_image_id="$image_id"
        elif [ "$image_commit" != "$runtime_commit" ] || \
            [ "$image_version" != "$runtime_version" ] || \
            [ "$image_id" != "$runtime_image_id" ]; then
            echo "Running services use different release images; deploy them together before rotating Secrets." >&2
            return 1
        fi
        running+=("$service")
        running_ids+=("$container_id")
    done
}

recreate_running_services() {
    [ "${#running[@]}" -gt 0 ] || return 0
    local index service container_id image_id tagged_image_id
    tagged_image_id="$(
        docker image inspect --format '{{.Id}}' "option-pro:$runtime_commit"
    )"
    [ "$tagged_image_id" = "$runtime_image_id" ] || {
        echo "The running release image tag changed during Secret rotation." >&2
        return 1
    }
    for index in "${!running[@]}"; do
        service="${running[$index]}"
        container_id="$(
            "$root/scripts/compose.sh" ps --status running -q "$service" \
                2>/dev/null
        )"
        [ "$container_id" = "${running_ids[$index]}" ] || {
            echo "Running $service changed during Secret rotation; no service was recreated." >&2
            return 1
        }
        image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
        [ "$image_id" = "$runtime_image_id" ] || {
            echo "Running $service changed image during Secret rotation." >&2
            return 1
        }
    done
    APP_COMMIT="$runtime_commit" APP_VERSION="$runtime_version" \
        "$root/scripts/compose.sh" up -d --no-deps --no-build --pull never \
        --force-recreate --wait --wait-timeout 180 "${running[@]}"
    for service in "${running[@]}"; do
        container_id="$(
            "$root/scripts/compose.sh" ps --status running -q "$service" \
                2>/dev/null
        )"
        image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
        [ "$image_id" = "$runtime_image_id" ] || {
            echo "Recreated $service does not use the preserved release image." >&2
            return 1
        }
    done
}

case "${1:-}" in
    doctor)
        [ "$#" -eq 1 ] || { echo "Usage: ./personal.sh doctor" >&2; exit 2; }
        acquire_operation_lock
        build_cli_image
        run_doctor
        exit 0
        ;;
    secrets)
        shift
        ;;
    *)
        echo "Usage: ./personal.sh {doctor|secrets {status|set KEY|remove KEY|validate}}" >&2
        exit 2
        ;;
esac

command_name="${1:-}"
key="${2:-}"
case "$command_name" in
    status|validate)
        [ "$#" -eq 1 ] || { echo "This command does not accept a value argument." >&2; exit 2; }
        acquire_operation_lock
        build_cli_image
        resolve_secret_container_user
        run_secret_python ro disabled app.tools.personal_secrets "$command_name"
        exit 0
        ;;
    set)
        [ "$#" -eq 2 ] || { echo "Secret values must be entered through standard input." >&2; exit 2; }
        select_affected_services
        acquire_operation_lock
        build_cli_image
        resolve_secret_container_user
        prepare_runtime_recreation </dev/null
        # Compose auto-detects a real terminal here so getpass can suppress echo.
        run_secret_python rw auto app.tools.personal_secrets "$command_name" "$key"
        recreate_running_services
        exit 0
        ;;
    remove)
        [ "$#" -eq 2 ] || { echo "Secret values must be entered through standard input." >&2; exit 2; }
        select_affected_services
        acquire_operation_lock
        build_cli_image
        resolve_secret_container_user
        prepare_runtime_recreation </dev/null
        run_secret_python rw disabled app.tools.personal_secrets "$command_name" "$key"
        recreate_running_services
        exit 0
        ;;
    *)
        echo "Usage: ./personal.sh secrets {status|set KEY|remove KEY|validate}" >&2
        exit 2
        ;;
esac
