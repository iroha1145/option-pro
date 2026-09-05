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
    '`/stocks/${encoded}/technical`',
  ]) {
    assert.ok(stocks.includes(pathSource), `missing invalidation path ${pathSource}`);
  }
  assert.match(stocks, /resetMarketReadPaths\(\[/);
  assert.ok(stocks.includes('{ ttlMs: 60_000, staleMs: 60 * 60_000, force }'));
});

test('snapshot-missing panels offer pull-and-analyze instead of a dead-end retry', async () => {
  // 快照缺失 ≠ 读取失败：三块分析卡都必须区分 public_snapshot_unavailable
  // 并给出「拉取并分析」入口；真正的瞬时失败仍保留重试按钮。
  const trend = await source('components/detail/TrendBiasPanel.tsx');
  const list = await source('components/detail/SignalList.tsx');
  const page = await source('pages/StockDetail.tsx');
  const control = await source('components/detail/ManualStockPull.tsx');
  for (const [name, body] of [['TrendBiasPanel', trend], ['SignalList', list], ['StockDetail', page]]) {
    assert.match(
      body,
      /bizCode === 'public_snapshot_unavailable'/,
      `${name} must branch on the snapshot-missing biz code`,
    );
    assert.match(
      body,
      /该股尚未拉取数据，拉取后自动分析/,
      `${name} must explain the pull-then-analyze recovery`,
    );
    assert.match(body, /<ManualStockPull[^>]*minimal/, `${name} must embed the minimal pull CTA`);
  }
  assert.match(trend, /趋势偏向读取失败/);
  assert.match(list, /信号数据读取失败/);
  assert.match(page, /技术结构读取失败 · 不代表该股没有结构/);
  assert.match(control, /minimal/);
  assert.match(control, /拉取并分析/);
});

test('stock pull UI reports truthful three-resource progress and persistence', async () => {
  const control = await source('components/detail/ManualStockPull.tsx');
  assert.match(control, /基础行情/);
  assert.match(control, /日线/);
  assert.match(control, /技术信号/);
  assert.ok(control.includes("{t('已完成')} {availableCount}/3"));
  assert.match(control, /服务器保存失败，重启后可能失效/);
  assert.match(control, /正在更新基础行情、日线与技术信号 · 共 3 项/);
  assert.match(control, /state\.ticker === ticker/);
  assert.match(control, /latestTickerRef\.current !== requestedTicker/);
  assert.match(control, /获取该股票的最新价格、日线与技术指标/);
  assert.doesNotMatch(control, /Massive|yfinance|兜底/);
  assert.match(control, /cause\.retryAfter/);
  assert.equal(control.includes('if (!isOwner) return null'), false);
});

test('manual pull refreshes the open detail, chart, and signal panels immediately', async () => {
  const drawer = await source('pages/StockDetail.tsx');
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
  const drawer = await source('pages/StockDetail.tsx');
  const lead = await source('components/breakouts/LeadBigCard.tsx');
  assert.match(drawer, /publicSnapshotMissing[\s\S]*可手动获取最新行情、日线与技术指标/);
  assert.match(drawer, /<ManualStockPull ticker=\{symbol\} onPulled=\{handlePulled\} \/>/);
  assert.equal(drawer.includes('isOwner'), false);
  assert.match(lead, /to=\{`\/stock\/\$\{encodeURIComponent\(e\.ticker\)\}`\}/);
  assert.match(lead, /打开研究页/);
});

test('breakout event rows survive a null event price without inventing one', async () => {
  // 盘前跳空（PREMARKET_GAP）过期事件的 event_price 为 null：AXTI 曾整页崩在
  // fmtPrice(null).toLocaleString。事件价显「—」，未知 result 不渲染状态章。
  const sidebar = await source('components/detail/SidebarEvents.tsx');
  const list = await source('components/detail/SignalList.tsx');
  const types = await source('api/types.ts');
  for (const body of [sidebar, list]) {
    assert.match(body, /typeof e\.price === 'number' && Number\.isFinite\(e\.price\) \? fmtPrice\(e\.price\) : '—'/);
  }
  assert.doesNotMatch(sidebar, /\{fmtPrice\(e\.price\)\}/);
  assert.match(list, /meta && \(/);
  assert.match(types, /price: number \| null;/);
});

test('index cards pass a canonical symbol into detail navigation', async () => {
  // 映射、旧别名请求和演示价格一致性由 index-and-watchlist-behavior.test.mjs 执行验证。
  const cards = await source('components/market/IndexCards.tsx');
  const market = await source('api/modules/market.ts');
  const mocks = await source('mocks/fixtures.ts');
  assert.match(cards, /onOpen\(quoteSymbol\(quote\.symbol \|\| quote\.code\)\)/);
  assert.doesNotMatch(cards, /onOpen\(quote\.code\)(?!\s*\|\|)/);
  assert.match(market, /symbol,\n/);
  assert.match(mocks, /symbol: '\^GSPC'/);
});

test('live trend bias maps optional signal fields and renders missing data without inventing values', async () => {
  const detailApi = await source('components/detail/api.ts');
  const trend = await source('components/detail/TrendBiasPanel.tsx');
  assert.match(detailApi, /mapTrendBiasResponse\(body, symbol\)/);
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
