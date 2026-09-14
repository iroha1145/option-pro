#!/usr/bin/env bash
set -o pipefail

if [[ "$#" -ne 3 ]]; then
  echo "usage: $0 <dist-dir> <frontend-dir> <log-path>" >&2
  exit 2
fi

DIST_DIR="$1"
FRONTEND_DIR="$2"
LOG_PATH="$3"

mkdir -p "$(dirname -- "$LOG_PATH")"
diff -rq -- "$DIST_DIR" "$FRONTEND_DIR" 2>&1 | tee "$LOG_PATH"
