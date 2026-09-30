import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

const fixtures = JSON.parse(readFileSync(new URL('../../docs/performance/artifacts/r7-controlled-fixtures.json', import.meta.url), 'utf8'));
const at = '2026-09-30T13:00:00Z';
async function fixture(page, { holdRadar = false, personal = false } = {}) {
  let releaseRadar;
  const radarGate = new Promise(resolve => { releaseRadar = resolve; });
  const errors = [];
  const scripts = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.resourceType() === 'script') scripts.push(request.url()); });
  await page.addInitScript(() => { localStorage.setItem('optix:locale', 'zh'); });
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (path === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: false,
      account: personal ? { logged_in: true, username: 'mobile-test' } : null } });
    if (path === '/api/breakouts/status') {
      if (holdRadar) await radarGate;
      return route.fulfill({ json: { enabled: true, market_session: 'regular', last_scan_at: at,
        next_session_at: new Date(Date.now() + 300_000).toISOString(), worker: { healthy: true } } });
    }
    if (path === '/api/breakouts/events') return route.fulfill({ json: { events: [], next_cursor: null } });
    if (personal && path === '/api/account/watchlist') return route.fulfill({ json: { tickers: Array.from({ length: 32 }, (_, i) => `S${String(i).padStart(3, '0')}`), max_tickers: 50 } });
    if (personal && path === '/api/stocks/watchlist') return route.fulfill({ json: { groups: [{ id: 'all', name: 'All', stocks: Array.from({ length: 32 }, (_, i) => ({
      ticker: `S${String(i).padStart(3, '0')}`, name: `Stock ${i}`, price: 100 + i, change_percent: i, quote_as_of: at,
    })) }] } });
    if (path === '/api/catalysts/news/1') return route.fulfill({ json: { item: fixtures['/api/catalysts/feed'].items[0] } });
    if (fixtures[path]) return route.fulfill({ json: fixtures[path] });
    return route.fulfill({ status: 503, json: { message: 'No fixture for this optional resource' } });
  });
  return { releaseRadar, errors, scripts };
}

for (const width of [320, 390]) {
  test(`portrait radar controls keep their position when status arrives at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    const state = await fixture(page, { holdRadar: true });
    await page.goto('/breakouts');
    const scope = page.getByRole('tablist', { name: '查看范围' });
    const filters = page.locator('[data-breakout-filters]');
    await expect(scope).toBeVisible();
    await expect(page.getByText('本轮暂无突破信号', { exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const before = await Promise.all([scope.boundingBox(), filters.boundingBox()]);
    state.releaseRadar();
    await expect(page.getByText('扫描已启用', { exact: true })).toBeVisible();
    const after = await Promise.all([scope.boundingBox(), filters.boundingBox()]);
    for (let i = 0; i < before.length; i++) {
      expect(Math.abs(after[i].x - before[i].x)).toBeLessThanOrEqual(1);
      expect(Math.abs(after[i].y - before[i].y)).toBeLessThanOrEqual(1);
    }
    await expect(page.locator('.page-enter')).toHaveCSS('animation-name', 'none');
    await expect(scope.locator('[data-glide-pill]')).toHaveCSS('transform', 'none');
    await scope.getByRole('tab', { name: '查看自选' }).click();
    await expect(scope.getByRole('tab', { name: '查看自选' })).toHaveAttribute('aria-selected', 'true');
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    expect(state.errors).toEqual([]);
  });
}

test('mobile table mode mounts one quote list and restores the desktop table on rotation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const state = await fixture(page, { personal: true });
  await page.goto('/watchlist');
  await expect(page.locator('main [data-quote-symbol="S000"]')).toBeVisible();
  await page.getByRole('tab', { name: '表格', exact: true }).click();
  await expect(page.locator('main table')).toHaveCount(0);
  await expect(page.locator('main [data-quote-symbol="S000"]')).toHaveCount(1);
  expect(await page.locator('main [data-quote-symbol="S000"] [style*="translateY"]').count()).toBe(0);
  await expect(page.locator('nav.glass')).toHaveCSS('backdrop-filter', 'none');
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('main table')).toBeVisible();
  await expect(page.locator('main [data-quote-symbol="S000"]')).toHaveCount(1);
  expect(await page.locator('main [data-quote-symbol="S000"] [style*="translateY"]').count()).toBeGreaterThan(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('main table')).toHaveCount(0);
  await expect(page.locator('main [data-quote-symbol="S000"]')).toHaveCount(1);
  expect(state.errors).toEqual([]);
});

test('news appears before inactive panels download; tabs and the drawer still work', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const state = await fixture(page);
  await page.goto('/catalysts');
  const headline = page.getByRole('heading', { name: '芯片企业发布最新进展', exact: true });
  await expect(headline).toBeVisible();
  await expect(headline.locator('xpath=ancestor::article')).toHaveCSS('opacity', '1');
  expect(state.scripts.filter(url => /\/(StocksPanel|CalendarPanel|SourcesPanel|NewsDrawer)\.tsx/.test(url))).toEqual([]);
  await page.getByRole('tab', { name: '数据源', exact: true }).click();
  await expect.poll(() => state.scripts.some(url => url.includes('/SourcesPanel.tsx'))).toBe(true);
  await page.getByRole('tab', { name: '新闻流', exact: true }).click();
  await expect(headline).toBeVisible();
  await headline.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(state.scripts.some(url => url.includes('/NewsDrawer.tsx'))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('chart callbacks do not reset zoom; mobile charts cap raster size and skip redundant resize', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('http://127.0.0.1:3027/visual-tests/support/mobile-performance.html');
    await expect(page.locator('canvas')).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.mobileChart.sets)).toBe(1);
    expect(await page.evaluate(() => window.mobileChart.resizes)).toBe(0);
    expect(await page.evaluate(() => window.mobileChart.chart.getOption().animation)).toBe(false);
    expect(await page.locator('canvas').evaluate(canvas => canvas.width / parseFloat(canvas.style.width))).toBe(2);
    const zoom = await page.evaluate(() => {
      window.mobileChart.chart.dispatchAction({ type: 'dataZoom', start: 25, end: 75 });
      return window.mobileChart.chart.getOption().dataZoom;
    });
    await page.getByRole('button', { name: 'Change handler' }).click();
    expect(await page.evaluate(() => window.mobileChart.sets)).toBe(1);
    expect(await page.evaluate(() => window.mobileChart.chart.getOption().dataZoom)).toEqual(zoom);
    await page.evaluate(() => window.mobileChart.chart.trigger('click', {}));
    await expect(page.locator('output')).toHaveText('2');
    await page.getByRole('button', { name: 'Remove handler' }).click();
    await page.evaluate(() => window.mobileChart.chart.trigger('click', {}));
    await expect(page.locator('output')).toHaveText('2');
    await page.getByRole('button', { name: 'Resize content' }).click();
    expect(await page.locator('.t-resize').evaluate(node => node.style.height)).toBe('');
    await page.locator('#chart-wrap').evaluate(node => { node.style.width = '300px'; });
    await expect.poll(() => page.evaluate(() => window.mobileChart.chart.getWidth())).toBe(300);
    expect(await page.evaluate(() => window.mobileChart.resizes)).toBe(1);
    await page.getByRole('button', { name: 'Unmount chart' }).click();
    expect(await page.evaluate(() => window.mobileChart.chart.isDisposed())).toBe(true);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
