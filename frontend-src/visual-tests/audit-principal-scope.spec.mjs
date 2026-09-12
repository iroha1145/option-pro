import { expect, test } from '@playwright/test';

// Identity is changed by the intercepted status endpoint, as with an expired cookie
// or another tab switching accounts. No synthetic React state or private hook is used.
async function fixture(page, options = {}) {
  const state = { owner: false, username: null, failIdentity: false, holdWatchlist: false,
    holdScans: false, unauthorized: false, holdPersonal: false, holdCalendar: false, calendarAvailable: false, feedAvailable: false, holdFeedNext: false, calendarEmpty: false,
    holdRuntime: false, holdAi: false, holdLogout: false, failLogout: false, identityFailureStatus: 503,
    heldRuntime: [], heldAi: [], heldLogout: [], held: [], reads: [], errors: [], ...options };
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const principal = state.owner ? 'OWNER' : state.username?.toUpperCase() ?? 'VISITOR';
    state.reads.push({ path, principal, method: request.method() });
    const unavailable = () => route.fulfill({ status: 503, json: { message: '本地身份隔离验证：暂不可用' } });
    if (path === '/api/access/status') return state.failIdentity ? route.fulfill({ status: state.identityFailureStatus,
      json: { message: '本地身份隔离验证：暂不可用' } }) : route.fulfill({ json: {
      access_mode: 'password', logged_in: state.owner,
      account: state.username ? { logged_in: true, username: state.username } : null,
    } });
    if (path === '/api/access/logout' || path === '/api/account/logout') {
      const release = () => {
        if (state.failLogout) return unavailable();
        state.owner = false; state.username = null;
        return route.fulfill({ json: { ok: true } });
      };
      if (state.holdLogout) { state.heldLogout.push(release); return; }
      return release();
    }
    if (path === '/api/ai/status') {
      const release = () => route.fulfill({ json: { enabled: false } });
      if (state.holdAi) { state.heldAi.push(release); return; }
      return release();
    }
    if (path === '/api/runtime-settings') {
      const release = () => route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } });
      if (state.holdRuntime) { state.heldRuntime.push(release); return; }
      return release();
    }
    if (path === '/api/account/watchlist') {
      if (state.unauthorized) return route.fulfill({ status: 401, json: { code: 'account_login_required', message: '请重新登录' } });
      const json = { tickers: state.username === 'bob' ? ['MSFT'] : ['AAPL'], max_tickers: 50 };
      if (state.holdPersonal) { state.held.push(() => route.fulfill({ json })); return; }
      return route.fulfill({ json });
    }
    if (path === '/api/catalysts/feed' && state.feedAvailable) {
      const next = url.searchParams.has('cursor');
      const json = { items: [{ news_id: next ? 2 : 1, title_zh: `${principal} ${next ? '迟到分页' : '催化快照'}`,
        summary_zh: '核验用新闻摘要', published_at: new Date().toISOString(), source: 'test', source_tickers: ['AAPL'] }],
        next_cursor: next ? null : 'page-two', summary: { count: 2 } };
      if (next && state.holdFeedNext) { state.held.push(() => route.fulfill({ json })); return; }
      return route.fulfill({ json });
    }
    if (path === '/api/catalysts/calendar' && state.calendarAvailable) {
      const json = { items: [{ event_id: 'test-event', country: 'US', title: `${principal} calendar`, impact: 'high',
        scheduled_at: new Date().toISOString(), actual: '2.1', forecast: '2.0', previous: '1.9' }] };
      if (state.calendarEmpty) json.items = [];
      if (state.holdCalendar) { state.held.push(() => route.fulfill({ json })); return; }
      return route.fulfill({ json });
    }
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

