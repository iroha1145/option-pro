#!/usr/bin/env bash
set -euo pipefail
umask 077

root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$root"
if [ -f "$root/machine.env" ]; then
    export COMPOSE_ENV_FILES=".env,machine.env"
else
    export COMPOSE_ENV_FILES=".env"
fi
python_bin="${PYTHON_BIN:-$root/.venv/bin/python}"
if [ ! -x "$python_bin" ]; then
    python_bin="python3"
fi

case "${1:-}" in
    doctor)
        [ "$#" -eq 1 ] || { echo "Usage: ./personal.sh doctor" >&2; exit 2; }
        PYTHONPATH="$root/backend${PYTHONPATH:+:$PYTHONPATH}" \
            "$python_bin" -m app.tools.validate_personal_deployment
        exit $?
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
        PYTHONPATH="$root/backend${PYTHONPATH:+:$PYTHONPATH}" \
            "$python_bin" -m app.tools.personal_secrets "$command_name"
        ;;
    set|remove)
        [ "$#" -eq 2 ] || { echo "Secret values must be entered through standard input." >&2; exit 2; }
        PYTHONPATH="$root/backend${PYTHONPATH:+:$PYTHONPATH}" \
            "$python_bin" -m app.tools.personal_secrets "$command_name" "$key"
        ;;
    *)
        echo "Usage: ./personal.sh secrets {status|set KEY|remove KEY|validate}" >&2
        exit 2
        ;;
esac

if [ "$command_name" != "set" ] && [ "$command_name" != "remove" ]; then
    exit 0
fi
if ! command -v docker >/dev/null 2>&1 || [ ! -f "$root/docker-compose.yml" ]; then
    exit 0
fi

case "$key" in
    OPENAI_API_KEY|FINNHUB_API_KEY|MARKETDATA_TOKEN|INTERNAL_API_TOKEN)
        services=(backend worker)
        ;;
    APP_PASSWORD_HASH) services=(backend) ;;
    *) services=() ;;
esac

running=()
for service in "${services[@]}"; do
    if [ -n "$(docker compose -f "$root/docker-compose.yml" ps --status running -q "$service" 2>/dev/null)" ]; then
        running+=("$service")
    fi
done
if [ "${#running[@]}" -gt 0 ]; then
    docker compose -f "$root/docker-compose.yml" up -d --no-deps \
        --force-recreate "${running[@]}"
fi
