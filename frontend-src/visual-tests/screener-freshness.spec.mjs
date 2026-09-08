import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const evidence = process.env.SCREENER_EVIDENCE_DIR || 'test-results/screener-evidence';
const isolatedApi = 'http://127.0.0.1:8765';

async function openScreener(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/screener');
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
  return errors;
}

async function selectSemiconductorsAndScan(page) {
  await page.locator('[data-testid="screener-advanced-filters"] summary').click();
  await page.getByRole('button', { name: '半导体' }).click();
  await page.locator('button.scan-trigger').click();
}

async function readScreenerStats(request) {
  const response = await request.get(`${isolatedApi}/debug/screener-stats`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test.beforeEach(async ({ request }) => {
  const response = await request.post(`${isolatedApi}/debug/reset`, { data: {} });
  expect(response.ok()).toBeTruthy();
});

test.afterEach(async ({ request }, testInfo) => {
  await testInfo.attach('worker-and-provider-summary', {
    body: JSON.stringify(await readScreenerStats(request), null, 2),
    contentType: 'application/json',
  });
});

async function expectPublishedSemiconductors(request) {
  await expect.poll(async () => {
    const stats = await readScreenerStats(request);
    return stats.actions.find((action) => action.sector_id === 'semiconductors')?.status;
  }).toBe('completed');
  const stats = await readScreenerStats(request);
  const action = stats.actions.find((item) => item.sector_id === 'semiconductors');
  expect(stats.scan_count).toBe(1);
  expect(action.result.published).toBe(true);
  expect(action.result.parameters_hash).toBe(action.parameters_hash);
  expect(action.result.score_data_through).toContain(stats.score_data_through);
  return stats;
}

test('A07 a later valid software view wins over a late semiconductor refresh', async ({ page, request }) => {
  test.setTimeout(120_000);
  await request.post(`${isolatedApi}/debug/reset`, {
    data: { software_fresh: true, provider_delay: 1.5 },
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await openScreener(page);
  await page.locator('[data-testid="screener-advanced-filters"] summary').click();
  await page.getByRole('button', { name: '半导体' }).click();
  await page.locator('button.scan-trigger').click();
  await expect(page.locator('button.scan-trigger')).toBeDisabled();
  await expect.poll(async () => (await readScreenerStats(request)).post_count).toBe(1);
  await page.getByRole('button', { name: '半导体' }).click();
  await page.getByRole('button', { name: '软件' }).click();
  await expect(page.locator('button.scan-trigger')).toBeEnabled();
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText('MSFT').filter({ visible: true }).first()).toBeVisible({ timeout: 90_000 });
  await expectPublishedSemiconductors(request);
  await expect(page.locator('[data-quote-symbol="NVDA"]').filter({ visible: true })).toHaveCount(0);
  await expect(page.getByText('12.5')).toHaveCount(0);
  await expect(page.getByText(/命中/).filter({ visible: true }).first()).toContainText('1');
});

test('A08 ten same-parameter clicks share one refresh computation', async ({ page, request }) => {
  test.setTimeout(120_000);
  const before = await readScreenerStats(request);
  const posts = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/api/worker/actions/strength_refresh')) {
      posts.push(req.url());
    }
  });
  await openScreener(page);
  await page.locator('[data-testid="screener-advanced-filters"] summary').click();
  await page.getByRole('button', { name: '半导体' }).click();
  await page.locator('button.scan-trigger').evaluate((button) => {
    for (let index = 0; index < 10; index += 1) button.click();
  });
  await expect(page.getByText('NVDA').filter({ visible: true }).first()).toBeVisible({ timeout: 90_000 });
  const after = await expectPublishedSemiconductors(request);
  expect(after.scan_count - before.scan_count).toBe(1);
  expect(posts.length).toBe(1);
  const semiconductorIds = new Set(
    after.actions.filter((action) => action.sector_id === 'semiconductors').map((action) => action.request_id),
  );
  expect(semiconductorIds.size).toBe(1);
});

test('A08 two tabs coalesce onto one semiconductor action', async ({ browser, request }) => {
  test.setTimeout(120_000);
  const before = await readScreenerStats(request);
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  await openScreener(pageA);
  await openScreener(pageB);
  await pageA.locator('[data-testid="screener-advanced-filters"] summary').click();
  await pageB.locator('[data-testid="screener-advanced-filters"] summary').click();
  await pageA.getByRole('button', { name: '半导体' }).click();
  await pageB.getByRole('button', { name: '半导体' }).click();
  await Promise.all([
    pageA.locator('button.scan-trigger').click(),
    pageB.locator('button.scan-trigger').click(),
  ]);
  await expect(pageA.getByText('NVDA').filter({ visible: true }).first()).toBeVisible({ timeout: 90_000 });
  await expect(pageB.getByText('NVDA').filter({ visible: true }).first()).toBeVisible({ timeout: 90_000 });
  const after = await expectPublishedSemiconductors(request);
  expect(after.scan_count - before.scan_count).toBe(1);
  const semiconductorIds = new Set(
    after.actions.filter((action) => action.sector_id === 'semiconductors').map((action) => action.request_id),
  );
  expect(semiconductorIds.size).toBe(1);
  await contextA.close();
  await contextB.close();
});

