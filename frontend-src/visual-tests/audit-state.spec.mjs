import { expect, test } from '@playwright/test';

// All API responses are local fixtures. Cookie/IndexedDB and React remain real.
async function fixture(page, options = {}) {
  const state = { customer: null, holdIdentity: false, failIdentity: false, failQuotes: false, quoteStatus: 503,
    failMarket: false, expired: false, identityRoutes: [], quoteReads: 0, strengthReads: 0, errors: [], ...options };
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const owner = (request.headers().cookie ?? '').includes('audit_owner=1');
    const unavailable = () => route.fulfill({ status: 503, json: { message: '模拟读取失败' } });
    if (path === '/api/access/status') {
      if (state.holdIdentity) { state.identityRoutes.push(route); return; }
      if (state.failIdentity) return unavailable();
      return route.fulfill({ json: { access_mode: 'password', logged_in: owner,
        account: state.customer && !state.expired ? { logged_in: true, username: state.customer } : null } });
    }
    if (path === '/api/access/login') {
      state.failIdentity = true;
      return route.fulfill({ json: { ok: true }, headers: { 'Set-Cookie': 'audit_owner=1; Path=/; HttpOnly; SameSite=Lax' } });
    }
    if (path === '/api/ai/status') return route.fulfill({ json: { enabled: false } });
    if (path === '/api/runtime-settings') return route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } });
    if (path === '/api/account/watchlist') return state.expired
      ? route.fulfill({ status: 401, json: { code: 'account_login_required', message: '请登录' } })
      : route.fulfill({ json: { tickers: ['AAPL'], max_tickers: 50 } });
    if (path === '/api/stocks/watchlist') {
      state.quoteReads++;
      if (state.failQuotes) return route.fulfill({ status: state.quoteStatus, json: { message: '模拟行情读取失败' } });
      return route.fulfill({ json: { groups: [{ id: 'default', name: '默认', stocks: ['AAPL', 'MSFT', 'NVDA', 'SPY'].map(ticker => ({ ticker, name: ticker, price: 100, change_percent: 1, quote_as_of: new Date().toISOString() })) }] } });
    }
    if (path === '/api/strength/market') {
      state.strengthReads++;
      return route.fulfill({ json: { source: owner ? 'audit-owner-cookie' : 'audit-visitor', stocks: [], avg_score: 50 } });
    }
    if (path === '/api/market/status') return state.failMarket ? unavailable() : route.fulfill({ json: { market: 'open', session: 'regular', is_open: true,
      next_open: '2026-09-14T13:30:00Z', next_close: '2026-09-14T20:00:00Z' } });
    if (path === '/api/market/indices') return route.fulfill({ json: { indices: [] } });
    if (path === '/api/quotes') return route.fulfill({ json: { quotes: [], status: { allowed: false, enabled: false, connected: false } } });
    return unavailable();
  });
  return state;
}

