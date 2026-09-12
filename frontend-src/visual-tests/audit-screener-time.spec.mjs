import { expect, test } from '@playwright/test';

// Real application components and browser interactions, with local API fixtures only.
async function fixture(page, options = {}) {
  const state = { failCatalysts: false, failAggressive: false, holdBalanced: false, failBalanced: false,
    releaseBalanced: null, batches: 0, scans: [], errors: [], ...options };
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const unavailable = () => route.fulfill({ status: 503, json: { message: '本地测试：读取失败' } });
    if (path === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: false, account: null } });
    if (path === '/api/quotes') return route.fulfill({ json: { quotes: [], status: { allowed: false, enabled: false, connected: false } } });
    if (path === '/api/market/status') return route.fulfill({ json: { market: 'open', session: 'regular', is_open: true, next_close: '2026-09-14T20:00:00Z' } });
    if (path === '/api/market/indices') return route.fulfill({ json: { indices: [] } });
    if (path === '/api/strength/market') return route.fulfill({ json: { avg_score: 80, stocks: [] } });
    if (path === '/api/strength/profiles') return route.fulfill({ json: { profiles: ['balanced', 'aggressive', 'conservative'], sectors: [] } });
    if (path === '/api/strength/scan') {
      state.scans.push(Object.fromEntries(url.searchParams));
      if (state.failAggressive && url.searchParams.get('profile') === 'aggressive') return unavailable();
      if (url.searchParams.get('profile') === 'balanced') {
        if (state.holdBalanced) await new Promise(resolve => { state.releaseBalanced = resolve; });
        if (state.failBalanced) return unavailable();
      }
      const now = new Date().toISOString();
      return route.fulfill({ json: { rows: [
        { ticker: 'AAA', name: url.searchParams.get('profile') === 'aggressive' ? '进取甲公司' : '甲公司', price: 100, final_score: 95, change_pct: 1, avg_dollar_volume_20d: 25_000_000, macro_fit_shadow: 80, score_short: 95, score_mid: 90 },
        { ticker: 'BBB', name: '乙公司', price: 110, final_score: 85, change_pct: 2, avg_dollar_volume_20d: 25_000_000, macro_fit_shadow: 20, score_short: 85, score_mid: 80 },
      ], universe_count: 2, screened_count: 2, source_status: 'active', snapshot_saved_at: now, scan_completed_at: now,
      cache_expires_at: new Date(Date.now() + 3_600_000).toISOString(), score_version: 'audit', _stale: false } });
    }
    if (path === '/api/catalysts/tickers/batch') {
      state.batches++;
      if (state.failCatalysts) return unavailable();
      const { tickers } = route.request().postDataJSON();
      return route.fulfill({ json: { results: Object.fromEntries(tickers.map(ticker => [ticker, {
        items: [{ title_zh: `${ticker} 公布新订单`, published_at: ticker === 'BBB' ? '2026-09-11T19:00:00Z' : '2026-09-11T18:00:00Z' }],
        has_more: false, summary: { bullish: 1, bearish: 0, pending: 0 },
      }])) } });
    }
    if (path.startsWith('/api/signals/stock/')) return route.fulfill({ json: { signals: [] } });
    return unavailable();
  });
  await page.goto('/screener');
  await expect(page.getByRole('heading', { name: '选股扫描', exact: true })).toBeVisible();
  return state;
}

async function scan(page) {
  await page.locator('button.scan-trigger').click();
  await expect(page.getByRole('table').getByText('AAA', { exact: true })).toBeVisible();
}

test('catalyst failure settles with retry and recovering news changes the real table order', async ({ page }) => {
  const state = await fixture(page, { failCatalysts: true });
  await scan(page);
  await expect.poll(() => state.batches).toBeGreaterThan(0);
  await page.getByRole('tab', { name: '最新催化', exact: true }).click();
  const status = page.getByRole('status').filter({ hasText: '催化摘要读取失败，暂按强度排序' });
  await expect(status).toBeVisible();
  await expect(page.getByText('正在准备排序数据 · 剩余')).toHaveCount(0);
  const before = state.batches;
  state.failCatalysts = false;
  await status.getByRole('button', { name: '重试', exact: true }).click();
  await expect(status).toHaveCount(0);
  await expect.poll(() => state.batches).toBe(before + 1);
  const first = page.locator('table tbody > tr').first();
  await expect(first).toContainText('BBB');
  expect(state.errors).toEqual([]);
});

test('Retry repeats the failed profile and preserves a draft edited after the failure', async ({ page }) => {
  const state = await fixture(page);
  await scan(page);
  state.failAggressive = true;
  const profile = page.getByRole('tablist', { name: '偏好', exact: true });
  await profile.getByRole('tab', { name: '进取', exact: true }).click();
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  await profile.getByRole('tab', { name: '稳健', exact: true }).click();
  state.failAggressive = false;
  const before = state.scans.filter(item => item.profile === 'aggressive').length;
  await page.getByRole('region', { name: '扫描结果', exact: true }).getByRole('button', { name: '重试', exact: true }).click();
  await expect.poll(() => state.scans.filter(item => item.profile === 'aggressive').length).toBe(before + 1);
  await expect(page.getByText('扫描数据不可用', { exact: true })).toHaveCount(0);
  await expect(profile.getByRole('tab', { name: '稳健', exact: true })).toHaveAttribute('aria-selected', 'true');
  expect(state.errors).toEqual([]);
});

