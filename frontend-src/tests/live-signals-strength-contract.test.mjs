import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const root = new URL('../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');

test('market signals map the real metric dictionary and never invent time-series counts', async () => {
  const [api, reading] = await Promise.all([
    read('src/api/modules/signals.ts'),
    read('src/components/market/SignalsReading.tsx'),
  ]);

  assert.match(api, /const rawSignals = asRec\(env\.signals\)/);
  assert.match(api, /top_score/);
  assert.match(api, /bottom_score/);
  assert.doesNotMatch(api, /delta_vs_yesterday/);
  assert.doesNotMatch(reading, /今日信号总数|较昨日|7 日均值|7日均值/);
});

test('market pages only expose supported market shape and signals', async () => {
  const [api, market, watchlist, home, regime] = await Promise.all([
    read('src/api/modules/strength.ts'), read('src/pages/Market.tsx'),
    read('src/pages/Watchlist.tsx'), read('src/pages/Home.tsx'),
    read('src/components/screener/MarketRegimeCard.tsx'),
  ]);
  for (const source of [api, market, watchlist, home, regime]) {
    assert.doesNotMatch(source, /aggregateAvailable|histogram|ge85Count|avgScore/);
  }
  assert.match(api, /mapRegime/);
  assert.match(regime, /liveDims/);
  assert.match(market, /<RegimePanel/);
  assert.match(market, /<SignalsReading/);
});

test('market status normalizes backend hyphenated extended-hours values', async () => {
  const api = await read('src/components/market/api.ts');
  assert.match(api, /state === 'pre-market'/);
  assert.match(api, /state === 'after-hours'/);
  assert.match(api, /normalizeMarketState\(raw\?\.market\)/);
});

test('watchlist refresh waits for the real worker action and follows both refresh states', async () => {
  const watchlist = await read('src/pages/Watchlist.tsx');
  assert.match(watchlist, /runtimeApi\.workerAction\('focus_refresh'\)/);
  assert.match(watchlist, /runtimeApi\.waitForWorkerAction\(action\.requestId\)/);
  assert.match(watchlist, /spinning=\{forceRefreshing \|\| wl\.refreshing\}/);
  assert.match(watchlist, /正在更新自选行情与评分/);
  assert.doesNotMatch(watchlist, /setTimeout\(\(\) => setSpinning/);
  assert.doesNotMatch(watchlist, /已强制刷新自选快照/);
  assert.match(watchlist, /rowStrengthAvailable/);
  assert.match(watchlist, /rowSignalsAvailable/);
});
