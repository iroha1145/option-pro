import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const bundle = await build({
  stdin: { contents: `
    export * from './src/lib/personalWatchlist.ts';
    export { accountApi, watchlistErrorMessage } from './src/api/modules/account.ts';
    export { ApiError } from './src/api/client.ts';
    export { mapWatchlist, stocksApi } from './src/api/modules/stocks.ts';
    export {
      login as mockLogin,
      logout as mockLogout,
      getAccountWatchlist,
      editAccountWatchlist,
      replaceAccountWatchlist,
    } from './src/mocks/session.ts';
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

test('watchlist API failures map to locale copy instead of leftover Chinese', () => {
  assert.equal(
    api.watchlistErrorMessage(new api.ApiError(409, '自选最多 50 只股票', { bizCode: 'watchlist_full' }), 50),
    '最多保存 50 只股票，请先移除一些代码',
  );
  assert.equal(
    api.watchlistErrorMessage(new api.ApiError(400, '股票代码格式不正确', { bizCode: 'invalid_ticker' })),
    '股票代码格式不正确',
  );
  assert.equal(
    api.watchlistErrorMessage(new api.ApiError(400, '请求无法完成', { bizCode: 'invalid_payload' })),
    '请求无法完成',
  );
  assert.equal(api.watchlistErrorMessage(new Error('保存失败，请重试')), '保存失败，请重试');
});

test('mock watchlist is empty until owner login and rejects invalid or over-capacity writes', () => {
  api.mockLogout();
  assert.throws(() => api.getAccountWatchlist(), (error) => error instanceof api.ApiError && error.bizCode === 'account_login_required');
  api.mockLogin('any');
  api.replaceAccountWatchlist([]);
  assert.deepEqual(api.getAccountWatchlist(), { tickers: [], maxTickers: 50 });
  assert.deepEqual(api.editAccountWatchlist(['aapl', 'MSFT'], []), { tickers: ['AAPL', 'MSFT'], maxTickers: 50 });
  try {
    api.editAccountWatchlist(['BAD!'], []);
    assert.fail('expected invalid ticker');
  } catch (error) {
    assert.equal(error.bizCode, 'invalid_ticker');
  }
  assert.deepEqual(api.getAccountWatchlist().tickers, ['AAPL', 'MSFT']);
  const filled = Array.from({ length: 50 }, (_, i) => `T${i}`);
  assert.equal(api.replaceAccountWatchlist(filled).tickers.length, 50);
  try {
    api.editAccountWatchlist(['SPY'], []);
    assert.fail('expected watchlist full');
  } catch (error) {
    assert.equal(error.bizCode, 'watchlist_full');
  }
  api.replaceAccountWatchlist([]);
  api.mockLogout();
});

test('account watchlist methods keep a mock fixture path', async () => {
  const account = await readFile(new URL('../src/api/modules/account.ts', import.meta.url), 'utf8');
  assert.match(account, /mockOr\(\(\) => session\.getAccountWatchlist/);
  assert.match(account, /mockOr\(\s*\(\) => session\.editAccountWatchlist/);
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