test('initial identity waits for both capability probes before mounting editable Screener filters', async ({ page }) => {
  const state = await fixture(page, { owner: true, holdRuntime: true, holdAi: true });
  await page.goto('/');
  await page.getByRole('link', { name: '选股', exact: true }).first().click();
  await expect(page).toHaveURL(/\/screener$/);
  await expect.poll(() => state.heldRuntime.length).toBeGreaterThan(0);
  await expect.poll(() => state.heldAi.length).toBeGreaterThan(0);
  await expect(page.getByRole('status', { name: '页面加载中' })).toBeVisible();
  await expect(page.locator('[aria-label="筛选条件"]')).toHaveCount(0);
  state.holdRuntime = false;
  await Promise.all(state.heldRuntime.splice(0).map(release => release()));
  // The access response and runtime settings are complete, but AI status is still pending.
  await expect(page.locator('[aria-label="筛选条件"]')).toHaveCount(0);
  state.holdAi = false;
  await Promise.all(state.heldAi.splice(0).map(release => release()));
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible();
  const last = page.getByRole('tablist', { name: /^强度分档/ }).getByRole('tab', { name: /^C/ });
  await last.click();
  await expect(last).toHaveAttribute('aria-selected', 'true');
  const original = await last.elementHandle();
  // A later same-principal capability refresh must preserve the mounted control and focus.
  state.holdAi = true;
  await focusAndVerify(page);
  await expect.poll(() => state.heldAi.length).toBeGreaterThan(0);
  await expect(last).toHaveAttribute('aria-selected', 'true');
  state.holdAi = false;
  await Promise.all(state.heldAi.splice(0).map(release => release()));
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(last).toHaveAttribute('aria-selected', 'true');
  await expect(last).toBeFocused();
  expect(await original.evaluate(node => node.isConnected)).toBe(true);
  expect(state.errors).toEqual([]);
});

test('initial identity 503 shows a recoverable error and automatically opens the page after retry succeeds', async ({ page }) => {
  const state = await fixture(page, { owner: true, failIdentity: true });
  await page.goto('/screener');
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  await expect(page.getByRole('status', { name: '页面加载中' })).toHaveCount(0);
  await expect(page.locator('[aria-label="筛选条件"]')).toHaveCount(0);
  state.failIdentity = false;
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible();
  await expect(page.locator('[aria-label="筛选条件"]')).toBeVisible();
  expect(state.reads.filter(read => read.path === '/api/access/status').length).toBeGreaterThan(1);
  expect(state.errors).toEqual([]);
});

test('initial identity 429 offers manual retry without mounting provisional editable controls', async ({ page }) => {
  const state = await fixture(page, { owner: true, failIdentity: true, identityFailureStatus: 429 });
  await page.goto('/screener');
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  await expect(page.locator('[aria-label="筛选条件"]')).toHaveCount(0);
  state.failIdentity = false;
  await page.getByRole('status').filter({ hasText: '身份暂时无法确认，请稍后重试' }).getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible();
  await expect(page.locator('[aria-label="筛选条件"]')).toBeVisible();
  expect(state.errors).toEqual([]);
});

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

test('same-page explicit logout retires customer content and write controls even when confirmation is 503', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', holdLogout: true });
  await page.goto('/watchlist');
  await expect(page.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  state.failIdentity = true;
  await page.getByRole('button', { name: '退出 alice', exact: true }).click();
  await expect.poll(() => state.heldLogout.length).toBe(1);
  // The cookie write is still pending. Neither the old account nor its controls may remain.
  await expect(page.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: '自选列表', exact: true })).toHaveCount(0);
  await Promise.all(state.heldLogout.splice(0).map(release => release()));
  await expect(page).toHaveURL(/\/watchlist$/);
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  const personalReads = state.reads.filter(read => ['/api/account/watchlist', '/api/stocks/watchlist'].includes(read.path)).length;
  const identityReads = state.reads.filter(read => read.path === '/api/access/status').length;
  // Exercise another automatic failed probe, rather than sampling only the first render.
  await expect.poll(() => state.reads.filter(read => read.path === '/api/access/status').length).toBeGreaterThan(identityReads);
  await expect(page.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  expect(state.reads.filter(read => ['/api/account/watchlist', '/api/stocks/watchlist'].includes(read.path)).length).toBe(personalReads);
  state.failIdentity = false;
  await page.getByRole('status').filter({ hasText: '身份暂时无法确认，请稍后重试' }).getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('link', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByText('VISITOR snapshot', { exact: true })).toBeVisible();
  await expect(page.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  expect(state.reads.filter(read => read.path === '/api/account/logout' && read.method === 'POST').length).toBe(1);
  expect(state.errors).toEqual([]);
});

