import { expect, test } from '@playwright/test';

// Every provider/account/job response below is synthetic and intercepted.
// No test may create a real account, modify production data, or incur model cost.
async function fixture(page, options = {}) {
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
  const at = new Date().toISOString();
  const state = {
    account: 'alice', owner: false, readyExpirations: true, holdStatus: false,
    statusRequests: [], requests: [], errors: [], expiryReads: 0, jobReads: 0,
    jobPosts: 0, failJob: true, ...options,
  };
  const event = ticker => ({ event_id: `event-${ticker}`, ticker, name: ticker,
    session: 'regular', setup_type: 'DAILY_BASE_BREAKOUT', lifecycle_state: 'TRIGGERED',
    state_version: 1, event_at: at, current_price: 100, event_price: 100,
    invalidation_price: 90, target_price: 120, session_change_pct: 1, intrinsic_strength_score: 80 });
  const events = ['AAOI', 'NVDA'].map(event);
  const jobResult = { output_language: 'zh-CN', confidence: 'low', direction: 'unknown',
    direction_status: 'unavailable_without_trade_side', summary: '原任务查询恢复成功',
    analysis: '此结果为本地合成验收。', risk_note: '未提交真实付费请求。', key_strikes: ['102.5'] };
  state.customer = { access_mode: 'password', logged_in: false, account: { logged_in: true, username: 'alice' } };
  state.visitor = { access_mode: 'password', logged_in: false, account: null };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = decodeURIComponent(url.pathname);
    if (!path.startsWith('/api/')) return route.continue(); // Do not intercept Vite's /src/api modules.
    state.requests.push({ path, account: state.account, method: route.request().method() });
    let json;
    if (path === '/api/access/status') {
      if (state.holdStatus) { state.statusRequests.push(route); return; }
      json = { access_mode: 'password', logged_in: state.owner,
        account: state.owner || !state.account ? null : { logged_in: true, username: state.account } };
    } else if (path === '/api/ai/status') json = { enabled: true };
    else if (path === '/api/runtime-settings') json = { settings: { ai: { manual_analysis_enabled: true } } };
    else if (path === '/api/account/watchlist') json = { tickers: state.account === 'alice' ? ['AAOI'] : ['CRDO'], max_tickers: 50 };
    else if (path === '/api/stocks/watchlist') json = { groups: [{ id: 'default', name: 'Default', stocks: ['NVDA', 'AAPL', 'MSFT', 'SPY'].map(ticker => ({ ticker, name: ticker, price: 100, change: 1, change_percent: 1, quote_as_of: at })) }] };
    else if (path === '/api/quotes') json = { quotes: [], status: { enabled: false, configured: false, allowed: false, connected: false, connection_status: 'disabled' } };
    else if (path === '/api/market/status') json = { session: 'regular', label: '盘中', is_open: true };
    else if (path === '/api/market/indices') json = { indices: [] };
    else if (path === '/api/stocks/data/status') json = { items: (url.searchParams.get('tickers') ?? '').split(',').filter(Boolean).map(ticker => ({ ticker, status: 'ready', refresh_status: 'ready', resources: { overview: { available: true, fresh: true, as_of: at }, daily_chart: { available: true, fresh: true, as_of: at }, signals: { available: true, fresh: true, as_of: at } } })) };
    else if (path === '/api/options/AAPL/expirations') {
      state.expiryReads++;
      json = { expirations: state.readyExpirations ? [tomorrow] : [], retryable: true };
    } else if (path === '/api/options/AAPL/chain') json = { ticker: 'AAPL', underlying_price: 103, calls: [{ strike: 102.5, volume: 300, open_interest: 100, iv: 0.00001, iv_source: 'vendor_raw', bid: 0, ask: 0 }], puts: [] };
    else if (path === '/api/breakouts/current') json = { events, as_of: at, session: 'regular' };
    else if (path === '/api/breakouts/events') json = { events: [], next_cursor: null };
    else if (path.startsWith('/api/breakouts/events/')) json = { event: events.find(row => path.endsWith(row.event_id)), transitions: [] };
    else if (path === '/api/breakouts/status') json = { enabled: true, market_session: 'regular', last_scan_at: at };
    else if (path === '/api/earnings/upcoming') json = { earnings: ['AAOI', 'CRDO'].map(ticker => ({ ticker, name: ticker, earnings_date: tomorrow, eps_estimate: 1, revenue_estimate: 100000000, market_cap: 1000000000, public_featured: false })), as_of: at, data_limited: false, source_status: 'ok' };
    else if (/^\/api\/stocks\/[^/]+$/.test(path)) json = { ticker: path.split('/').at(-1), name: '本地验收', price: 103, change: 1, change_percent: 1, prev_close: 102, as_of: at };
    else if (path.endsWith('/chart')) json = { ticker: path.split('/')[3], interval: '1d', adjustment: 'raw', bars: Array.from({ length: 60 }, (_, i) => ({ t: new Date(Date.UTC(2026, 6, 1 + i)).toISOString().slice(0, 10), o: 101, h: 105, l: 100, c: 103, v: 1000 })) };
    else if (path === '/api/ai/jobs/option-alerts') {
      state.jobPosts++;
      return route.fulfill({ status: 202, json: { id: 'audit-existing-job', status: 'queued' }, headers: { Location: '/api/ai/jobs/audit-existing-job' } });
    } else if (path === '/api/ai/jobs/audit-existing-job') {
      state.jobReads++;
      return state.failJob
        ? route.fulfill({ status: 503, json: { message: '模拟短暂查询失败' }, headers: { 'Retry-After': '1' } })
        : route.fulfill({ json: { id: 'audit-existing-job', status: 'succeeded', result: jobResult } });
    } else return route.fulfill({ status: 503, json: { message: '无关模拟接口' } });
    return route.fulfill({ json });
  });
  return state;
}

