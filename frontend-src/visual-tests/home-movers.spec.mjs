import { expect, test } from '@playwright/test';

const defaults = ['AAPL', 'MSFT', 'NVDA', 'SPY'];
const region = (page) => page.getByRole('region', { name: '关注池异动', exact: true });
const cards = (page) => region(page).getByTestId('watchlist-mover-card');
const symbols = (page) => cards(page).evaluateAll((els) => els.map((el) => decodeURIComponent(el.getAttribute('href').split('/').at(-1))));
const focus = (page) => page.evaluate(() => window.dispatchEvent(new Event('focus')));
const visibility = (page) => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));

async function fixture(page, options = {}) {
  const state = { owner: false, username: null, members: { alice: ['AMD'], bob: ['TSLA'], owner: ['QQQ'] },
    failMembers: false, failQuotes: false, holdIdentity: false, holdMembers: false,
    holdQuotesFor: null, held: [], quoteReads: [], statusReads: [], memberReads: 0,
    prepared: false, price: 100, errors: [], ...options };
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.route('**/*', (route) => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url()), path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const fulfill = (json, status = 200) => route.fulfill({ json, status });
    const hold = (json) => state.held.push(() => fulfill(json));
    if (path === '/api/access/status') {
      const json = { access_mode: 'password', logged_in: state.owner,
        account: state.username ? { logged_in: true, username: state.username } : null };
      if (state.holdIdentity) return hold(json);
      return fulfill(json);
    }
    if (path === '/api/access/logout' || path === '/api/account/logout') {
      state.owner = false; state.username = null;
      return fulfill({ ok: true });
    }
    if (path === '/api/ai/status') return fulfill({ enabled: false });
    if (path === '/api/runtime-settings') return fulfill({ settings: { ai: { manual_analysis_enabled: false } } });
    if (path === '/api/account/watchlist') {
      state.memberReads++;
      if (state.failMembers) return fulfill({ message: '个人成员读取失败' }, 503);
      const json = { tickers: [...(state.members[state.username ?? 'owner'] ?? [])], max_tickers: 50 };
      if (state.holdMembers) return hold(json);
      return fulfill(json);
    }
    if (path === '/api/stocks/watchlist') {
      const selection = url.searchParams.get('tickers');
      const requested = selection?.split(',') ?? defaults;
      state.quoteReads.push({ selection, requested: [...requested] });
      if (selection && state.failQuotes) return fulfill({ message: '个人行情读取失败' }, 503);
      const json = { groups: [{ stocks: requested.filter((ticker) => ticker !== 'MISSING').map((ticker, index) => ({
        ticker, name: `${selection ? 'PERSONAL' : 'PUBLIC'} ${ticker}`, price: state.price,
        change_percent: 4 - index, quote_as_of: '2026-10-07T20:00:00Z',
      })) }] };
      if (selection && requested.includes(state.holdQuotesFor)) return hold(json);
      return fulfill(json);
    }
    if (path === '/api/stocks/data/status') {
      const requested = url.searchParams.get('tickers')?.split(',') ?? [];
      state.statusReads.push(requested);
      const resource = { available: state.prepared, fresh: state.prepared, as_of: state.prepared ? '2026-10-07T20:00:00Z' : null };
      return fulfill({ items: requested.map((ticker) => ({ ticker, status: state.prepared ? 'ready' : 'pending',
        resources: { overview: resource, daily_chart: resource, signals: resource } })) });
    }
    if (path === '/api/quotes') return fulfill({ quotes: [], status: { allowed: false, enabled: false, connected: false } });
    if (path === '/api/market/indices') return fulfill({ indices: [] });
    if (path === '/api/market/status') return fulfill({ market: 'closed' });
    return fulfill({ code: 'public_snapshot_unavailable', message: '其他首页模块未提供测试数据' }, 503);
  });
  return state;
}

test('visitor keeps the public pool without reading personal membership', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/');
  await expect.poll(() => symbols(page)).toEqual(defaults);
  expect(state.memberReads).toBe(0);
  expect(state.errors).toEqual([]);
});

