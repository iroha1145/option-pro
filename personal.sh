#!/usr/bin/env bash
set -euo pipefail

root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
python_bin="${PYTHON_BIN:-$root/.venv/bin/python}"
if [ ! -x "$python_bin" ]; then
    python_bin="python3"
fi

if [ "${1:-}" != "secrets" ]; then
    echo "Usage: ./personal.sh secrets {status|set KEY|remove KEY|validate}" >&2
    exit 2
fi
shift

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
    OPENAI_API_KEY) services=(backend ai-worker) ;;
    FINNHUB_API_KEY|INTERNAL_API_TOKEN|DATA_DIR) services=(backend) ;;
    APP_PASSWORD_HASH) services=(backend) ;;
    *) services=() ;;
esac

running=()
for service in "${services[@]}"; do
    if [ -n "$(docker compose -f "$root/docker-compose.yml" ps -q "$service" 2>/dev/null)" ]; then
        running+=("$service")
    fi
done
if [ "${#running[@]}" -gt 0 ]; then
    docker compose -f "$root/docker-compose.yml" restart "${running[@]}"
fi