test('native table row expands through a real keyboard button without activating from tooltips', async ({ page }) => {
  const state = await fixture(page);
  await scan(page);
  const row = page.getByRole('row').filter({ has: page.getByText('AAA', { exact: true }) }).first();
  await expect(row).not.toHaveAttribute('role', 'button');
  await expect(row).not.toHaveAttribute('tabindex');
  const toggle = row.getByRole('button', { name: '展开或收起 AAA 详情', exact: true });
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const id = await toggle.getAttribute('aria-controls');
  await expect(page.locator(`[id=${JSON.stringify(id)}]`)).toBeVisible();
  const tooltip = row.getByRole('button', { name: '分项强度', exact: true });
  await tooltip.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await toggle.focus();
  await page.keyboard.press('Space');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(state.errors).toEqual([]);
});

test('reset all clears the macro filter that caused an empty result', async ({ page }) => {
  const state = await fixture(page);
  await scan(page);
  await page.getByRole('button', { name: '宏观适配', exact: true }).click();
  await page.getByRole('tab', { name: '中性', exact: true }).click();
  await expect(page.getByText('当前条件无命中', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重置全部条件', exact: true }).click();
  await expect(page.getByText('当前条件无命中', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('table').getByText('AAA', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('BBB', { exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: '中性', exact: true })).toHaveAttribute('aria-selected', 'false');
  expect(state.errors).toEqual([]);
});

test('reset from aggressive actually scans balanced and preserves old profile through failure and retry', async ({ page }) => {
  const state = await fixture(page);
  const profile = page.getByRole('tablist', { name: '偏好', exact: true });
  await profile.getByRole('tab', { name: '进取', exact: true }).click();
  await scan(page);
  const method = page.getByRole('button', { name: /评分方法/ });
  await expect(method).toContainText('进取');
  await page.getByRole('button', { name: '宏观适配', exact: true }).click();
  await page.getByRole('tab', { name: '中性', exact: true }).click();
  await expect(page.getByText('当前条件无命中', { exact: true })).toBeVisible();
  state.holdBalanced = true;
  await page.getByRole('button', { name: '重置全部条件', exact: true }).click();
  await expect.poll(() => typeof state.releaseBalanced).toBe('function');
  expect(state.scans.at(-1).profile).toBe('balanced');
  await expect(profile.getByRole('tab', { name: '均衡', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(method).toContainText('进取');
  await expect(page.getByRole('table').getByText('进取甲公司', { exact: true })).toBeVisible();
  state.failBalanced = true;
  state.releaseBalanced();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  await expect(method).toContainText('进取');
  state.holdBalanced = false;
  state.failBalanced = false;
  const before = state.scans.filter(item => item.profile === 'balanced').length;
  await page.getByRole('region', { name: '扫描结果', exact: true }).getByRole('button', { name: '重试', exact: true }).click();
  await expect.poll(() => state.scans.filter(item => item.profile === 'balanced').length).toBe(before + 1);
  await expect(method).toContainText('均衡');
  await expect(page.getByRole('table').getByText('甲公司', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('进取甲公司', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test.describe('chart exchange time', () => {
  test.use({ timezoneId: 'Asia/Tokyo' });
  test('real chart axis, tooltip and indicator headers agree in a non-US timezone', async ({ page }) => {
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.goto('/visual-tests/support/indicator-harness.html?scenario=empty');
    const readout = page.locator('[data-indicator-header="volume"]');
    await expect(readout).toContainText('2026-06-09');
    await expect(readout).not.toContainText('2026-06-10');
    const initial = await page.evaluate(() => {
      const chart = window.indicatorTest.getChart(), option = chart.getOption();
      return { labels: option.xAxis[0].data, count: window.indicatorTest.bars.length };
    });
    expect(initial.labels.at(-1)).toBe('06-09');
    expect(initial.labels).toHaveLength(initial.count);
    await page.getByRole('tab', { name: '5分', exact: true }).click();
    await expect(readout).toContainText('2026-06-09 17:00 ET');
    // React readouts commit before the ECharts option update settles.
    await expect.poll(() => page.evaluate(() => window.indicatorTest.getChart()?.getOption().xAxis[0].data.at(-1)))
      .toBe('06-09 17:00');
    const detail = await page.evaluate(() => {
      const chart = window.indicatorTest.getChart(), option = chart.getOption();
      const index = window.indicatorTest.bars.length - 1;
      const title = option.tooltip[0].formatter([{ seriesType: 'candlestick', dataIndex: index }]);
      return { label: option.xAxis[0].data.at(-1), title, count: option.xAxis[0].data.length };
    });
    expect(detail.label).toBe('06-09 17:00');
    expect(detail.title).toContain('2026-06-09 17:00 ET');
    expect(detail.count).toBe(initial.count);
  });
});
