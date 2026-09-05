import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { contents: `
    export { quoteSymbol } from './src/lib/quoteSymbol.ts';
    export { mapIndices } from './src/api/modules/market.ts';
    export { mapWatchlist, mapDailyTrend, stocksApi } from './src/api/modules/stocks.ts';
    export { getWatchlist, getIndices, getStockDetail, getStockChart, hasTicker } from './src/mocks/fixtures.ts';
  `, resolveDir: root },
  bundle: true, write: false, format: 'esm', platform: 'node',
  alias: { '@': `${root}/src` }, define: { 'import.meta.env': '{"VITE_API_MODE":"live"}' },
});
const api = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('index mapper preserves the real symbol over display code and resolves legacy aliases', () => {
  const rows = api.mapIndices({ indices: [
    { code: 'SPX', symbol: '^GSPC', price: 5972, change_percent: 1 },
    { code: 'NDX', price: 21468, change_percent: -1 },
    { code: 'IXIC', symbol: '^IXIC', price: 18950, change_percent: 0 },
  ] });
  assert.deepEqual(rows.map((row) => [row.code, row.symbol]), [['SPX', '^GSPC'], ['NDX', '^NDX'], ['IXIC', '^IXIC']]);
  assert.equal(api.quoteSymbol(' spy '), 'SPY');
  assert.equal(api.quoteSymbol('QQQ'), 'QQQ');
});

test('stock requests resolve index display aliases before sharing cache and requesting charts', async () => {
  const originalFetch = globalThis.fetch;
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(JSON.stringify({ ticker: '^GSPC', price: 5972, bars: [] }), { status: 200 });
  };
  try {
    await Promise.all([api.stocksApi.detail('SPX'), api.stocksApi.detail('^GSPC'), api.stocksApi.chart('spx')]);
    assert.deepEqual(urls.sort(), ['/api/stocks/%5EGSPC', '/api/stocks/%5EGSPC/chart?range=1d&adjustment=raw']);
  } finally { globalThis.fetch = originalFetch; }
});

test('every demo index is recognized and its detail and chart retain the same index price', () => {
  for (const quote of api.getIndices()) {
    assert.equal(api.hasTicker(quote.code), true);
    assert.equal(api.hasTicker(quote.symbol), true);
    const detail = api.getStockDetail(quote.symbol);
    assert.equal(detail.ticker, quote.symbol);
    assert.equal(detail.price, quote.price);
    assert.equal(detail.change, quote.change);
    assert.equal(detail.marketCap, null);
    const chart = api.getStockChart(quote.code, '1d');
    assert.equal(chart.ticker, quote.symbol);
    assert.equal(chart.candles.at(-1).c, quote.price);
  }
  assert.notEqual(api.getStockDetail('NDX').price, api.getStockDetail('IXIC').price);
  for (const symbol of ['IXIC', 'SSE', 'N225']) {
    const detail = api.getStockDetail(symbol);
    assert.equal(api.getStockChart(symbol, '1d').candles.at(-1).c, detail.price);
  }
  assert.equal(api.hasTicker('NOT-A-TICKER'), false);
});

test('demo watchlist has 30 dated sessions with both pullbacks and rebounds', () => {
  for (const item of api.getWatchlist()) {
    assert.equal(item.dailyTrend.length, 30);
    const changes = item.dailyTrend.slice(1).map((point, i) => point.close - item.dailyTrend[i].close);
    assert.ok(changes.some((n) => n > 0));
    assert.ok(changes.some((n) => n < 0));
    assert.ok(item.dailyTrend.every((point) => point.close > 0 && ![0, 6].includes(new Date(point.date).getUTCDay())));
    assert.ok(item.dailyTrend.every((point, i, points) => i === 0 || point.date > points[i - 1].date));
  }
});

test('long-term curve requires dated daily data and rejects bad values or adjusted prices', () => {
  const points = [{ date: '2026-09-02', close: 100 }, { date: '2026-09-03', close: 98 }];
  const trend = { interval: '1d', adjustment: 'raw', points };
  assert.deepEqual(api.mapDailyTrend(trend), points);
  for (const bad of [undefined, { ...trend, interval: '5m' }, { ...trend, adjustment: 'adjusted' }, { ...trend, points: points.toReversed() }, { ...trend, points: [points[0], points[0]] }, { ...trend, points: [points[0], { date: '2026-09-03', close: NaN }] }, { ...trend, points: [points[0], { date: '2026-02-30', close: 1 }] }]) {
    assert.equal(api.mapDailyTrend(bad), undefined);
  }
  const rows = api.mapWatchlist({ groups: [{ name: 'a', stocks: [{ ticker: 'NVDA', spark: [1, 2, 3] }, { ticker: 'AMD', daily_trend: trend }] }] });
  assert.equal(api.mapDailyTrend({ ...trend, points: [points[0], null] }), undefined);
  assert.equal(rows[0].dailyTrend, undefined, 'old spark must not be relabeled as monthly history');
  assert.deepEqual(rows[1].dailyTrend, points);
});
