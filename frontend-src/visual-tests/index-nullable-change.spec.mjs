import { expect, test } from '@playwright/test';

async function fixture(page) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    let json;
    if (path === '/api/access/status') json = { access_mode: 'password', logged_in: false, account: null };
    else if (path === '/api/ai/status') json = { enabled: false };
    else if (path === '/api/runtime-settings') json = { settings: { ai: { manual_analysis_enabled: false } } };
    else if (path === '/api/quotes') json = { quotes: [], status: { allowed: false, enabled: false, connected: false } };
    else if (path === '/api/market/indices') json = {
      indices: [
        { symbol: '^GSPC', price: 6123.45, change_percent: null },
        { symbol: '^IXIC', price: 20000, change_percent: 0 },
        { symbol: '^DJI', price: null, change_percent: null },
        { symbol: '^N225', price: -1, change_percent: 0 },
        { symbol: '000001.SS', price: 0, change_percent: 0 },
      ], attempted: 5, succeeded: 2, data_limited: true, source_status: 'degraded', as_of: new Date().toISOString(),
    };
    else if (path === '/api/market/status') json = { market: 'closed', server_time: new Date().toISOString() };
    else if (path === '/api/signals/market') json = { signals: {}, scores: { top_score: 20, bottom_score: 30 } };
    else if (path === '/api/stocks/watchlist') json = { groups: [] };
    else return route.fulfill({ status: 503, json: { message: 'Fixture resource unavailable' } });
    return route.fulfill({ json });
  });
  return errors;
}

for (const pathname of ['/', '/market']) {
  test(`valid index prices with unknown change remain visible on ${pathname}`, async ({ page }) => {
    const errors = await fixture(page);
    await page.goto(pathname);
    const tape = page.getByRole('button', { name: /查看大盘强弱，SPX 最新价 6,123.45/ });
    await expect(tape).toHaveCount(1);
    await expect(tape).toContainText('6,123.45');
    await expect(tape).toContainText('—');
    await expect(tape).not.toContainText('0.00%');
    await expect(tape).toHaveAttribute('aria-label', /涨跌数据缺失/);
    const card = page.getByRole(pathname === '/' ? 'link' : 'button', { name: '标普500 SPX 详情', exact: true });
    await expect(card).toBeVisible();
    await expect(card).toContainText('6,123.45');
    await expect(card.getByLabel('涨跌数据缺失', { exact: true })).toHaveText('—');
    await expect(card).not.toContainText('0.00%');
    const flat = page.getByRole('button', { name: /查看大盘强弱，IXIC 最新价 20,000.00，持平/ });
    await expect(flat).toContainText('0.00%');
    for (const code of ['DJI', 'N225', 'SSE']) {
      await expect(page.getByRole('button', { name: new RegExp(`查看大盘强弱，${code} `) })).toHaveCount(0);
      await expect(page.getByRole(pathname === '/' ? 'link' : 'button', { name: new RegExp(`${code} 详情`) })).toHaveCount(0);
    }
    if (pathname === '/market') {
      const reading = page.getByRole('region', { name: '市场信号解读', exact: true });
      await expect(reading).toContainText('2 个主要指数 0 涨 0 跌 1 平，1 个涨跌未知');
      await expect(reading).not.toContainText('2 平');
    }
    expect(errors).toEqual([]);
  });
}