test('C06 offline failure does not invent a scan clock, then one reconnect scan works', async ({ page, request }) => {
  test.setTimeout(120_000);
  const before = await readScreenerStats(request);
  await page.setViewportSize({ width: 1440, height: 900 });
  await openScreener(page);
  await page.context().setOffline(true);
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText(/扫描失败|Failed/).filter({ visible: true }).first()).toBeVisible();
  const lastScan = page.getByText('上次扫描').locator('xpath=following-sibling::span[1]');
  await expect(lastScan).toHaveText('—');
  await page.context().setOffline(false);
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText('AAPL').filter({ visible: true }).first()).toBeVisible({ timeout: 90_000 });
  await expect(lastScan).not.toHaveText('—');
  const after = await readScreenerStats(request);
  expect(after.post_count - before.post_count).toBeLessThanOrEqual(1);
  await expect(page.getByText(/评分依据/).filter({ visible: true }).first()).toBeVisible();
});

test('E02 provider failure keeps prior rows and timestamps without publishing', async ({ page, request }) => {
  await request.post(`${isolatedApi}/debug/reset`, { data: { provider_failure: true } });
  const before = await readScreenerStats(request);
  await openScreener(page);
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText('AAPL').filter({ visible: true }).first()).toBeVisible();
  const lastScan = page.getByText('上次扫描').locator('xpath=following-sibling::span[1]');
  const previousTime = await lastScan.textContent();
  await selectSemiconductorsAndScan(page);
  await expect(page.getByText('扫描数据不可用').filter({ visible: true }).first()).toBeVisible();
  await expect(page.getByText('AAPL').filter({ visible: true }).first()).toBeVisible();
  await expect(lastScan).toHaveText(previousTime);
  const after = await readScreenerStats(request);
  expect(after.scan_count).toBe(1);
  expect(after.actions).toHaveLength(1);
  expect(after.actions[0].status).toBe('failed');
  expect(after.snapshot_hashes).toEqual(before.snapshot_hashes);
});

test('F02 desktop 1440 owner refreshes a stale semiconductor snapshot', async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await mkdir(evidence, { recursive: true });
  const errors = await openScreener(page);
  await page.screenshot({ path: `${evidence}/desktop-1440-before.png`, animations: 'disabled' });
  await selectSemiconductorsAndScan(page);
  await expect(page.getByText('NVDA').filter({ visible: true }).first()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/评分依据/).filter({ visible: true }).first()).toBeVisible();
  await expect(page.getByText(/使用已有评分|命中/).filter({ visible: true }).first()).toBeVisible();
  const stats = await expectPublishedSemiconductors(request);
  await expect(page.getByText(/评分依据/).filter({ visible: true }).first()).toContainText(stats.score_data_through);
  await expect(page.locator('[data-quote-symbol="NVDA"]').filter({ visible: true }).first()).not.toContainText('12.5');
  await page.screenshot({ path: `${evidence}/desktop-1440-after.png`, animations: 'disabled' });
  expect(errors.filter((message) => !/ResizeObserver|AbortError/.test(message))).toEqual([]);
});

test('F02 mobile 390 shows scan date on cards after refresh', async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await mkdir(evidence, { recursive: true });
  const errors = await openScreener(page);
  await selectSemiconductorsAndScan(page);
  const nvda = page.getByText('NVDA').filter({ visible: true }).first();
  await expect(nvda).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/评分依据/).filter({ visible: true }).first()).toBeVisible();
  const scanPrice = page.getByText(/扫描价/).filter({ visible: true }).first();
  await expect(scanPrice).toBeVisible();
  await expect(page.getByText(/上次扫描/).filter({ visible: true }).first()).toBeVisible();
  const stats = await expectPublishedSemiconductors(request);
  await expect(page.getByText(/评分依据/).filter({ visible: true }).first()).toContainText(stats.score_data_through);
  await expect(page.locator('[data-quote-symbol="NVDA"]').filter({ visible: true }).first()).not.toContainText('12.5');
  await scanPrice.scrollIntoViewIfNeeded();
  await page.locator('[data-quote-symbol="NVDA"]').filter({ visible: true }).first().screenshot({
    path: `${evidence}/mobile-390-nvda-card.png`,
    animations: 'disabled',
  });
  await page.screenshot({ path: `${evidence}/mobile-390-after.png`, animations: 'disabled', fullPage: true });
  expect(errors.filter((message) => !/ResizeObserver|AbortError/.test(message))).toEqual([]);
});

for (const [locale, heading] of [
  ['zh', '选股扫描'],
  ['en', 'Screener'],
  ['ja', 'スクリーナー'],
]) {
  for (const [width, height] of [
    [320, 720],
    [390, 844],
    [768, 900],
    [1440, 900],
  ]) {
    test(`F01 ${locale} ${width} keeps the start-scan control visible`, async ({ page }) => {
      await page.addInitScript((code) => {
        localStorage.setItem('optix:locale', code);
      }, locale);
      await page.setViewportSize({ width, height });
      await openScreener(page);
      await expect(page.getByRole('heading', { level: 1 })).toContainText(heading);
      await expect(page.locator('button.scan-trigger')).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
      expect(overflow, `${locale} ${width} must not clip horizontally`).toBeTruthy();
    });
  }
}
