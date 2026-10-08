import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const defaults = ['AAPL', 'MSFT', 'NVDA', 'SPY'];
async function fixture(page, tickers = [], owner = true) {
  const state = { tickers: [...tickers], owner, username: null, writes: [], quoteReads: [], errors: [], failIdentity: false, failRead: false, failWrite: false, malformedWrite: false, holdRead: null, holdWrite: null, holdRemovalResponse: null };
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('optix:locale', 'zh');
    window.watchlistChanges = [];
    window.addEventListener('optix:personal-watchlist-changed', (event) => window.watchlistChanges.push(event.detail));
  });
  await page.route('**/*', (route) => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (path === '/api/access/status') return route.fulfill(state.failIdentity ? { status: 503, json: { message: '身份暂不可用' } } : { json: { access_mode: 'password', logged_in: state.owner, account: state.username ? { logged_in: true, username: state.username } : null } });
    if (path === '/api/access/logout') { state.owner = false; state.username = null; return route.fulfill({ json: { ok: true } }); }
    if (path === '/api/access/login') { state.owner = true; state.username = null; return route.fulfill({ json: { ok: true } }); }
    if (path === '/api/ai/status') return route.fulfill({ json: { enabled: false } });
    if (path === '/api/runtime-settings') return route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } });
    if (['/api/account/watchlist', '/api/account/watchlist/removals', '/api/account/watchlist/restore'].includes(path)) {
      if (request.method() === 'GET') {
        const result = { tickers: [...state.tickers], max_tickers: 50 };
        if (state.holdRead) { const hold = state.holdRead; state.holdRead = null; await hold(); }
        return route.fulfill(state.failRead ? { status: 503, json: { message: '自选暂不可用' } } : { json: result });
      }
      expect(request.method()).toBe(path === '/api/account/watchlist' ? 'PATCH' : 'POST');
      const body = request.postDataJSON();
      state.writes.push(body);
      if (state.holdWrite) await state.holdWrite();
      if (state.failWrite) return route.fulfill({ status: 503, json: { message: '保存失败，请重试' } });
      if (state.malformedWrite) return route.fulfill({ json: {} });
      const principalId = state.username ? `usr_${state.username}` : 'own_local';
      if (path === '/api/account/watchlist/removals') {
        if (body.expected_username !== (state.username ?? 'admin')) return route.fulfill({ status: 409, json: { code: 'watchlist_identity_changed', message: '登录身份已变化' } });
        const undo = state.tickers.includes(body.ticker) ? { ticker: body.ticker, original_order: [...state.tickers], principal_id: principalId } : null;
        state.tickers = state.tickers.filter((symbol) => symbol !== body.ticker);
        const response = { tickers: [...state.tickers], max_tickers: 50, undo };
        if (state.holdRemovalResponse) await state.holdRemovalResponse();
        return route.fulfill({ json: response });
      }
      if (path === '/api/account/watchlist/restore') {
        if (body.principal_id !== principalId) return route.fulfill({ status: 409, json: { code: 'watchlist_identity_changed', message: '登录身份已变化' } });
        if (!state.tickers.includes(body.ticker)) {
          if (state.tickers.length >= 50) return route.fulfill({ status: 409, json: { code: 'watchlist_full', message: '自选已满' } });
          const index = body.original_order.indexOf(body.ticker);
          const following = body.original_order.slice(index + 1).find((symbol) => state.tickers.includes(symbol));
          const preceding = body.original_order.slice(0, index).reverse().find((symbol) => state.tickers.includes(symbol));
          const insertion = following ? state.tickers.indexOf(following) : preceding ? state.tickers.indexOf(preceding) + 1 : Math.min(index, state.tickers.length);
          state.tickers.splice(insertion, 0, body.ticker);
        }
        return route.fulfill({ json: { tickers: state.tickers, max_tickers: 50 } });
      }
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

const remove = (page, ticker) => page.getByRole('button', { name: `移除关注 ${ticker}`, exact: true });
const manage = (page) => page.getByRole('button', { name: '管理关注', exact: true }).first();
async function openManager(page) {
  await manage(page).click();
  const dialog = page.getByRole('dialog', { name: '管理关注' });
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
    await dialog.getByRole('button', { name: '保存关注', exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(manage(page)).toBeFocused();
    expect(state.writes).toEqual([{ add: ['MSFT', 'NVDA', 'SPY'], remove: ['AAPL'] }]);
    await expect(remove(page, 'AAPL')).toHaveCount(0);
    await expect(remove(page, 'MSFT')).toBeAttached();
    const second = await openManager(page);
    await second.getByLabel('全选', { exact: false }).check();
    await second.getByRole('button', { name: '移除所选（4）', exact: true }).click();
    await second.getByRole('button', { name: '保存关注', exact: true }).click();
    await expect(second).toBeHidden();
    await expect(page.getByText('暂无关注', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('暂无关注', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /^移除关注 .+$/ })).toHaveCount(0);
    expect(state.tickers).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}

test('invalid input and failed saves keep the draft; cancel makes no write', async ({ page }) => {
  const state = await fixture(page, ['AAPL']);
  await page.goto('/watchlist');
  const dialog = await openManager(page);
  await dialog.getByLabel('添加股票代码').fill('MSFT INVALID!');
  await dialog.getByRole('button', { name: '保存关注', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('INVALID!');
  expect(state.writes).toHaveLength(0);
  await dialog.getByLabel('添加股票代码').fill('MSFT');
  state.failWrite = true;
  await dialog.getByRole('button', { name: '保存关注', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('保存失败');
  await expect(dialog.getByLabel('选择 MSFT', { exact: true })).toBeVisible();
  expect(state.tickers).toEqual(['AAPL']);
  state.failWrite = false; state.malformedWrite = true;
  await dialog.getByRole('button', { name: '保存关注', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('关注列表返回异常');
  state.malformedWrite = false;
  // Simulate an unrelated addition from another tab after this draft opened.
  state.tickers.push('AMD');
  await dialog.getByRole('button', { name: '保存关注', exact: true }).click();
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
  await expect(page.getByText('暂无关注', { exact: true })).toBeVisible();
  releaseRead();
  await expect(remove(page, 'AAPL')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('暂无关注', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('detail toggle works without quote coverage and persists back to the watchlist', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/stock/AAPL');
  await page.getByRole('button', { name: '加入关注', exact: true }).click();
  await expect(page.getByRole('button', { name: '已关注', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(state.tickers).toEqual(['AAPL']);
  await page.getByRole('link', { name: '我的关注', exact: true }).first().click();
  await expect(remove(page, 'AAPL')).toBeAttached();
  await page.goto('/stock/AAPL');
  await page.getByRole('button', { name: '已关注', exact: true }).click();
  await expect(page.getByRole('button', { name: '加入关注', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await page.goto('/watchlist');
  await expect(page.getByText('暂无关注', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('visitors see only four defaults; personal read failures do not fall back to them', async ({ page }) => {
  const state = await fixture(page, [], false);
  await page.goto('/watchlist');
  await expect(page.getByRole('link', { name: '登录后管理关注', exact: true })).toBeVisible();
  await expect(page.locator('main [data-quote-symbol]')).toHaveCount(4);
  await expect(page.getByRole('button', { name: /^移除关注 .+$/ })).toHaveCount(0);
  expect(state.quoteReads).toEqual([defaults]);
  state.owner = true; state.failRead = true;
  await page.reload();
  await expect(page.getByText('关注读取失败', { exact: true })).toBeVisible();
  await expect(page.locator('main [data-quote-symbol]')).toHaveCount(0);
  expect(state.writes).toHaveLength(0);
  expect(state.errors).toEqual([]);
});

test('batch cap does not partially add and changing accounts discards the previous draft', async ({ page }) => {
  const state = await fixture(page, Array.from({ length: 50 }, (_, i) => `T${i}`));
  await page.goto('/watchlist');
  const dialog = await openManager(page);
  await dialog.getByLabel('添加股票代码').fill('NVDA');
  await dialog.getByRole('button', { name: '保存关注', exact: true }).click();
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
  await expect(page.getByText('行情暂时读取失败，关注名单已保留。', { exact: true })).toBeVisible();
  await expect(remove(page, 'AAPL')).toBeAttached();
  expect(state.tickers).toEqual(['AAPL']);
  state.failQuotes = false;
  await page.getByText('行情暂时读取失败，关注名单已保留。', { exact: true }).locator('..').getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByText('行情暂时读取失败，关注名单已保留。', { exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: /AAPL Company/ })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('removing a non-final card offers undo and restores its exact order', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
  await page.goto('/watchlist');
  // 卡片上的移除钮悬停或键盘聚焦才显形；用键盘走一遍
  await remove(page, 'MSFT').focus();
  await page.keyboard.press('Enter');
  const notice = page.getByRole('status').filter({ hasText: '已移除关注' });
  await expect(notice).toBeVisible();
  await expect.poll(() => state.tickers).toEqual(['AAPL', 'NVDA']);
  await notice.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(() => state.tickers).toEqual(['AAPL', 'MSFT', 'NVDA']);
  await expect(page.getByRole('status').filter({ hasText: '已恢复关注' })).toBeVisible();
  expect(state.writes.at(-1)).toEqual({ ticker: 'MSFT', original_order: ['AAPL', 'MSFT', 'NVDA'], principal_id: 'own_local' });
  expect(state.errors).toEqual([]);
});


async function deleteWithNotice(page, symbol) {
  await remove(page, symbol).focus();
  await page.keyboard.press('Enter');
  const notice = page.getByRole('status').filter({ hasText: '已移除关注' });
  await expect(notice).toBeVisible();
  await notice.getByRole('button', { name: '撤销', exact: true }).focus();
  return notice;
}

test('undo survives navigation and preserves another tab addition', async ({ page }) => {
  const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
  await page.goto('/watchlist');
  const notice = await deleteWithNotice(page, 'MSFT');
  await page.locator('a[href="/"]').first().click();
  await expect(page).toHaveURL(/\/$/);
  state.tickers.push('AMD');
  await notice.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(() => state.tickers).toEqual(['AAPL', 'MSFT', 'NVDA', 'AMD']);
  await expect(page.getByRole('status').filter({ hasText: '已恢复关注' })).toBeVisible();
  await page.getByRole('link', { name: '我的关注', exact: true }).first().click();
  await expect(remove(page, 'MSFT')).toBeAttached();
  expect(state.errors).toEqual([]);
});

for (const identity of ['unavailable', 'changed', 'signed-out']) {
  test(`old undo makes no write when identity is ${identity}`, async ({ page }) => {
    const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
    await page.goto('/watchlist');
    const notice = await deleteWithNotice(page, 'MSFT');
    const writes = state.writes.length;
    if (identity === 'unavailable') state.failIdentity = true;
    else { state.owner = false; state.username = identity === 'changed' ? 'second-account' : null; state.tickers = []; }
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    if (identity === 'unavailable') await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
    else if (identity === 'changed') await expect(page.getByText('暂无关注', { exact: true })).toBeVisible();
    else await expect(page.getByRole('link', { name: '登录后管理关注', exact: true })).toBeVisible();
    await notice.getByRole('button', { name: '撤销', exact: true }).click();
    await expect(page.getByRole('alert').filter({ hasText: '恢复失败' })).toBeVisible();
    expect(state.writes).toHaveLength(writes);
    expect(state.errors).toEqual([]);
  });
}

for (const failure of ['server', 'full', 'malformed']) {
  test(`undo reports ${failure} failure without false success`, async ({ page }) => {
    const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
    await page.goto('/watchlist');
    const notice = await deleteWithNotice(page, 'MSFT');
    if (failure === 'server') state.failWrite = true;
    if (failure === 'malformed') state.malformedWrite = true;
    if (failure === 'full') state.tickers = Array.from({ length: 50 }, (_, index) => `T${index}`);
    const before = [...state.tickers];
    await notice.getByRole('button', { name: '撤销', exact: true }).click();
    await expect(page.getByRole('alert').filter({ hasText: '恢复失败' })).toBeVisible();
    await expect(page.getByRole('status').filter({ hasText: '已恢复关注' })).toHaveCount(0);
    expect(state.tickers).toEqual(before);
    expect(state.errors).toEqual([]);
  });
}

test('detail deletion can be undone after leaving the stock route', async ({ page }) => {
  const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
  await page.goto('/stock/MSFT');
  await page.getByRole('button', { name: '已关注', exact: true }).click();
  const notice = page.getByRole('status').filter({ hasText: '已移除关注' });
  await expect(notice).toBeVisible();
  await page.getByRole('link', { name: '我的关注', exact: true }).first().click();
  await notice.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(() => state.tickers).toEqual(['AAPL', 'MSFT', 'NVDA']);
  await expect(remove(page, 'MSFT')).toBeAttached();
  expect(state.errors).toEqual([]);
});


for (const navigate of [false, true]) {
  test(`first ticker undo restores order with concurrent addition after navigation=${navigate}`, async ({ page }) => {
    const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
    await page.goto('/watchlist');
    const notice = await deleteWithNotice(page, 'AAPL');
    if (navigate) await page.locator('a[href="/"]').first().click();
    state.tickers.push('AMD');
    await notice.getByRole('button', { name: '撤销', exact: true }).click();
    await expect.poll(() => state.tickers).toEqual(['AAPL', 'MSFT', 'NVDA', 'AMD']);
    expect(state.writes.at(-1).original_order).toEqual(['AAPL', 'MSFT', 'NVDA']);
    expect(state.errors).toEqual([]);
  });
}

test('server refuses undo after cookie account changes before the UI refreshes identity', async ({ page }) => {
  const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
  await page.goto('/watchlist');
  const notice = await deleteWithNotice(page, 'MSFT');
  state.owner = false; state.username = 'second-account'; state.tickers = ['AMD'];
  await notice.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: '恢复失败' })).toBeVisible();
  expect(state.writes.at(-1).principal_id).toEqual('own_local');
  expect(state.tickers).toEqual(['AMD']);
  await expect(page.getByRole('status').filter({ hasText: '已恢复关注' })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('duplicate synchronous undo clicks issue just one restore request', async ({ page }) => {
  const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
  await page.goto('/watchlist');
  const notice = await deleteWithNotice(page, 'MSFT');
  await notice.getByRole('button', { name: '撤销', exact: true }).evaluate((button) => { button.click(); button.click(); });
  await expect.poll(() => state.tickers).toEqual(['AAPL', 'MSFT', 'NVDA']);
  expect(state.writes).toHaveLength(2);
  expect(state.errors).toEqual([]);
});

test('an old removal response is not offered as undo to a new confirmed account', async ({ page }) => {
  const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
  await page.goto('/watchlist');
  let releaseWrite;
  state.holdRemovalResponse = () => new Promise((resolve) => { releaseWrite = resolve; });
  await remove(page, 'MSFT').focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => state.writes.length).toBe(1);
  state.owner = false; state.username = 'second-account'; state.tickers = ['AMD'];
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(remove(page, 'AMD')).toBeAttached();
  const completed = page.waitForResponse((response) => response.url().endsWith('/account/watchlist/removals'));
  releaseWrite();
  const oldResponse = await completed;
  expect((await oldResponse.json()).undo.principal_id).toBe('own_local');
  await expect.poll(() => page.evaluate(() => window.watchlistChanges.length)).toBe(2);
  await expect(page.getByRole('status').filter({ hasText: '已移除关注' })).toHaveCount(0);
  expect(state.tickers).toEqual(['AMD']);
  expect(state.errors).toEqual([]);
});


for (const interruption of ['signed-out', 'identity-unavailable']) {
  test(`pending removal is retired after ${interruption} even when the same principal returns`, async ({ page }) => {
    const state = await fixture(page, ['AAPL', 'MSFT', 'NVDA']);
    await page.goto('/watchlist');
    let releaseResponse;
    state.holdRemovalResponse = () => new Promise((resolve) => { releaseResponse = resolve; });
    await remove(page, 'MSFT').focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => state.writes.length).toBe(1);
    if (interruption === 'signed-out') await page.getByRole('button', { name: '退出', exact: true }).click();
    else { state.failIdentity = true; await page.evaluate(() => window.dispatchEvent(new Event('focus'))); }
    if (interruption === 'signed-out') await expect(page.getByRole('link', { name: '登录后管理关注', exact: true })).toBeVisible();
    else await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
    if (interruption === 'signed-out') {
      await page.getByRole('link', { name: '登录', exact: true }).first().click();
      await page.getByLabel('用户名', { exact: true }).fill('admin');
      await page.getByLabel('密码', { exact: true }).fill('fixture-only-password');
      await page.locator('button[type="submit"]').click();
      await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible();
    } else {
      state.failIdentity = false;
      await page.evaluate(() => window.dispatchEvent(new Event('focus')));
      await expect(manage(page)).toBeAttached();
    }
    await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toHaveCount(0);
    releaseResponse();
    await expect(page.getByRole('alert').filter({ hasText: '移除失败' })).toBeVisible();
    await expect(remove(page, 'MSFT')).toHaveCount(0);
    await expect(page.getByRole('status').filter({ hasText: '已移除关注' })).toHaveCount(0);
    expect(state.tickers).toEqual(['AAPL', 'NVDA']);
    expect(state.writes).toHaveLength(1);
    expect(state.errors).toEqual([]);
  });
}
