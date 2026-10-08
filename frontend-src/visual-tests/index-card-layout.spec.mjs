import { expect, test } from '@playwright/test';

async function fixture(page, waitForIndices) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    let json;
    if (path === '/api/access/status') json = { access_mode: 'password', logged_in: false, account: null };
    else if (path === '/api/ai/status') json = { enabled: false };
    else if (path === '/api/runtime-settings') json = { settings: { ai: { manual_analysis_enabled: false } } };
    else if (path === '/api/quotes') json = { quotes: [], status: { allowed: false, enabled: false, connected: false } };
    else if (path === '/api/market/indices') {
      await waitForIndices;
      json = {
        indices: [
          { symbol: '^GSPC', price: 6123.45, change_percent: 1.25 },
          { symbol: '^IXIC', price: 20123.45, change_percent: 0 },
          { symbol: '^DJI', price: 46123.45, change_percent: -1.25 },
          { symbol: '^N225', price: 39123.45, change_percent: 0 },
          { symbol: '000001.SS', price: 3123.45, change_percent: 0 },
        ], attempted: 5, succeeded: 5, data_limited: true, source_status: 'degraded', as_of: new Date().toISOString(),
      };
    } else if (path === '/api/market/status') json = { market: 'closed', server_time: new Date().toISOString() };
    else if (path === '/api/signals/market') json = { signals: {}, scores: { top_score: 20, bottom_score: 30 } };
    else if (path === '/api/stocks/watchlist') json = { groups: [] };
    else return route.fulfill({ status: 503, json: { message: 'Fixture resource unavailable' } });
    return route.fulfill({ json });
  });
  return errors;
}

for (const width of [320, 390]) {
  for (const pathname of ['/', '/market']) {
    test(`index cards preserve price and padding at ${width}px on ${pathname}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      let release;
      const indicesReady = new Promise(resolve => { release = resolve; });
      const errors = await fixture(page, indicesReady);
      await page.goto(pathname);
      const region = page.getByRole('region', { name: '市场指数', exact: true });
      const skeleton = region.locator('[data-state="loading"]');
      await expect(skeleton.first()).toBeVisible();
      const expectedColumns = width < 360 ? 2 : 3;
      expect(await skeleton.first().evaluate(el => getComputedStyle(el.parentElement).gridTemplateColumns.split(' ').length)).toBe(expectedColumns);
      release();
      const cards = region.locator('a[aria-label$="详情"], button[aria-label$="详情"]');
      await expect(cards).toHaveCount(5);
      await expect(cards.first().locator('.metric-value')).toHaveText('6,123.45');
      const measurements = await cards.evaluateAll(elements => elements.map(card => {
        const price = card.querySelector('.metric-value');
        const surface = card.getBoundingClientRect();
        const text = price.getBoundingClientRect();
        const style = getComputedStyle(card);
        return {
          label: card.getAttribute('aria-label'),
          left: text.left - surface.left,
          right: surface.right - text.right,
          paddingLeft: parseFloat(style.paddingLeft),
          paddingRight: parseFloat(style.paddingRight),
          fontSize: parseFloat(getComputedStyle(price).fontSize),
          columns: getComputedStyle(card.parentElement.parentElement).gridTemplateColumns.split(' ').length,
        };
      }));
      for (const card of measurements) {
        expect(card.columns, card.label).toBe(expectedColumns);
        expect(card.paddingLeft, card.label).toBeGreaterThanOrEqual(10);
        expect(card.paddingRight, card.label).toBeGreaterThanOrEqual(10);
        expect(card.left, card.label).toBeGreaterThanOrEqual(card.paddingLeft - 0.5);
        expect(card.right, card.label).toBeGreaterThanOrEqual(card.paddingRight - 0.5);
        expect(card.fontSize, card.label).toBeGreaterThanOrEqual(17);
      }
      expect(errors).toEqual([]);
    });
  }
}

test('market page lists US indices apart from other markets and counts only US ones in the reading', async ({ page }) => {
  const errors = await fixture(page, Promise.resolve());
  await page.goto('/market');
  await expect(page.getByRole('heading', { level: 1, name: '美股大盘强弱', exact: true })).toBeVisible();
  const region = page.getByRole('region', { name: '市场指数', exact: true });
  const labels = (scope) => scope.locator('button[aria-label$="详情"]').evaluateAll(els => els.map(el => el.getAttribute('aria-label')));
  await expect.poll(() => labels(region.getByRole('group', { name: '美股指数', exact: true })))
    .toEqual(['标普500 SPX 详情', '纳指综合 IXIC 详情', '道琼斯 DJI 详情']);
  expect(await labels(region.getByRole('group', { name: '其他市场', exact: true })))
    .toEqual(['日经225 N225 详情', '上证综指 SSE 详情']);
  // 1440 宽时两组并排成一行，两组的卡一样宽。卡片有错峰入场位移，等它停下再量顶边。
  const boxes = () => region.locator('button[aria-label$="详情"]').evaluateAll(els => els.map(el => {
    const rect = el.getBoundingClientRect();
    return { top: Math.round(rect.top), width: rect.width };
  }));
  await expect.poll(async () => new Set((await boxes()).map(box => box.top)).size).toBe(1);
  const widths = (await boxes()).map(box => box.width);
  expect(Math.max(...widths) - Math.min(...widths)).toBeLessThanOrEqual(2);
  // 日经、上证不进美股的涨跌统计：5 个指数里只数 3 个美股指数。
  const reading = page.getByRole('region', { name: '信号解读', exact: true });
  await expect(reading).toContainText('美股 3 个主要指数 1 涨 1 跌 1 平');
  await expect(reading).not.toContainText('5 个主要指数');
  expect(errors).toEqual([]);
});