test('failed logout waits for confirmation before restoring the same customer and its list', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', holdLogout: true, failLogout: true });
  await page.goto('/watchlist');
  await expect(page.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  state.failIdentity = true;
  await page.getByRole('button', { name: '退出 alice', exact: true }).click();
  await expect.poll(() => state.heldLogout.length).toBe(1);
  await expect(page.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  await Promise.all(state.heldLogout.splice(0).map(release => release()));
  await expect(page.getByText('退出失败', { exact: true })).toBeVisible();
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toHaveCount(0);
  const readsBefore = state.reads.filter(read => ['/api/account/watchlist', '/api/stocks/watchlist'].includes(read.path)).length;
  state.failIdentity = false;
  await page.getByRole('status').filter({ hasText: '身份暂时无法确认，请稍后重试' }).getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toBeVisible();
  await expect(page.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  const resumed = state.reads.filter(read => ['/api/account/watchlist', '/api/stocks/watchlist'].includes(read.path)).slice(readsBefore);
  expect(resumed.length).toBeGreaterThan(0);
  expect(resumed.every(read => read.principal === 'ALICE')).toBe(true);
  await expect(page.getByText('VISITOR snapshot', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});


test('confirmed personal Watchlist keeps names prices and count on identity 503, pauses writes and retries identity', async ({ page }) => {
  const state = await fixture(page, { username: 'alice' });
  await page.goto('/watchlist');
  const list = page.getByRole('region', { name: '自选列表', exact: true });
  await expect(list.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  await expect(list.getByText('100.00', { exact: true })).toBeVisible();
  await expect(list).toContainText('1 只标的');
  state.failIdentity = true;
  await focusAndVerify(page, 503);
  await expect(list.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  await expect(list.getByText('100.00', { exact: true })).toBeVisible();
  await expect(list).toContainText('1 只标的');
  await expect(list.getByRole('button', { name: '管理自选', exact: true })).toBeDisabled();
  await expect(list.getByRole('button', { name: '将 AAPL 移出自选', exact: true })).toBeDisabled();
  await expect(list.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  const before = state.reads.filter(read => ['/api/account/watchlist', '/api/stocks/watchlist'].includes(read.path)).length;
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => state.reads.filter(read => read.path === '/api/access/status').length).toBeGreaterThan(2);
  expect(state.reads.filter(read => ['/api/account/watchlist', '/api/stocks/watchlist'].includes(read.path)).length).toBe(before);
  state.failIdentity = false;
  await list.getByRole('button', { name: '重试', exact: true }).click();
  await expect(list.getByRole('button', { name: '管理自选', exact: true })).toBeEnabled();
  await expect(list.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toHaveCount(0);
  await expect(list.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  expect(state.reads.filter(read => read.path === '/api/account/watchlist' && read.method !== 'GET')).toEqual([]);
  expect(state.errors).toEqual([]);
});


test('first identity failure on Watchlist shows an error without a fabricated default or personal list', async ({ page }) => {
  const state = await fixture(page, { failIdentity: true, username: 'alice' });
  await page.goto('/watchlist');
  const list = page.getByRole('region', { name: '自选列表', exact: true });
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  await expect(list).toHaveCount(0);
  await expect(page.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  await expect(page.getByText('默认关注 AAPL、MSFT、NVDA、SPY，共 4 只。', { exact: true })).toHaveCount(0);
  expect(state.reads.filter(read => ['/api/account/watchlist', '/api/stocks/watchlist'].includes(read.path))).toEqual([]);
  state.failIdentity = false;
  await page.getByRole('status').filter({ hasText: '身份暂时无法确认，请稍后重试' }).getByRole('button', { name: '重试', exact: true }).click();
  await expect(list.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('confirmed customer change discards the previous Watchlist before delayed new membership arrives', async ({ page }) => {
  const state = await fixture(page, { username: 'alice' });
  await page.goto('/watchlist');
  const list = page.getByRole('region', { name: '自选列表', exact: true });
  await expect(list.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  state.username = 'bob'; state.holdPersonal = true;
  await focusAndVerify(page);
  await expect(page.getByRole('button', { name: '退出 bob', exact: true })).toBeVisible();
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  await expect(list.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  await expect(list.getByText('AAPL', { exact: true })).toHaveCount(0);
  state.holdPersonal = false;
  await Promise.all(state.held.splice(0).map(release => release()));
  await expect(list.getByRole('button', { name: 'MSFT MSFT 涨跌数据缺失 — 暂无行情', exact: true })).toBeVisible();
  await expect(list.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('explicit watchlist 401 clears confirmed personal data even when the follow-up identity check fails', async ({ page }) => {
  const state = await fixture(page, { username: 'alice' });
  await page.goto('/watchlist');
  const list = page.getByRole('region', { name: '自选列表', exact: true });
  await expect(list.getByText('ALICE snapshot', { exact: true })).toBeVisible();
  state.unauthorized = true; state.failIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(list.getByText('ALICE snapshot', { exact: true })).toHaveCount(0);
  await expect(list.getByText('自选读取失败', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '退出 alice', exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('confirmed catalyst calendar survives identity 503 with retry and clears on a customer change', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', calendarAvailable: true });
  await page.goto('/catalysts?tab=calendar');
  await expect(page.getByText('ALICE calendar', { exact: true })).toBeVisible();
  state.failIdentity = true;
  await focusAndVerify(page, 503);
  await expect(page.getByText('ALICE calendar', { exact: true })).toBeVisible();
  const status = page.getByTestId('catalyst-cache-status');
  await expect(status).toContainText('身份暂时无法确认，请稍后重试');
  const readsBefore = state.reads.filter(read => read.path === '/api/catalysts/calendar').length;
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => state.reads.filter(read => read.path === '/api/access/status').length).toBeGreaterThan(2);
  expect(state.reads.filter(read => read.path === '/api/catalysts/calendar').length).toBe(readsBefore);
  state.failIdentity = false;
  await status.getByRole('button', { name: '重试', exact: true }).click();
  await expect(status).not.toContainText('身份暂时无法确认');
  await expect(page.getByText('ALICE calendar', { exact: true })).toBeVisible();
  state.username = 'bob'; state.holdCalendar = true;
  await focusAndVerify(page);
  await expect(page.getByRole('button', { name: '退出 bob', exact: true })).toBeVisible();
  await expect(page.getByText('ALICE calendar', { exact: true })).toHaveCount(0);
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  state.holdCalendar = false;
  await Promise.all(state.held.splice(0).map(release => release()));
  await expect(page.getByText('BOB calendar', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('catalyst calendar with no confirmed snapshot stays in an error state on identity failure', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', failIdentity: true, calendarAvailable: true });
  await page.goto('/catalysts?tab=calendar');
  await expect(page.getByText('身份暂时无法确认，请稍后重试', { exact: true })).toBeVisible();
  await expect(page.getByText('本窗口暂无经济事件', { exact: true })).toHaveCount(0);
  await expect(page.getByTestId('catalyst-cache-status')).toHaveCount(0);
  expect(state.reads.filter(read => read.path === '/api/catalysts/calendar')).toEqual([]);
  state.failIdentity = false;
  await page.getByRole('status').filter({ hasText: '身份暂时无法确认，请稍后重试' }).getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByText('ALICE calendar', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});


test('retained catalyst feed suspends pagination while identity is unavailable', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', feedAvailable: true });
  await page.goto('/catalysts');
  await expect(page.getByText('ALICE 催化快照', { exact: true })).toBeVisible();
  const more = page.getByRole('button', { name: '加载更多', exact: true });
  await expect(more).toBeEnabled();
  state.holdFeedNext = true;
  await more.click();
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  state.failIdentity = true;
  await focusAndVerify(page, 503);
  await expect(page.getByText('ALICE 催化快照', { exact: true })).toBeVisible();
  await expect(more).toBeDisabled();
  await Promise.all(state.held.splice(0).map(release => release()));
  await expect(page.getByText('ALICE 迟到分页', { exact: true })).toHaveCount(0);
  await expect(page.getByTestId('catalyst-cache-status')).toContainText('身份暂时无法确认，请稍后重试');
  const readsBefore = state.reads.filter(read => read.path === '/api/catalysts/feed').length;
  await more.evaluate(button => button.click());
  expect(state.reads.filter(read => read.path === '/api/catalysts/feed').length).toBe(readsBefore);
  state.failIdentity = false;
  await page.getByTestId('catalyst-cache-status').getByRole('button', { name: '重试', exact: true }).click();
  await expect(more).toBeEnabled();
  expect(state.errors).toEqual([]);
});


test('confirmed empty catalyst calendar still reports identity errors with a working retry', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', calendarAvailable: true, calendarEmpty: true });
  await page.goto('/catalysts?tab=calendar');
  await expect(page.getByText('本窗口暂无经济事件', { exact: true })).toBeVisible();
  state.failIdentity = true;
  await focusAndVerify(page, 503);
  await expect(page.getByText('本窗口暂无经济事件', { exact: true })).toBeVisible();
  const status = page.getByTestId('catalyst-cache-status');
  await expect(status).toContainText('身份暂时无法确认，请稍后重试');
  state.failIdentity = false;
  await status.getByRole('button', { name: '重试', exact: true }).click();
  await expect(status).not.toContainText('身份暂时无法确认');
  expect(state.errors).toEqual([]);
});