for (const [name, options, expected] of [
  ['customer with visitor role', { username: 'alice' }, ['AMD']],
  ['owner', { owner: true }, ['QQQ']],
  ['owner with a customer session', { owner: true, username: 'bob' }, ['TSLA']],
]) {
  test(`${name} uses personal members and preserves the separate public query`, async ({ page }) => {
    const state = await fixture(page, options);
    await page.goto('/');
    await expect.poll(() => symbols(page)).toEqual(expected);
    expect(state.quoteReads.some((read) => read.selection === null)).toBe(true);
    expect(state.quoteReads.some((read) => read.requested.join(',') === expected.join(','))).toBe(true);
    await expect(region(page)).not.toContainText('PUBLIC');
    expect(state.errors).toEqual([]);
  });
}

test('identity and membership confirmation never flash the public movers', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', holdIdentity: true, holdMembers: true });
  await page.goto('/');
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  await expect(cards(page)).toHaveCount(0);
  state.holdIdentity = false;
  await Promise.all(state.held.splice(0).map((release) => release()));
  await expect.poll(() => state.memberReads).toBeGreaterThan(0);
  await expect.poll(() => state.quoteReads.some((read) => read.selection === null)).toBe(true);
  await expect(cards(page)).toHaveCount(0);
  expect(state.quoteReads.some((read) => read.selection !== null)).toBe(false);
  state.holdMembers = false;
  await Promise.all(state.held.splice(0).map((release) => release()));
  await expect.poll(() => symbols(page)).toEqual(['AMD']);
});

test('an empty personal pool stays empty and sends no personal quote request', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', members: { alice: [] } });
  await page.goto('/');
  await expect(region(page)).toContainText('暂无关注标的');
  await expect(cards(page)).toHaveCount(0);
  expect(state.quoteReads.every((read) => read.selection === null)).toBe(true);
});

for (const failure of ['failMembers', 'failQuotes']) {
  test(`${failure} displays a retryable error without default members`, async ({ page }) => {
    const state = await fixture(page, { username: 'alice', [failure]: true });
    await page.goto('/');
    await expect(region(page).getByRole('button', { name: '重试', exact: true })).toBeVisible();
    await expect(cards(page)).toHaveCount(0);
    await expect(region(page)).toContainText(failure === 'failMembers' ? '个人成员读取失败' : '个人行情读取失败');
    state[failure] = false;
    await region(page).getByRole('button', { name: '重试', exact: true }).click();
    await expect.poll(() => symbols(page)).toEqual(['AMD']);
    expect(state.errors).toEqual([]);
  });
}

for (const failure of ['failMembers', 'failQuotes']) {
  test(`${failure} after success retains the same personal pool and successful timestamp`, async ({ page }) => {
    const state = await fixture(page, { username: 'alice' });
    await page.goto('/');
    await expect.poll(() => symbols(page)).toEqual(['AMD']);
    const footer = region(page).locator(':scope > p').last();
    await expect(footer).toContainText('更新');
    const updated = await footer.textContent();
    await page.clock.setSystemTime(new Date('2026-10-08T01:01:01Z'));
    state[failure] = true;
    if (failure === 'failMembers') await focus(page);
    else await visibility(page);
    await expect(region(page).getByRole('button', { name: '重试', exact: true })).toBeVisible();
    await expect.poll(() => symbols(page)).toEqual(['AMD']);
    await expect(region(page)).not.toContainText('PUBLIC');
    await expect(footer).toHaveText(updated);
    state[failure] = false;
    await region(page).getByRole('button', { name: '重试', exact: true }).click();
    await expect(region(page).getByRole('button', { name: '重试', exact: true })).toHaveCount(0);
    await expect.poll(() => symbols(page)).toEqual(['AMD']);
    expect(state.errors).toEqual([]);
  });
}

