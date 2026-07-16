#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

[ -f .env ] || {
    echo ".env is missing. Copy .env.example to .env first." >&2
    exit 1
}
[ -f machine.env ] || {
    echo "machine.env is missing. Copy machine.env.example to machine.env first." >&2
    exit 1
}

skip_global_value=false
for argument in "$@"; do
    case "$argument" in
        --env-file|--env-file=*|-f|-f?*|--file|--file=*|--project-directory|--project-directory=*)
            echo "Compose file and environment overrides are not supported; edit .env or machine.env instead." >&2
            exit 2
            ;;
    esac
    if [ "$skip_global_value" = true ]; then
        skip_global_value=false
        continue
    fi
    case "$argument" in
        --ansi|--parallel|--profile|--progress|-p|--project-name)
            skip_global_value=true
            ;;
        --*|-*) ;;
        *) break ;;
    esac
done

unset COMPOSE_FILE COMPOSE_PATH_SEPARATOR COMPOSE_DISABLE_ENV_FILE
export COMPOSE_ENV_FILES=".env,machine.env"
export OPTIX_COMPOSE_ENTRYPOINT="scripts/compose.sh"
exec docker compose "$@"
