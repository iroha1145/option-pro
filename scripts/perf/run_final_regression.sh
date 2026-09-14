#!/usr/bin/env bash
# Final cumulative regression after the remaining measurement suite.
# Refuses to start while measure_pages / measure_interact / measure_interleaved
# / run_faults are still running. Does not relax product rate limits.
set -Eeuo pipefail

ART="${OPTIX_PERF_ART:-/opt/cursor/artifacts/perf}"
OPT_BASE="${OPTIX_PERF_BASE:-http://127.0.0.1:2000}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE=(node --experimental-strip-types)
export OPTIX_PERF_PAIR_GAP_MS="${OPTIX_PERF_PAIR_GAP_MS:-8000}"
export OPTIX_PERF_429_COOLDOWN_MS="${OPTIX_PERF_429_COOLDOWN_MS:-60000}"

if pgrep -f 'scripts/perf/measure_(pages|interact|interleaved|spa)|scripts/perf/run_faults' >/dev/null; then
  echo "remaining suite still running; refuse to overlap" >&2
  exit 2
fi
if ! curl -fsS "${OPT_BASE}/ready" >/dev/null; then
  echo "optimized backend not ready at ${OPT_BASE}" >&2
  exit 1
fi

echo "=== frontend tests $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
(
  cd "$ROOT"
  node --experimental-strip-types --test frontend-src/tests/*.test.mjs
) | tee "${ART}/final-frontend-tests.log"

echo "=== catalyst pytest $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
(
  cd "$ROOT"
  PYTHONPATH=backend .venv/bin/python -m pytest -q \
    tests/test_personal_catalyst_service.py \
    tests/test_catalyst_api.py \
    tests/test_catalyst_local_intelligence.py
) | tee "${ART}/final-catalyst-pytest.log"

echo "=== static frontend vs dist $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
bash "$ROOT/scripts/perf/lib/verify_frontend_dist.sh" \
  "$ROOT/frontend-src/dist" \
  "$ROOT/frontend" \
  "${ART}/final-frontend-dist.diff"

echo "=== mobile-ref n=20 $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
OPTIX_PERF_BASE="$OPT_BASE" \
  OPTIX_PERF_PROFILE=mobile-ref \
  OPTIX_PERF_PAIRS=20 \
  OPTIX_PERF_OUT="${ART}/browser-final-mobile-ref.json" \
  "${NODE[@]}" "$ROOT/scripts/perf/measure_browser.mjs"

echo "=== interact n=20 $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
OPTIX_PERF_BASE="$OPT_BASE" \
  OPTIX_PERF_REPEATS=20 \
  OPTIX_PERF_OUT="${ART}/browser-final-interact.json" \
  "${NODE[@]}" "$ROOT/scripts/perf/measure_interact.mjs"

echo "final regression finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
