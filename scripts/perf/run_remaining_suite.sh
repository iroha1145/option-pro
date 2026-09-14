#!/usr/bin/env bash
# Continue the laboratory suite after desktop / 360 / 430 n=20.
# Does not rebuild frontend, does not restart :2000, does not touch production.
set -Eeuo pipefail

ART="${OPTIX_PERF_ART:-/opt/cursor/artifacts/perf}"
OPT_BASE="${OPTIX_PERF_BASE:-http://127.0.0.1:2000}"
UNOPT_BASE="${OPTIX_PERF_UNOPT_BASE:-http://127.0.0.1:2001}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE=(node --experimental-strip-types)
export OPTIX_PERF_PAIR_GAP_MS="${OPTIX_PERF_PAIR_GAP_MS:-8000}"
export OPTIX_PERF_429_COOLDOWN_MS="${OPTIX_PERF_429_COOLDOWN_MS:-60000}"

if ! curl -fsS "${OPT_BASE}/ready" >/dev/null; then
  echo "optimized backend not ready at ${OPT_BASE}" >&2
  exit 1
fi

run_node() {
  local name="$1"
  shift
  echo "=== ${name} $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  "$@"
  echo "=== cooldown 20s after ${name} $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  sleep 20
}

run_node spa env \
  OPTIX_PERF_BASE="$OPT_BASE" \
  OPTIX_PERF_REPEATS=20 \
  OPTIX_PERF_OUT="${ART}/browser-spa.json" \
  "${NODE[@]}" "$ROOT/scripts/perf/measure_spa.mjs"

run_node pages env \
  OPTIX_PERF_BASE="$OPT_BASE" \
  OPTIX_PERF_PROFILE=mobile-ref \
  OPTIX_PERF_REPEATS=20 \
  OPTIX_PERF_OUT="${ART}/browser-pages-n20.json" \
  "${NODE[@]}" "$ROOT/scripts/perf/measure_pages.mjs"

run_node interact-extra env \
  OPTIX_PERF_BASE="$OPT_BASE" \
  OPTIX_PERF_REPEATS=20 \
  OPTIX_PERF_INTERACT_EXTRA=1 \
  OPTIX_PERF_OUT="${ART}/browser-interact-extra.json" \
  "${NODE[@]}" "$ROOT/scripts/perf/measure_interact.mjs"

run_node interact-desktop env \
  OPTIX_PERF_BASE="$OPT_BASE" \
  OPTIX_PERF_PROFILE=desktop \
  OPTIX_PERF_REPEATS=20 \
  OPTIX_PERF_OUT="${ART}/browser-interact-desktop.json" \
  "${NODE[@]}" "$ROOT/scripts/perf/measure_interact.mjs"

if curl -fsS "${UNOPT_BASE}/ready" >/dev/null; then
  run_node interleaved env \
    OPTIX_PERF_OPT_BASE="$OPT_BASE" \
    OPTIX_PERF_UNOPT_BASE="$UNOPT_BASE" \
    OPTIX_PERF_PROFILE=mobile-ref \
    OPTIX_PERF_PAIRS=20 \
    OPTIX_PERF_OUT="${ART}/browser-interleaved-mobile-ref.json" \
    "${NODE[@]}" "$ROOT/scripts/perf/measure_interleaved.mjs"
else
  echo "skip interleaved: unoptimized backend not ready at ${UNOPT_BASE}" >&2
fi

run_node faults env \
  OPTIX_PERF_BASE="$OPT_BASE" \
  OPTIX_PERF_OUT="${ART}/faults.json" \
  "${NODE[@]}" "$ROOT/scripts/perf/run_faults.mjs"

echo "remaining suite finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
