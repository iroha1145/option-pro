import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const defaults = ['AAPL', 'MSFT', 'NVDA', 'SPY'];
async function fixture(page, tickers = [], owner = true) {
  const state = { tickers: [...tickers], owner, username: null, writes: [], quoteReads: [], errors: [], failRead: false, failWrite: false, malformedWrite: false, holdRead: null, holdWrite: null };
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('optix-locale', 'zh'));
  await page.route('**/*', (route) => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (path === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: state.owner, account: state.username ? { logged_in: true, username: state.username } : null } });
    if (path === '/api/ai/status') return route.fulfill({ json: { enabled: false } });
    if (path === '/api/runtime-settings') return route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } });
    if (path === '/api/account/watchlist') {
      if (request.method() === 'GET') {
        const result = { tickers: [...state.tickers], max_tickers: 50 };
        if (state.holdRead) { const hold = state.holdRead; state.holdRead = null; await hold(); }
        return route.fulfill(state.failRead ? { status: 503, json: { message: '自选暂不可用' } } : { json: result });
      }
      expect(request.method()).toBe('PATCH');
      const body = request.postDataJSON();
      state.writes.push(body);
      if (state.holdWrite) await state.holdWrite();
      if (state.failWrite) return route.fulfill({ status: 503, json: { message: '保存失败，请重试' } });
      if (state.malformedWrite) return route.fulfill({ json: {} });
      state.tickers = [...new Set([...state.tickers.filter((symbol) => !body.remove.includes(symbol)), ...body.add])];
      return route.fulfill({ json: { tickers: state.tickers, max_tickers: 50 } });
    }
    if (path === '/api/stocks/watchlist') {
      const requested = url.searchParams.get('tickers')?.split(',') ?? defaults;
      state.quoteReads.push(requested);
      if (state.failQuotes) return route.fulfill({ status: 503, json: { message: '行情读取失败' } });
      return route.fulfill({ json: { groups: [{ id: 'saved', name: '科技', stocks: requested.filter((symbol) => symbol !== 'MISSING').map((symbol) => ({ ticker: symbol, name: `${symbol} Company`, price: 100, change: 1, change_percent: 1, quote_as_of: '2026-09-04T20:00:00Z', spark: [98, 101, 100] })) }] } });
    }
    if (path === '/api/market/status') return route.fulfill({ json: { session: 'closed', label: '已收盘', is_open: false } });
    if (path === '/api/market/indices') return route.fulfill({ json: { indices: [] } });
    if (path === '/api/quotes') return route.fulfill({ json: { quotes: [], status: { allowed: false, enabled: false } } });
    return route.fulfill({ status: 503, json: { code: 'public_snapshot_unavailable', message: '暂无行情' } });
  });
  return state;
}

const remove = (page, ticker) => page.getByRole('button', { name: `将 ${ticker} 移出自选`, exact: true });
const manage = (page) => page.getByRole('button', { name: '管理自选', exact: true }).first();
async function openManager(page) {
  await manage(page).click();
  const dialog = page.getByRole('dialog', { name: '管理自选' });
  await expect(dialog).toBeVisible();
  return dialog;
}

