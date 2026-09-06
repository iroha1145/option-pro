import assert from 'node:assert/strict';
import test from 'node:test';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { contents: `
    export * from './src/lib/personalWatchlist.ts';
    export { accountApi } from './src/api/modules/account.ts';
    export { mapWatchlist, stocksApi } from './src/api/modules/stocks.ts';
  `, resolveDir: root },
  bundle: true, write: false, platform: 'node', format: 'esm',
  alias: { '@': `${root}/src` }, define: { 'import.meta.env': '{"VITE_API_MODE":"live"}' },
});
const api = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('bulk tickers normalize full-width input, deduplicate and report invalid tokens', () => {
  assert.deepEqual(api.parseWatchlistInput('aapl，ＭＳＦＴ\nNVDA spy AAPL'), { tickers: ['AAPL', 'MSFT', 'NVDA', 'SPY'], invalid: [] });
  assert.deepEqual(api.parseWatchlistInput('BRK.B 7203.T ^GSPC BAD!'), { tickers: ['BRK.B', '7203.T', '^GSPC'], invalid: ['BAD!'] });
  assert.deepEqual(api.watchlistDelta(['AAPL', 'MSFT'], ['MSFT', 'NVDA']), { add: ['NVDA'], remove: ['AAPL'] });
  assert.deepEqual(api.watchlistDelta(['AAPL'], []), { add: [], remove: ['AAPL'] });
});

test('empty membership stays empty and missing quote rows remain manageable without fabricated zeros', () => {
  const quotes = api.mapWatchlist({ groups: [{ stocks: [{ ticker: 'AAPL' }, { ticker: 'MSFT', price: 100, change: 0, change_percent: 0 }] }] });
  assert.ok(Number.isNaN(quotes[0].price));
  assert.ok(Number.isNaN(quotes[0].changePct));
  assert.equal(quotes[1].changePct, 0);
  assert.deepEqual(api.personalWatchlistRows([], quotes), []);
  const result = api.personalWatchlistRows(['MISSING', 'MSFT'], quotes);
  assert.deepEqual(result.map((row) => row.ticker), ['MISSING', 'MSFT']);
  assert.ok(Number.isNaN(result[0].price));
  assert.deepEqual(result[0].sparkline, []);
});

test('reading an empty watchlist makes no market request and equivalent combinations share one path', async () => {
  const original = globalThis.fetch;
  const paths = [];
  globalThis.fetch = async (url) => { paths.push(String(url)); return new Response(JSON.stringify({ groups: [] })); };
  try {
    assert.deepEqual(await api.stocksApi.watchlistFor([]), []);
    assert.deepEqual(paths, []);
    await Promise.all([api.stocksApi.watchlistFor(['MSFT', 'AAPL']), api.stocksApi.watchlistFor(['AAPL', 'MSFT'])]);
    assert.deepEqual(paths, ['/api/stocks/watchlist?tickers=AAPL%2CMSFT']);
  } finally { globalThis.fetch = original; }
});

test('a malformed successful write cannot be mistaken for deleting the entire watchlist', async () => {
  const original = globalThis.fetch;
  let body = {};
  globalThis.fetch = async () => new Response(JSON.stringify(body));
  try {
    await assert.rejects(api.accountApi.edit([], ['AAPL']), /自选列表返回异常/);
    body = { tickers: ['AAPL', 'AAPL'], max_tickers: 50 };
    await assert.rejects(api.accountApi.watchlist(), /自选列表返回异常/);
    body = { tickers: [], max_tickers: 50 };
    assert.deepEqual(await api.accountApi.edit([], ['AAPL']), { tickers: [], maxTickers: 50 });
  } finally { globalThis.fetch = original; }
});
