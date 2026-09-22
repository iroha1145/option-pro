#!/usr/bin/env bash
# Start the production-like isolated backend (single-worker uvicorn).
set -Eeuo pipefail

ROOT="${OPTIX_PERF_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}"
COUNT="${1:-10000}"
PORT="${PORT:-2000}"
DATA_DIR="${DATA_DIR:-$HOME/optix-perf-data/n${COUNT}}"
LOG="${LOG:-/tmp/optix-perf-backend.log}"

mkdir -p "$DATA_DIR"
export DATA_DIR
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1}"
export HOST_BIND="${HOST_BIND:-127.0.0.1}"
export PORT
if [[ -z "${APP_COMMIT:-}" ]]; then
  checkout_root="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null)" || checkout_root=""
  if [[ -n "$checkout_root" ]] && [[ "$(cd "$checkout_root" && pwd -P)" == "$(cd "$ROOT" && pwd -P)" ]]; then
    APP_COMMIT="$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null)" || APP_COMMIT=unknown
  else
    APP_COMMIT=unknown
  fi
fi
export APP_COMMIT

exec "$ROOT/.venv/bin/uvicorn" app.main:app \
  --host 127.0.0.1 \
  --port "$PORT" \
  --app-dir "$ROOT/backend" \
  --no-server-header