for (const width of [320, 390, 1440]) {
  test(`bulk edits, missing quotes and empty reload persist at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await fixture(page, ['AAPL', 'MISSING']);
    await page.goto('/watchlist');
    await expect(remove(page, 'MISSING')).toBeAttached();
    const dialog = await openManager(page);
    await expect(dialog.getByLabel('添加股票代码')).toBeFocused();
    await dialog.getByLabel('添加股票代码').fill('msft，NVDA\nＭＳＦＴ SPY');
    await dialog.getByRole('button', { name: '加入列表', exact: true }).click();
    await expect(dialog.getByLabel('选择 MSFT', { exact: true })).toHaveCount(1);
    await dialog.getByLabel('选择 AAPL', { exact: true }).check();
    await dialog.getByRole('button', { name: '移除所选（1）', exact: true }).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await mkdir('test-results/watchlist', { recursive: true });
    await page.screenshot({ path: `test-results/watchlist/manager-${width}.png`, animations: 'disabled' });
    await dialog.getByRole('button', { name: '保存自选', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(manage(page)).toBeFocused();
    expect(state.writes).toEqual([{ add: ['MSFT', 'NVDA', 'SPY'], remove: ['AAPL'] }]);
    await expect(remove(page, 'AAPL')).toHaveCount(0);
    await expect(remove(page, 'MSFT')).toBeAttached();
    const second = await openManager(page);
    await second.getByLabel('全选', { exact: false }).check();
    await second.getByRole('button', { name: '移除所选（4）', exact: true }).click();
    await second.getByRole('button', { name: '保存自选', exact: true }).click();
    await expect(second).toBeHidden();
    await expect(page.getByText('清单还是空的', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('清单还是空的', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /^将 .* 移出自选$/ })).toHaveCount(0);
    expect(state.tickers).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}

test('invalid input and failed saves keep the draft; cancel makes no write', async ({ page }) => {
  const state = await fixture(page, ['AAPL']);
  await page.goto('/watchlist');
  const dialog = await openManager(page);
  await dialog.getByLabel('添加股票代码').fill('MSFT INVALID!');
  await dialog.getByRole('button', { name: '保存自选', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('INVALID!');
  expect(state.writes).toHaveLength(0);
  await dialog.getByLabel('添加股票代码').fill('MSFT');
  state.failWrite = true;
  await dialog.getByRole('button', { name: '保存自选', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('保存失败');
  await expect(dialog.getByLabel('选择 MSFT', { exact: true })).toBeVisible();
  expect(state.tickers).toEqual(['AAPL']);
  state.failWrite = false; state.malformedWrite = true;
  await dialog.getByRole('button', { name: '保存自选', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('自选列表返回异常');
  state.malformedWrite = false;
  // Simulate an unrelated addition from another tab after this draft opened.
  state.tickers.push('AMD');
  await dialog.getByRole('button', { name: '保存自选', exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(state.tickers).toEqual(['AAPL', 'AMD', 'MSFT']);
  const again = await openManager(page);
  await again.getByLabel('添加股票代码').fill('NVDA');
  const writes = state.writes.length;
  await page.keyboard.press('Escape');
  await expect(again).toBeHidden();
  expect(state.writes).toHaveLength(writes);
  expect(state.errors).toEqual([]);
});

test('deleting the final ticker ignores an older in-flight read and prevents duplicate writes', async ({ page }) => {
  const state = await fixture(page, ['AAPL']);
  await page.goto('/watchlist');
  await expect(remove(page, 'AAPL')).toBeAttached();
  let releaseRead, releaseWrite, readStarted;
  const started = new Promise((resolve) => { readStarted = resolve; });
  state.holdRead = () => new Promise((resolve) => { releaseRead = resolve; readStarted(); });
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await started;
  state.holdWrite = () => new Promise((resolve) => { releaseWrite = resolve; });
  await remove(page, 'AAPL').focus();
  await page.keyboard.press('Enter');
  await expect(remove(page, 'AAPL')).toBeDisabled();
  await page.keyboard.press('Enter');
  expect(state.writes).toHaveLength(1);
  releaseWrite();
  await expect(page.getByText('清单还是空的', { exact: true })).toBeVisible();
  releaseRead();
  await expect(remove(page, 'AAPL')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('清单还是空的', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('detail toggle works without quote coverage and persists back to the watchlist', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/stock/AAPL');
  await page.getByRole('button', { name: '加入自选', exact: true }).click();
  await expect(page.getByRole('button', { name: '已加入自选', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(state.tickers).toEqual(['AAPL']);
  await page.getByRole('link', { name: '自选', exact: true }).first().click();
  await expect(remove(page, 'AAPL')).toBeAttached();
  await page.goto('/stock/AAPL');
  await page.getByRole('button', { name: '已加入自选', exact: true }).click();
  await expect(page.getByRole('button', { name: '加入自选', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await page.goto('/watchlist');
  await expect(page.getByText('清单还是空的', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('visitors see only four defaults; personal read failures do not fall back to them', async ({ page }) => {
  const state = await fixture(page, [], false);
  await page.goto('/watchlist');
  await expect(page.getByRole('link', { name: '登录后管理自选', exact: true })).toBeVisible();
  await expect(page.locator('main [data-quote-symbol]')).toHaveCount(4);
  await expect(page.getByRole('button', { name: /^将 .* 移出自选$/ })).toHaveCount(0);
  expect(state.quoteReads).toEqual([defaults]);
  state.owner = true; state.failRead = true;
  await page.reload();
  await expect(page.getByText('自选读取失败', { exact: true })).toBeVisible();
  await expect(page.locator('main [data-quote-symbol]')).toHaveCount(0);
  expect(state.writes).toHaveLength(0);
  expect(state.errors).toEqual([]);
});

test('batch cap does not partially add and changing accounts discards the previous draft', async ({ page }) => {
  const state = await fixture(page, Array.from({ length: 50 }, (_, i) => `T${i}`));
  await page.goto('/watchlist');
  const dialog = await openManager(page);
  await dialog.getByLabel('添加股票代码').fill('NVDA');
  await dialog.getByRole('button', { name: '保存自选', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('最多保存 50');
  expect(state.writes).toHaveLength(0);
  state.owner = false; state.username = 'second-account'; state.tickers = ['MSFT'];
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(dialog).toBeHidden();
  await expect(remove(page, 'MSFT')).toBeAttached();
  await expect(remove(page, 'T0')).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('quote failures preserve membership and a visible retry restores quotes', async ({ page }) => {
  const state = await fixture(page, ['AAPL']);
  state.failQuotes = true;
  await page.goto('/watchlist');
  await expect(page.getByText('行情暂时读取失败，自选名单已保留。', { exact: true })).toBeVisible();
  await expect(remove(page, 'AAPL')).toBeAttached();
  expect(state.tickers).toEqual(['AAPL']);
  state.failQuotes = false;
  await page.getByText('行情暂时读取失败，自选名单已保留。', { exact: true }).locator('..').getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByText('行情暂时读取失败，自选名单已保留。', { exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: /AAPL Company/ })).toBeVisible();
  expect(state.errors).toEqual([]);
});