test('logout retires personal movers and restores the public pool', async ({ page }) => {
  const state = await fixture(page, { username: 'alice' });
  await page.goto('/');
  await expect.poll(() => symbols(page)).toEqual(['AMD']);
  await page.getByRole('button', { name: '退出 alice', exact: true }).click();
  // 全站退出动作既有跳转为自选页，再经实际首页入口回到异动区。
  await expect(page).toHaveURL(/\/watchlist$/);
  await page.getByRole('link', { name: 'Optix Pro 首页', exact: true }).click();
  await expect.poll(() => symbols(page)).toEqual(defaults);
  expect(state.username).toBeNull();
  expect(state.errors).toEqual([]);
});

test('switching account rejects a late personal quote response from the old account', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', holdQuotesFor: 'AMD' });
  await page.goto('/');
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  state.username = 'bob'; state.holdQuotesFor = null;
  await focus(page);
  await expect.poll(() => symbols(page)).toEqual(['TSLA']);
  await Promise.all(state.held.splice(0).map((release) => release()));
  await expect.poll(() => symbols(page)).toEqual(['TSLA']);
  await expect(region(page)).not.toContainText('AMD');
  expect(state.errors).toEqual([]);
});

test('changing membership rejects the old in-flight scope without changing identity', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', holdQuotesFor: 'AMD' });
  await page.goto('/');
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  state.members.alice = ['TSLA']; state.holdQuotesFor = null;
  await focus(page);
  await expect.poll(() => symbols(page)).toEqual(['TSLA']);
  await Promise.all(state.held.splice(0).map((release) => release()));
  await expect.poll(() => symbols(page)).toEqual(['TSLA']);
  expect(state.errors).toEqual([]);
});

test('a new membership hides already-successful old rows while its quotes are pending', async ({ page }) => {
  const state = await fixture(page, { username: 'alice' });
  await page.goto('/');
  await expect.poll(() => symbols(page)).toEqual(['AMD']);
  state.members.alice = ['TSLA']; state.holdQuotesFor = 'TSLA';
  await focus(page);
  await expect.poll(() => state.held.length).toBeGreaterThan(0);
  await expect(cards(page)).toHaveCount(0);
  await expect(region(page)).not.toContainText('PUBLIC');
  state.holdQuotesFor = null;
  await Promise.all(state.held.splice(0).map((release) => release()));
  await expect.poll(() => symbols(page)).toEqual(['TSLA']);
  expect(state.errors).toEqual([]);
});

test('missing personal quotes retain membership and prepared daily data refreshes that pool', async ({ page }) => {
  const state = await fixture(page, { username: 'alice', members: { alice: ['AMD', 'MISSING'] } });
  await page.goto('/');
  await expect.poll(() => symbols(page)).toEqual(['AMD', 'MISSING']);
  const missing = region(page).locator('[href="/stock/MISSING"]');
  await expect(missing).toContainText('—');
  await expect(missing).not.toContainText('0.00%');
  await expect.poll(() => state.statusReads.some((tickers) => tickers.includes('MISSING'))).toBe(true);
  const before = state.quoteReads.filter((read) => read.selection !== null).length;
  state.price = 123; state.prepared = true;
  await visibility(page);
  // 可见性恢复读一次，准备版本变化再强制读一次。
  await expect.poll(() => state.quoteReads.filter((read) => read.selection !== null).length).toBe(before + 2);
  await expect(region(page).locator('[href="/stock/AMD"]')).toContainText('123.00');
  await expect.poll(() => symbols(page)).toEqual(['AMD', 'MISSING']);
  // 再次读取同一准备版本不会不断刷新个人行情。
  const settled = state.quoteReads.filter((read) => read.selection !== null).length;
  const statusReads = state.statusReads.length;
  await visibility(page);
  await expect.poll(() => state.statusReads.length).toBeGreaterThan(statusReads);
  await expect(region(page).locator('[href="/stock/AMD"]')).toContainText('123.00');
  await expect.poll(() => state.quoteReads.filter((read) => read.selection !== null).length).toBe(settled + 1);
  // 观察已就绪后多个渲染周期，防止效果依赖变化引发持续重读。
  await page.waitForTimeout(350);
  expect(state.quoteReads.filter((read) => read.selection !== null).length).toBe(settled + 1);
  expect(state.errors).toEqual([]);
});
