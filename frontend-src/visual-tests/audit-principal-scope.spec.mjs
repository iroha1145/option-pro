import { expect, test } from '@playwright/test';

// Identity is changed by the intercepted status endpoint, as with an expired cookie
// or another tab switching accounts. No synthetic React state or private hook is used.
async function fixture(page, options = {}) {
  const state = { owner: false, username: null, failIdentity: false, holdWatchlist: false,
    holdScans: false, held: [], reads: [], errors: [], ...options };
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const principal = state.owner ? 'OWNER' : state.username?.toUpperCase() ?? 'VISITOR';
    state.reads.push({ path, principal, method: request.method() });
    const unavailable = () => route.fulfill({ status: 503, json: { message: '本地身份隔离验证：暂不可用' } });
    if (path === '/api/access/status') return state.failIdentity ? unavailable() : route.fulfill({ json: {
      access_mode: 'password', logged_in: state.owner,
      account: state.username ? { logged_in: true, username: state.username } : null,
    } });
    if (path === '/api/access/logout' || path === '/api/account/logout') {
      state.owner = false; state.username = null;
      return route.fulfill({ json: { ok: true } });
    }
    if (path === '/api/ai/status') return route.fulfill({ json: { enabled: false } });
    if (path === '/api/runtime-settings') return route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } });
    if (path === '/api/account/watchlist') return route.fulfill({ json: { tickers: ['AAPL'], max_tickers: 50 } });
    if (path === '/api/quotes') return route.fulfill({ json: { quotes: [], status: { enabled: false, allowed: false, connected: false } } });
    if (path === '/api/market/status') return route.fulfill({ json: { market: 'open', session: 'regular', is_open: true } });
    if (path === '/api/market/indices') return route.fulfill({ json: { indices: [] } });
    if (path === '/api/strength/market') return route.fulfill({ json: { avg_score: state.owner ? 95 : 50, stocks: [] } });
    if (path === '/api/strength/profiles') return route.fulfill({ json: { profiles: ['balanced', 'aggressive', 'conservative'], sectors: [] } });
    if (path === '/api/stocks/watchlist') {
      const json = { groups: [{ id: 'default', name: '默认', stocks: [{ ticker: 'AAPL', name: `${principal} snapshot`, price: 100,
        change_percent: 1, quote_as_of: new Date().toISOString() }] }] };
      if (state.holdWatchlist) { state.held.push(() => route.fulfill({ json })); return; }
      return route.fulfill({ json });
    }
    if (path === '/api/strength/scan') {
      const now = new Date().toISOString();
      const json = { rows: [{ ticker: 'AAA', name: `${principal} scan`, price: 100, final_score: 95, change_pct: 1, avg_dollar_volume_20d: 25_000_000 }],
        universe_count: 1, screened_count: 1, source_status: 'active', snapshot_saved_at: now, scan_completed_at: now,
        cache_expires_at: new Date(Date.now() + 3_600_000).toISOString(), score_version: 'principal-audit', _stale: false };
      if (state.holdScans) { state.held.push(() => route.fulfill({ json })); return; }
      return route.fulfill({ json });
    }
    if (path === '/api/catalysts/tickers/batch') return route.fulfill({ json: { results: {} } });
    if (path.startsWith('/api/signals/stock/')) return route.fulfill({ json: { signals: [] } });
    return unavailable();
  });
  return state;
}

async function focusAndVerify(page, status = 200) {
  const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/access/status' && response.status() === status);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await response;
}

test('principal expiry on Home removes the old 300-second snapshot before delayed visitor data arrives', async ({ page }) => {
  const state = await fixture(page, { owner: true });
  await page.goto('/');
  await expect(page.getByText('OWNER snapshot', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible();
  state.owner = false; state.holdWatchlist = true;
  await focusAndVerify(page);
  await expect(page).toHaveURL(/\/$/);
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  await expect(page.getByText('OWNER snapshot', { exact: true })).toHaveCount(0);
  expect(state.reads.filter(read => read.path === '/api/stocks/watchlist' && read.principal === 'VISITOR').length).toBeGreaterThan(0);
  state.holdWatchlist = false;
  await Promise.all(state.held.splice(0).map(release => release()));
  await expect(page.getByText('VISITOR snapshot', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('same-path customer change clears completed Screener results and reruns one-shot reads', async ({ page }) => {
  const state = await fixture(page, { username: 'alice' });
  await page.goto('/screener');
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toBeVisible();
  await page.locator('button.scan-trigger').click();
  await expect(page.getByRole('table').getByText('ALICE scan', { exact: true })).toBeVisible();
  state.username = 'bob'; state.holdScans = true;
  await focusAndVerify(page);
  await expect(page).toHaveURL(/\/screener$/);
  await expect(page.getByRole('button', { name: '退出 bob', exact: true })).toBeVisible();
  await expect(page.getByText('ALICE scan', { exact: true })).toHaveCount(0);
  await expect.poll(() => state.reads.filter(read => read.path === '/api/strength/profiles' && read.principal === 'BOB').length).toBeGreaterThan(0);
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  state.holdScans = false;
  await Promise.all(state.held.splice(0).map(release => release()));
  await page.locator('button.scan-trigger').click();
  await expect(page.getByRole('table').getByText('BOB scan', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('same-principal focus and unavailable identity preserve the Screener draft and current result', async ({ page }) => {
  const state = await fixture(page, { username: 'alice' });
  await page.goto('/screener');
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toBeVisible();
  await page.locator('button.scan-trigger').click();
  await expect(page.getByRole('table').getByText('ALICE scan', { exact: true })).toBeVisible();
  const profile = page.getByRole('tablist', { name: '偏好', exact: true });
  await profile.getByRole('tab', { name: '进取', exact: true }).click();
  const profilesBefore = state.reads.filter(read => read.path === '/api/strength/profiles').length;
  await focusAndVerify(page);
  await expect(profile.getByRole('tab', { name: '进取', exact: true })).toHaveAttribute('aria-selected', 'true');
  state.failIdentity = true;
  await focusAndVerify(page, 503);
  await expect(profile.getByRole('tab', { name: '进取', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('table').getByText('ALICE scan', { exact: true })).toBeVisible();
  expect(state.reads.filter(read => read.path === '/api/strength/profiles').length).toBe(profilesBefore);
  expect(state.errors).toEqual([]);
});

test('explicit Home logout still navigates to Watchlist', async ({ page }) => {
  const state = await fixture(page, { owner: true });
  await page.goto('/');
  await expect(page.getByText('OWNER snapshot', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '退出', exact: true }).click();
  await expect(page).toHaveURL(/\/watchlist$/);
  await expect(page.getByRole('link', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByText('OWNER snapshot', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});
