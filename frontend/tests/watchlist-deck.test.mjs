import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '..');
const app = await readFile(path.join(frontend, 'static/js/deck-app.js'), 'utf8');

test('production watchlist uses one shared background loader without force refreshes', () => {
  assert.equal((app.match(/N\.watchlist\(/g) || []).length, 1);
  assert.match(app, /watchRefreshPromise = N\.watchlist\(\)/);
  assert.match(app, /settle\(refreshWatchState\(\)\)/);
  assert.match(app, /settle\(N\.sectors\(\)\), settle\(refreshWatchState\(\)\)/);
  assert.doesNotMatch(app, /N\.watchlist\(!!quiet\)|renderWatchlist\(true\)/);
});

test('cold watchlist paints after the batch snapshot while auxiliary data stays in background', () => {
  assert.match(app, /watch = await settle\(refreshWatchState\(\)\);/);
  assert.doesNotMatch(app, /Promise\.all\(\[\s*settle\(refreshWatchState\(\)\),\s*contextDue/);
  assert.match(app, /cacheOnly: !!options\.cacheOnlyFocus \|\| !useState/);
  assert.match(app, /if \(contextRefreshNeeded\) \{\s*void refreshWatchContextDeferred\(\)/);
  assert.match(app, /watchRenderPending > 0/);
  assert.match(app, /pending: true/);
  assert.match(app, /K线后台读取中/);
  assert.match(app, /期权异动后台读取中/);
});

test('auxiliary waits cannot block quote snapshots or schedule cooldown redraw loops', () => {
  const contextDeferred = app.match(/function refreshWatchContextDeferred\(\) \{[\s\S]*?\n  \}/)?.[0] || '';
  assert.match(app, /void loadWatchFocus\(ticker, \{ background: true \}\)/);
  assert.match(app, /if \(background\) \{\s*requestDeferredWatchRender\(\);\s*flushDeferredWatchRender\(\);/);
  assert.match(app, /const retryReady = [\s\S]*if \(!currentLoad && retryReady\)/);
  assert.match(app, /const watchTask = refreshWatchState\(\);[\s\S]*await watchTask;/);
  assert.doesNotMatch(app, /await Promise\.all\(\[\s*refreshWatchState\(\),[\s\S]*refreshWatchContext/);
  assert.doesNotMatch(contextDeferred, /watchInteractionPending \+= 1;/);
  assert.match(app, /function flushDeferredWatchRender\(\) \{\s*if \(!watchDeferredRender \|\| watchRenderPending > 0/);
  assert.match(app, /const \{ fchart: r \} = await loadWatchFocus\(f\.ticker\);/);
});

test('route returns restore the live watchlist view before checking for new data', () => {
  assert.match(app, /stashWatchlistView\(\)/);
  assert.match(app, /restoreWatchlistView\(\)/);
  assert.match(app, /currentRoute === "watchlist" && restoreWatchlistView\(\)/);
  assert.match(app, /Date\.now\(\) - watchFetchedAt >= 60e3[\s\S]*refreshWatchlistBackground\(\)/);
  assert.match(app, /function route\(\) \{\s*const key =[\s\S]*?gen \+= 1;/);
});

test('watchlist filters stay local and focus changes only use cached watch state', () => {
  assert.match(app, /data-wf[\s\S]*renderWatchlist\(\{ quiet: true, useState: true, cacheOnlyFocus: true, preserveFocus: true \}\)/);
  assert.match(app, /data-wg[\s\S]*renderWatchlist\(\{ quiet: true, useState: true, cacheOnlyFocus: true, preserveFocus: true \}\)/);
  assert.match(app, /data-card[\s\S]*renderWatchlist\(\{ quiet: true, useState: true, cacheOnlyFocus: true, retryFailedFocus: true, preserveFocus: true \}\)/);
  assert.match(app, /requestDeferredWatchRender\(\)[\s\S]*flushDeferredWatchRender\(\)/);
  assert.match(app, /finishWatchInteraction\(\)[\s\S]*flushDeferredWatchRender\(\)/);
});

test('each watchlist render is pinned to one local snapshot', () => {
  assert.match(app, /const watchSnapshot = watch\.v;/);
  assert.match(app, /const groups = watchSnapshot\.groups;/);
  assert.match(app, /const flat = watchSnapshot\.flat;/);
  assert.match(app, /watchRenderedRevision = watchSnapshotRevision;/);
  assert.match(app, /watchStateRevision !== watchRenderedRevision/);
});

test('focus cache is bounded and never stores failed requests', () => {
  assert.match(app, /const WATCH_FOCUS_MAX_STALE_MS = 15 \* 60e3;/);
  assert.match(app, /if \(maxAge >= WATCH_FOCUS_MAX_STALE_MS\) store\.delete\(key\);/);
  assert.match(app, /if \(result && result\.ok\) store\.set\(key,/);
});

test('background redraws preserve a stable keyboard target', () => {
  assert.match(app, /function captureWatchFocus\(\)/);
  assert.match(app, /function restoreWatchFocus\(bookmark\)/);
  assert.match(app, /renderWatchlist\(\{ quiet: true, useState: true, cacheOnlyFocus: true, preserveFocus: true \}\)/);
});

test('refresh copy describes the actual five-minute snapshot contract', () => {
  assert.match(app, /每 75 秒检查 · 五分钟行情快照/);
  assert.match(app, /setInterval\([\s\S]*refreshWatchlistBackground\(\)[\s\S]*WATCH_CHECK_MS/);
  assert.doesNotMatch(app, /每 75 秒自动拉取/);
});
