#!/usr/bin/env bash
# After n=20 interleaved JSON exists: sync production frontend, then
# language + surfaces labs. Do not start this while measure_round6_browser.mjs
# is still using :2000/:2001.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

JSON="${OPTIX_PERF_OUT:-/opt/cursor/artifacts/perf/round6-interleaved-mobile-ref.json}"
EXPECTED_PRODUCT_COMMIT="${OPTIX_PERF_PRODUCT_COMMIT:?set OPTIX_PERF_PRODUCT_COMMIT to the measured product commit}"
EXPECTED_ENTRY="${OPTIX_PERF_ENTRY:?set OPTIX_PERF_ENTRY to the measured frontend entry path}"
if [[ ! -f "$JSON" ]]; then
  echo "interleaved JSON missing: $JSON" >&2
  exit 1
fi

node -e '
  const fs = require("node:fs");
  const [file, expectedCommit, expectedEntry] = process.argv.slice(1);
  let report;
  try { report = JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { console.error(`invalid interleaved JSON: ${error.message}`); process.exit(4); }
  const failures = [];
  if (report.ok !== true) failures.push("ok is not true");
  if (report.gate?.ok !== true) failures.push("gate.ok is not true");
  if (report.product_commit !== expectedCommit) failures.push(`product_commit=${report.product_commit ?? "missing"}`);
  if (report.entry !== expectedEntry) failures.push(`entry=${report.entry ?? "missing"}`);
  if (!Array.isArray(report.samples) || report.samples.length !== report.pairs || report.pairs < 20) {
    failures.push(`raw samples=${report.samples?.length ?? "missing"} pairs=${report.pairs ?? "missing"}`);
  }
  if (failures.length) { console.error(`interleaved input rejected: ${failures.join("; ")}`); process.exit(5); }
' "$JSON" "$EXPECTED_PRODUCT_COMMIT" "$EXPECTED_ENTRY"

if pgrep -f 'scripts/perf/measure_round6_browser.mjs' >/dev/null; then
  echo "interleaved measure still running; refuse to rebuild/run labs" >&2
  exit 2
fi

echo "== build frontend =="
VITE_API_MODE=live npm run build --prefix frontend-src
# newsToday 等源码改动会换哈希。只 cp 会留下旧 hashed 文件，diff -rq 必失败。
find frontend -mindepth 1 -delete
cp -a frontend-src/dist/. frontend/
diff -rq frontend-src/dist frontend
node scripts/perf/record_round6_bundles.mjs
DATA_DIR="${DATA_DIR:-$HOME/optix-perf-data/n10000-r6}" \
  OPTIX_PERF_PRODUCT_COMMIT="$EXPECTED_PRODUCT_COMMIT" \
  node scripts/perf/record_round6_provenance.mjs

echo "== i18n lab (vite mock :3021) =="
if ! curl -sf -o /dev/null http://127.0.0.1:3021/; then
  echo "vite mock is not up on 3021; starting" >&2
  SESSION_NAME="vite-i18n-3021"
  tmux -f /exec-daemon/tmux.portal.conf has-session -t "=$SESSION_NAME" 2>/dev/null \
    || tmux -f /exec-daemon/tmux.portal.conf new-session -d -s "$SESSION_NAME" -c "$ROOT/frontend-src" -- "${SHELL:-bash}" -l
  tmux -f /exec-daemon/tmux.portal.conf send-keys -t "$SESSION_NAME:0.0" \
    'VITE_API_MODE=mock OPTIX_API_PROXY=http://127.0.0.1:9 npm run dev -- --host 127.0.0.1 --port 3021 --strictPort' C-m
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf -o /dev/null http://127.0.0.1:3021/; then
      break
    fi
    sleep 1
  done
fi
if ! curl -sf -o /dev/null http://127.0.0.1:3021/; then
  echo "vite mock is not up on 3021" >&2
  exit 3
fi
OPTIX_PERF_BASE=http://127.0.0.1:3021 \
  OPTIX_PERF_PRODUCT_COMMIT="$EXPECTED_PRODUCT_COMMIT" \
  OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-i18n.json \
  node scripts/perf/measure_round6_i18n.mjs

echo "== surfaces lab (opt :2000, abort paid) =="
OPTIX_PERF_BASE=http://127.0.0.1:2000 \
  OPTIX_PERF_PRODUCT_COMMIT="$EXPECTED_PRODUCT_COMMIT" \
  OPTIX_PERF_ENTRY="$EXPECTED_ENTRY" \
  OPTIX_PERF_REPEATS="${OPTIX_PERF_REPEATS:-8}" \
  OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-surfaces.json \
  node scripts/perf/measure_round6_surfaces.mjs

OPTIX_PERF_PRODUCT_COMMIT="$EXPECTED_PRODUCT_COMMIT" \
  OPTIX_PERF_ENTRY="$EXPECTED_ENTRY" \
  node scripts/perf/summarize_round6.mjs
mkdir -p "$ROOT/docs/performance/artifacts"
for name in round6-summary.json round6-bundles.json; do
  if [[ -f "/opt/cursor/artifacts/perf/$name" ]]; then
    cp "/opt/cursor/artifacts/perf/$name" "$ROOT/docs/performance/artifacts/r6-${name#round6-}"
  fi
done
echo "wrote $JSON /opt/cursor/artifacts/perf/round6-i18n.json /opt/cursor/artifacts/perf/round6-surfaces.json /opt/cursor/artifacts/perf/round6-summary.json"
