#!/usr/bin/env bash
# After n=20 interleaved JSON exists: sync production frontend, then
# language + surfaces labs. Do not start this while measure_round6_browser.mjs
# is still using :2000/:2001.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

JSON="${OPTIX_PERF_OUT:-/opt/cursor/artifacts/perf/round6-interleaved-mobile-ref.json}"
if [[ ! -f "$JSON" ]]; then
  echo "interleaved JSON missing: $JSON" >&2
  exit 1
fi

if pgrep -f 'scripts/perf/measure_round6_browser.mjs' >/dev/null; then
  echo "interleaved measure still running; refuse to rebuild/run labs" >&2
  exit 2
fi

echo "== build frontend =="
VITE_API_MODE=live npm run build --prefix frontend-src
diff -rq frontend-src/dist frontend

echo "== i18n lab (vite mock :3021) =="
if ! curl -sf -o /dev/null http://127.0.0.1:3021/; then
  echo "vite mock is not up on 3021" >&2
  exit 3
fi
OPTIX_PERF_BASE=http://127.0.0.1:3021 \
  OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-i18n.json \
  node scripts/perf/measure_round6_i18n.mjs

echo "== surfaces lab (opt :2000, abort paid) =="
OPTIX_PERF_BASE=http://127.0.0.1:2000 \
  OPTIX_PERF_REPEATS="${OPTIX_PERF_REPEATS:-8}" \
  OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-surfaces.json \
  node scripts/perf/measure_round6_surfaces.mjs

echo "wrote $JSON /opt/cursor/artifacts/perf/round6-i18n.json /opt/cursor/artifacts/perf/round6-surfaces.json"
