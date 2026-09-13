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
export APP_COMMIT="${APP_COMMIT:-31e8955d89dc2b9b51a5bea1c47f5cfa6ea8cabc}"

exec "$ROOT/.venv/bin/uvicorn" app.main:app \
  --host 127.0.0.1 \
  --port "$PORT" \
  --app-dir "$ROOT/backend" \
  --no-server-header