test('empty expiration retry fetches again and legacy invalid IV stays missing', async ({ page }) => {
  const state = await fixture(page, { readyExpirations: false });
  await page.goto('/stock/AAPL');
  const retry = page.getByRole('button', { name: '重新获取', exact: true });
  await expect(retry).toBeVisible();
  expect(state.expiryReads).toBe(1);
  state.readyExpirations = true;
  await retry.click();
  await expect(page.getByRole('combobox', { name: '选择到期日', exact: true })).toBeVisible();
  expect(state.expiryReads).toBe(2);
  await page.getByRole('table', { name: '期权合约列表', exact: true }).getByRole('button', { name: '查看 看涨（Call） · $102.5 明细', exact: true }).click();
  const detail = page.getByRole('region', { name: '合约报价明细', exact: true });
  await expect(detail.locator('div').filter({ has: page.locator('dt').getByText('隐含波动率', { exact: true }) }).last().locator('dd')).toHaveText('—');
  await expect(detail).not.toContainText('0.0%');
  expect(state.errors).toEqual([]);
});

test('late identity probe cannot restore the user after a newer signed-out reply', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/watchlist');
  await expect(page.getByRole('button', { name: /退出 alice/ })).toBeVisible();
  state.holdStatus = true;
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('focus')); });
  await expect.poll(() => state.statusRequests.length).toBe(2);
  await state.statusRequests[1].fulfill({ json: state.visitor });
  await expect(page.getByRole('link', { name: '登录', exact: true })).toBeVisible();
  const oldResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/access/status');
  await state.statusRequests[0].fulfill({ json: state.customer });
  await oldResponse;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.getByRole('link', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /退出 alice/ })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('radar watchlist scope uses the signed-in personal selection', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/breakouts');
  const current = page.getByRole('region', { name: '当日信号', exact: true });
  await expect(current).toContainText('AAOI');
  await page.getByRole('tab', { name: '查看自选', exact: true }).click();
  await expect(current).toContainText('AAOI');
  await expect(current).not.toContainText('NVDA');
  expect(state.requests.some(row => row.path === '/api/account/watchlist')).toBe(true);
  expect(state.errors).toEqual([]);
});

test('earnings featured companies follow account changes while the page stays mounted', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/earnings');
  const list = page.getByRole('region', { name: '即将公布', exact: true });
  await expect(list).toContainText('AAOI');
  state.account = 'bob';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('button', { name: /退出 bob/ })).toBeVisible();
  await expect(list).toContainText('CRDO');
  await expect(list).not.toContainText('AAOI');
  expect(state.requests.some(row => row.path === '/api/account/watchlist' && row.account === 'bob')).toBe(true);
  expect(state.errors).toEqual([]);
});

test('canonical and aliased indices keep charts without company-only requests', async ({ page }) => {
  const state = await fixture(page, { owner: true });
  for (const symbol of ['^GSPC', 'SPX']) {
    state.requests.length = 0;
    await page.goto(`/stock/${encodeURIComponent(symbol)}`);
    await expect(page.getByText('指数不适用公司新闻与财报摘要', { exact: true })).toBeVisible();
    await expect(page.getByText('股票雷达暂不覆盖指数，指数行情与技术研究仍可查看。', { exact: true })).toBeVisible();
    expect(state.requests.filter(({ path }) => path.startsWith('/api/breakouts/tickers/') || path.startsWith('/api/catalysts/tickers/') || path.startsWith('/api/earnings/impact/') || path.startsWith('/api/options/'))).toEqual([]);
    expect(state.requests.some(({ path }) => path.endsWith('/chart'))).toBe(true);
  }
  expect(state.errors).toEqual([]);
});

test('repeated job lookup failures pause and resume the same job without a second create', async ({ page }) => {
  const state = await fixture(page, { owner: true });
  await page.goto('/stock/AAPL');
  await page.getByRole('button', { name: '生成解读', exact: true }).click();
  await page.getByRole('button', { name: '生成解读', exact: true }).click();
  const resume = page.getByRole('button', { name: '继续查询原任务', exact: true });
  await expect(resume).toBeVisible({ timeout: 18000 });
  expect(state.jobReads).toBe(5);
  expect(state.jobPosts).toBe(1);
  state.failJob = false;
  await resume.click();
  await expect(page.getByText('原任务查询恢复成功', { exact: true })).toBeVisible();
  expect(state.jobReads).toBe(6);
  expect(state.jobPosts).toBe(1);
  expect(state.errors).toEqual([]);
});
