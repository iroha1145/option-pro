import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

async function source(relativePath) {
  return readFile(path.join(src, relativePath), 'utf8');
}

test('stock pull refreshes detail, daily chart, and signals cache paths', async () => {
  const stocks = await source('api/modules/stocks.ts');
  assert.ok(stocks.includes('post(`/stocks/${encoded}/pull`, {})'));
  for (const pathSource of [
    '`/stocks/${encoded}`',
    '`/stocks/${encoded}/chart?range=1d&adjustment=raw`',
    '`/signals/stock/${encoded}`',
  ]) {
    assert.ok(stocks.includes(pathSource), `missing invalidation path ${pathSource}`);
  }
  assert.match(stocks, /resetMarketReadPaths\(\[/);
  assert.ok(stocks.includes('{ ttlMs: 60_000, staleMs: 60 * 60_000, force }'));
});

test('stock pull UI reports truthful three-resource progress and persistence', async () => {
  const control = await source('components/detail/ManualStockPull.tsx');
  assert.match(control, /基础行情/);
  assert.match(control, /日线/);
  assert.match(control, /技术信号/);
  assert.ok(control.includes('已完成 {availableCount}/3'));
  assert.match(control, /服务器保存失败，重启后可能失效/);
  assert.match(control, /正在更新基础行情、日线与技术信号 · 共 3 项/);
  assert.match(control, /state\.ticker === ticker/);
  assert.match(control, /latestTickerRef\.current !== requestedTicker/);
  assert.match(control, /Massive 优先，Yahoo 兜底/);
  assert.match(control, /cause\.retryAfter/);
  assert.equal(control.includes('if (!isOwner) return null'), false);
});

test('manual pull refreshes the open detail, chart, and signal panels immediately', async () => {
  const drawer = await source('components/StockDrawerBody.tsx');
  const chart = await source('components/detail/KlineChart.tsx');
  const trend = await source('components/detail/TrendBiasPanel.tsx');
  const list = await source('components/detail/SignalList.tsx');
  assert.match(drawer, /setDataRevision\(\(value\) => value \+ 1\)/);
  assert.match(drawer, /refreshVersion=\{dataRevision\}/);
  assert.match(chart, /getDetailChart\(ticker, range, force\)/);
  assert.match(trend, /\[ticker, refreshVersion\]/);
  assert.match(list, /\[ticker, refreshVersion\]/);
});

test('public breakout research pages and drawers expose the same manual recovery path', async () => {
  const drawer = await source('components/StockDrawerBody.tsx');
  const lead = await source('components/breakouts/LeadBigCard.tsx');
  assert.match(drawer, /publicSnapshotMissing[\s\S]*可手动拉取真实行情、日线与技术信号/);
  assert.match(drawer, /<ManualStockPull ticker=\{ticker\} onPulled=\{handlePulled\} \/>/);
  assert.equal(drawer.includes('isOwner'), false);
  assert.match(lead, /to=\{`\/stock\/\$\{encodeURIComponent\(e\.ticker\)\}`\}/);
  assert.match(lead, /打开研究页/);
});

test('live trend bias maps optional signal fields and renders missing data without inventing values', async () => {
  const detailApi = await source('components/detail/api.ts');
  const trend = await source('components/detail/TrendBiasPanel.tsx');
  assert.match(detailApi, /mapTrendBiasResponse\(body, t\)/);
  assert.match(detailApi, /asRec\(raw\.scores\)/);
  assert.match(detailApi, /asRec\(body\.signals\)/);
  assert.match(detailApi, /trendBiasScore === null \|\| rawStatus === 'insufficient_data'/);
  assert.match(detailApi, /rawStatus === 'degraded'[\s\S]*hasMissingSubscores/);
  assert.match(detailApi, /factorsFromSignals\(raw\)/);
  assert.match(trend, /data\.trend_bias_score === null/);
  assert.match(trend, /v === null \? '—'/);
  assert.match(trend, /data\.factors\.length > 0/);
});

test('only the explicit public snapshot boundary may use the scan-row fallback', async () => {
  const detailApi = await source('components/detail/api.ts');
  assert.match(
    detailApi,
    /e\.code !== 503 \|\| e\.bizCode !== 'public_snapshot_unavailable'/,
  );
});

test('numeric chart epochs are converted to real ISO timestamps', async () => {
  const stocks = await source('api/modules/stocks.ts');
  assert.match(stocks, /rawTime \* 1_000/);
  assert.match(stocks, /parsedTime\.toISOString\(\)/);
});

test('screener daily closes only deduplicate in-flight requests', async () => {
  const row = await source('components/screener/RowExpansion.tsx');
  assert.match(row, /\.finally\(\(\) => \{/);
  assert.match(row, /closesCache\.get\(ticker\) === p/);
  assert.match(row, /closesCache\.delete\(ticker\)/);
});

test('screener row expansion reads the persisted technical-signal snapshot', async () => {
  const screener = await source('pages/Screener.tsx');
  assert.match(screener, /signalsApi[\s\S]*\.stock\(next\)/);
  assert.doesNotMatch(screener, /stocksApi[\s\S]*\.signals\(next\)/);
});
