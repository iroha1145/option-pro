#!/usr/bin/env bash
# After the 2h soak summary exists, run the remaining laboratory suite.
# Refuses to start while soak-2h.summary.json is missing unless --now.
# Does not compile, does not restart :2000, does not touch production.
set -Eeuo pipefail

NOW=0
if [[ "${1:-}" == "--now" ]]; then
  NOW=1
fi

ART="${OPTIX_PERF_ART:-/opt/cursor/artifacts/perf}"
SOAK_SUMMARY="${ART}/soak-2h.summary.json"
OPT_BASE="${OPTIX_PERF_BASE:-http://127.0.0.1:2000}"
UNOPT_BASE="${OPTIX_PERF_UNOPT_BASE:-http://127.0.0.1:2001}"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE=(node --experimental-strip-types)

if [[ "$NOW" -ne 1 ]]; then
  echo "waiting for ${SOAK_SUMMARY}"
  while [[ ! -f "$SOAK_SUMMARY" ]]; do
    sleep 30
  done
fi

if ! curl -fsS "${OPT_BASE}/ready" >/dev/null; then
  echo "optimized backend not ready at ${OPT_BASE}" >&2
  exit 1
fi

python3 "$ROOT/scripts/perf/analyze_soak.py" --in "${ART}/soak-2h.jsonl" --rss "${ART}/backend-rss.jsonl" --out "${ART}/soak-2h.analysis.json" || true

# Soak is over: rebuild the production SPA so recovery/user-refresh fixes are what we measure.
echo "=== rebuild production frontend $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
(cd "$ROOT/frontend-src" && VITE_API_MODE=live npm run build)
rm -rf "$ROOT/frontend"
mkdir "$ROOT/frontend"
cp -a "$ROOT/frontend-src/dist/." "$ROOT/frontend/"

run_node() {
  local name="$1"
  shift
  echo "=== ${name} $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  "$@"
}

# News viewports n=20 (cold+warm pairs)
for profile in desktop mobile-360 mobile-430; do
  run_node "browser-${profile}" env \
    OPTIX_PERF_BASE="$OPT_BASE" \
    OPTIX_PERF_PROFILE="$profile" \
    OPTIX_PERF_PAIRS=20 \
    OPTIX_PERF_OUT="${ART}/browser-r5-${profile}.json" \
    "${NODE[@]}" "$ROOT/scripts/perf/measure_browser.mjs"
done

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

echo "suite finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
