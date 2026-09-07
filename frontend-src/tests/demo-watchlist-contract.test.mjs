import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { contents: `
    export { DEFAULT_WATCHLIST_TICKERS, personalWatchlistRows } from './src/lib/personalWatchlist.ts';
    export { WATCHLIST_TICKERS, hasTicker, getWatchlist, getStockDetail, getStockChart } from './src/mocks/fixtures.ts';
  `, resolveDir: root },
  bundle: true, write: false, platform: 'node', format: 'esm',
  alias: { '@': `${root}/src` }, define: { 'import.meta.env': '{"VITE_API_MODE":"mock"}' },
});
const fx = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('DEMO-01 default watchlist members all have mock quotes', () => {
  assert.deepEqual(fx.DEFAULT_WATCHLIST_TICKERS, ['AAPL', 'MSFT', 'NVDA', 'SPY']);
  for (const ticker of fx.DEFAULT_WATCHLIST_TICKERS) {
    assert.ok(fx.WATCHLIST_TICKERS.includes(ticker), `${ticker} missing from mock pool`);
    assert.equal(fx.hasTicker(ticker), true);
  }
  const quotes = fx.getWatchlist();
  const rows = fx.personalWatchlistRows(fx.DEFAULT_WATCHLIST_TICKERS, quotes);
  assert.deepEqual(rows.map((row) => row.ticker), fx.DEFAULT_WATCHLIST_TICKERS);
  for (const row of rows) {
    assert.equal(Number.isFinite(row.price), true, `${row.ticker} price`);
    assert.notEqual(row.price, 0);
  }
});

test('DEMO-02 SPY detail identity matches list and chart last close', () => {
  const quotes = fx.getWatchlist();
  const list = quotes.find((row) => row.ticker === 'SPY');
  const detail = fx.getStockDetail('SPY');
  const chart = fx.getStockChart('SPY', '1d');
  assert.ok(list);
  assert.equal(detail.ticker, 'SPY');
  assert.equal(list.ticker, 'SPY');
  assert.equal(detail.price, list.price);
  const last = chart.candles[chart.candles.length - 1];
  assert.equal(last.c, detail.price);
});

test('DEMO-04 unknown ticker stays missing and does not borrow another quote', () => {
  const quotes = fx.getWatchlist();
  const rows = fx.personalWatchlistRows(['MISSINGXYZ', 'AAPL'], quotes);
  assert.equal(rows[0].ticker, 'MISSINGXYZ');
  assert.equal(Number.isNaN(rows[0].price), true);
  assert.equal(rows[1].ticker, 'AAPL');
  assert.equal(Number.isFinite(rows[1].price), true);
  assert.notEqual(rows[0].price, rows[1].price);
});
