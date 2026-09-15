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
# newsToday 等源码改动会换哈希。只 cp 会留下旧 hashed 文件，diff -rq 必失败。
find frontend -mindepth 1 -delete
cp -a frontend-src/dist/. frontend/
diff -rq frontend-src/dist frontend
node scripts/perf/record_round6_bundles.mjs

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
  OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-i18n.json \
  node scripts/perf/measure_round6_i18n.mjs

echo "== surfaces lab (opt :2000, abort paid) =="
OPTIX_PERF_BASE=http://127.0.0.1:2000 \
  OPTIX_PERF_REPEATS="${OPTIX_PERF_REPEATS:-8}" \
  OPTIX_PERF_OUT=/opt/cursor/artifacts/perf/round6-surfaces.json \
  node scripts/perf/measure_round6_surfaces.mjs

node scripts/perf/summarize_round6.mjs
mkdir -p "$ROOT/docs/performance/artifacts"
for name in round6-summary.json round6-bundles.json; do
  if [[ -f "/opt/cursor/artifacts/perf/$name" ]]; then
    cp "/opt/cursor/artifacts/perf/$name" "$ROOT/docs/performance/artifacts/r6-${name#round6-}"
  fi
done
echo "wrote $JSON /opt/cursor/artifacts/perf/round6-i18n.json /opt/cursor/artifacts/perf/round6-surfaces.json /opt/cursor/artifacts/perf/round6-summary.json"
