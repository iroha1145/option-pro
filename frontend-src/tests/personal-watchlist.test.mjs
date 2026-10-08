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
    export { industryLabel } from './src/lib/industryLabel.ts';
    export { getLocale, setLocale } from './src/i18n/core.ts';
    export { installTestDictionaries } from './src/i18n/testing.ts';
    export {
      login as mockLogin,
      logout as mockLogout,
      getAccountWatchlist,
      editAccountWatchlist,
      replaceAccountWatchlist,
      removeAccountWatchlist,
      restoreAccountWatchlist,
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

test('watchlist industry labels use company metadata instead of the custom group', () => {
  const rows = api.mapWatchlist({ groups: [{ name: '自定义', stocks: [
    { ticker: 'BE', sector: 'Electrical Equipment & Parts' },
    { ticker: 'NBIS', sector: '自定义', sic_description: 'Internet Content & Information' },
    { ticker: 'UNKNOWN' },
  ] }, { name: '半导体', stocks: [{ ticker: 'NVDA' }] },
  { name: '宽基 ETF', stocks: [{ ticker: 'GLD' }] }] });
  assert.deepEqual(rows.map((row) => [row.ticker, row.sector]), [
    ['BE', '电气设备及零部件'], ['NBIS', '互联网内容与信息'],
    ['UNKNOWN', ''], ['NVDA', '半导体'], ['GLD', '宽基 ETF'],
  ]);
});

test('duplicate groups cannot replace a known industry and later metadata can fill it', () => {
  const rows = api.mapWatchlist({ groups: [
    { name: '自定义', stocks: [{ ticker: 'BE', price: 100 }, { ticker: 'NBIS', sector: 'Internet Content & Information' }] },
    { name: '关注', stocks: [{ ticker: 'BE', price: 99, sector: 'Electrical Equipment & Parts' }, { ticker: 'NBIS' }] },
  ] });
  assert.deepEqual(rows.map((row) => row.sector), ['电气设备及零部件', '互联网内容与信息']);
  assert.equal(rows[0].price, 100);
});

test('provider industry labels follow the selected interface language', () => {
  api.installTestDictionaries();
  const previous = api.getLocale();
  try {
    for (const [locale, expected] of [
      ['zh', ['电气设备及零部件', '互联网内容与信息']],
      ['en', ['Electrical Equipment & Parts', 'Internet Content & Information']],
      ['ja', ['電気機器・部品', 'インターネット・コンテンツ・情報']],
    ]) {
      api.setLocale(locale);
      assert.deepEqual([
        api.industryLabel('Electrical Equipment & Parts'),
        api.industryLabel('Internet Content & Information'),
      ], expected);
    }
  } finally { api.setLocale(previous); }
});

test('watchlist API failures map to locale copy instead of leftover Chinese', () => {
  assert.equal(
    api.watchlistErrorMessage(new api.ApiError(409, '自选最多 50 只股票', { bizCode: 'watchlist_full' }), 50),
    '最多保存 50 只股票，请先移除部分股票',
  );
  assert.equal(
    api.watchlistErrorMessage(new api.ApiError(400, '股票代码格式不正确', { bizCode: 'invalid_ticker' })),
    '股票代码格式不正确',
  );
  assert.equal(
    api.watchlistErrorMessage(new api.ApiError(400, '请求无法完成', { bizCode: 'invalid_payload' })),
    '请求无法完成，请重试',
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
    await assert.rejects(api.accountApi.edit([], ['AAPL']), /关注列表返回异常/);
    body = { tickers: ['AAPL', 'AAPL'], max_tickers: 50 };
    await assert.rejects(api.accountApi.watchlist(), /关注列表返回异常/);
    body = { tickers: [], max_tickers: 50 };
    assert.deepEqual(await api.accountApi.edit([], ['AAPL']), { tickers: [], maxTickers: 50 });
  } finally { globalThis.fetch = original; }
});


test('removal restores exact order, preserves independent edits and is idempotent', () => {
  api.mockLogin('any');
  api.replaceAccountWatchlist(['AAPL', 'MSFT', 'NVDA']);
  const removed = api.removeAccountWatchlist('MSFT', 'admin');
  assert.deepEqual(removed.undo, { ticker: 'MSFT', original_order: ['AAPL', 'MSFT', 'NVDA'], principal_id: 'own_local' });
  api.editAccountWatchlist(['AMD'], []);
  assert.deepEqual(api.restoreAccountWatchlist(removed.undo).tickers, ['AAPL', 'MSFT', 'NVDA', 'AMD']);
  assert.deepEqual(api.restoreAccountWatchlist(removed.undo).tickers, ['AAPL', 'MSFT', 'NVDA', 'AMD']);
  const again = api.removeAccountWatchlist('MSFT', 'admin');
  api.replaceAccountWatchlist(Array.from({ length: 50 }, (_, index) => `T${index}`));
  assert.throws(() => api.restoreAccountWatchlist(again.undo), (error) => error.bizCode === 'watchlist_full');
  assert.equal(api.getAccountWatchlist().tickers.length, 50);
  assert.throws(() => api.removeAccountWatchlist('T0', 'another'), (error) => error.bizCode === 'watchlist_identity_changed');
  assert.throws(() => api.restoreAccountWatchlist({ ...again.undo, principal_id: 'usr_other' }), (error) => error.bizCode === 'watchlist_identity_changed');
  api.replaceAccountWatchlist([]);
  api.mockLogout();
});

test('removal validates undo metadata and sends identity; restore sends server metadata', async () => {
  const original = globalThis.fetch;
  const requests = [];
  let body;
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), body: JSON.parse(options.body) });
    return new Response(JSON.stringify(body));
  };
  const undo = { ticker: 'MSFT', original_order: ['AAPL', 'MSFT', 'NVDA'], principal_id: 'own_local' };
  try {
    for (const invalid of [undefined, { ...undo, principal_id: 1 }, { ...undo, principal_id: '' }, { ...undo, original_order: ['AAPL'] }, { ...undo, original_order: ['MSFT', 'MSFT'] }]) {
      body = { tickers: ['AAPL', 'NVDA'], max_tickers: 50, undo: invalid };
      await assert.rejects(api.accountApi.remove('MSFT', 'admin'), /关注列表返回异常/);
    }
    body = { tickers: ['AAPL', 'NVDA'], max_tickers: 50, undo };
    assert.deepEqual((await api.accountApi.remove('MSFT', 'admin')).undo, undo);
    assert.deepEqual(requests.at(-1), { url: '/api/account/watchlist/removals', body: { ticker: 'MSFT', expected_username: 'admin' } });
    body = { tickers: ['AAPL', 'MSFT', 'NVDA'], max_tickers: 50 };
    assert.deepEqual((await api.accountApi.restore(undo)).tickers, body.tickers);
    assert.deepEqual(requests.at(-1), { url: '/api/account/watchlist/restore', body: undo });
    body = { tickers: ['AAPL', 'NVDA'], max_tickers: 50, undo: null };
    assert.equal((await api.accountApi.remove('MSFT', 'admin')).undo, null);
  } finally { globalThis.fetch = original; }
});