async function cached(page, path) {
  return page.evaluate(path => new Promise(resolve => {
    const opening = indexedDB.open('optix-reads-v1', 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore('responses', { keyPath: 'path' });
    opening.onerror = () => resolve(null);
    opening.onsuccess = () => {
      const db = opening.result, read = db.transaction('responses', 'readonly').objectStore('responses').get(path);
      read.onsuccess = () => { db.close(); resolve(read.result ?? null); };
      read.onerror = () => { db.close(); resolve(null); };
    };
  }), path);
}

test('slow identity followed by unavailable visitor quotes shows an error, never an empty saved list', async ({ page }) => {
  const state = await fixture(page, { holdIdentity: true, failQuotes: true });
  await page.goto('/watchlist');
  await expect.poll(() => state.identityRoutes.length).toBe(1);
  await expect(page.getByText('清单还是空的', { exact: true })).toHaveCount(0);
  expect(state.quoteReads).toBe(0);
  state.holdIdentity = false;
  await state.identityRoutes.shift().fulfill({ json: { access_mode: 'password', logged_in: false, account: null } });
  await expect(page.getByText('数据暂不可用', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('清单还是空的', { exact: true })).toHaveCount(0);
  expect(state.quoteReads).toBeGreaterThan(0);
  expect(state.errors).toEqual([]);
});

test('visitor quotes survive a failed poll and expose their own retry notice', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/watchlist');
  await expect(page.locator('[data-quote-symbol="AAPL"]').first()).toContainText('100');
  await page.clock.install();
  await page.clock.fastForward(6000);
  // 408 is surfaced immediately; 5xx may deliberately reuse marketGet's 10-minute stale cache.
  state.failQuotes = true; state.quoteStatus = 408;
  const failed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/stocks/watchlist' && response.status() === 408);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await failed;
  await expect(page.getByText('行情暂时读取失败，自选名单已保留。', { exact: true })).toBeVisible();
  await expect(page.locator('[data-quote-symbol="AAPL"]').first()).toContainText('100');
  expect(state.errors).toEqual([]);
});

test('home market status retains its content after polling failure', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/');
  const panel = page.getByRole('region', { name: '市场状态', exact: true });
  await expect(panel.getByText('纽约时间', { exact: true })).toBeVisible();
  // Expire the registry's real 15-second fresh window without waiting on a live clock.
  await page.clock.install();
  await page.clock.fastForward(16000);
  state.failMarket = true;
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect(panel.getByText('刷新失败，显示上次成功的结果', { exact: true })).toBeVisible();
  await expect(panel.getByText('纽约时间', { exact: true })).toBeVisible();
  const statusNotice = panel.getByRole('status').filter({
    has: page.getByText('刷新失败，显示上次成功的结果', { exact: true }),
  });
  const retry = statusNotice.getByRole('button', { name: '重试', exact: true });
  await expect(retry).toBeVisible();
  state.failMarket = false;
  await retry.click();
  await expect(statusNotice).toHaveCount(0);
  await expect(panel.getByText('纽约时间', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('customer 401 clears the username and management controls even while identity service fails', async ({ page }) => {
  const state = await fixture(page, { customer: 'alice' });
  await page.goto('/watchlist');
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '管理自选', exact: true }).first()).toBeEnabled();
  state.expired = true; state.failIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '管理自选', exact: true })).toHaveCount(0);
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('login cookie plus failed identity pauses personalized reads and restores only confirmed owner cache', async ({ page, context }) => {
  const state = await fixture(page);
  await page.goto('/watchlist');
  // The old principal must be confirmed before this test changes its cookie.
  await expect(page.getByRole('heading', { name: '自选观察', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '登录', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '登录', exact: true }).click();
  await page.getByLabel('用户名', { exact: true }).fill('admin');
  await page.getByLabel('密码', { exact: true }).fill('audit-password-12345');
  const before = state.strengthReads;
  await page.locator('form').getByRole('button', { name: '登录', exact: true }).click();
  await expect(page).toHaveURL(/\/watchlist$/);
  expect((await context.cookies()).some(cookie => cookie.name === 'audit_owner' && cookie.value === '1')).toBe(true);
  // An explicit credential write suspends all page-owned personalized reads until
  // its new identity is confirmed. Navigating to Home cannot bypass that boundary.
  await page.getByRole('link', { name: 'Optix Pro 首页', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  expect(state.strengthReads).toBe(before);
  expect(await cached(page, '/strength/market')).toBeNull();
  state.failIdentity = false;
  // Visitor state has an automatic identity retry even without a signed-in focus listener.
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible();
  await expect.poll(() => state.strengthReads).toBeGreaterThan(before);
  // The confirmed principal remounts Home and obtains an owner-tagged response.
  await expect.poll(async () => (await cached(page, '/strength/market'))?.principal).toBe('owner\0');
  expect((await cached(page, '/strength/market')).raw.source).toBe('audit-owner-cookie');
  expect(state.errors).toEqual([]);
});
