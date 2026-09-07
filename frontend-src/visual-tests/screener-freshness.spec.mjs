import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const evidence = 'test-results/screener-evidence';

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
  await page.getByRole('button', { name: /开始扫描/ }).click();
}

test('F02 desktop 1440 owner refreshes a stale semiconductor snapshot', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await mkdir(evidence, { recursive: true });
  const errors = await openScreener(page);
  await page.screenshot({ path: `${evidence}/desktop-1440-before.png`, animations: 'disabled' });
  await selectSemiconductorsAndScan(page);
  await expect(page.getByText('历史结果').or(page.getByText('数据未刷新'))).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText('NVDA')).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/评分依据/)).toBeVisible();
  await expect(page.locator('body')).not.toContainText('定时更新');
  await page.screenshot({ path: `${evidence}/desktop-1440-after.png`, animations: 'disabled' });
  expect(errors.filter((message) => !/ResizeObserver|AbortError/.test(message))).toEqual([]);
});

test('F02 mobile 390 shows scan date on cards after refresh', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await mkdir(evidence, { recursive: true });
  const errors = await openScreener(page);
  await selectSemiconductorsAndScan(page);
  await expect(page.getByText('NVDA')).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/评分依据|扫描价|2026-/)).toBeVisible();
  await page.screenshot({ path: `${evidence}/mobile-390-after.png`, animations: 'disabled' });
  expect(errors.filter((message) => !/ResizeObserver|AbortError/.test(message))).toEqual([]);
});

for (const [locale, heading] of [
  ['zh', '选股扫描'],
  ['en', 'Screener'],
  ['ja', 'スクリーナー'],
]) {
  test(`F01 ${locale} 768 keeps the start-scan control visible`, async ({ page }) => {
    await page.addInitScript((code) => {
      localStorage.setItem('optix:locale', code);
    }, locale);
    await page.setViewportSize({ width: 768, height: 900 });
    await openScreener(page);
    await expect(page.getByRole('heading', { level: 1 })).toContainText(heading);
    await expect(page.getByRole('button', { name: /开始扫描|Start scan|スキャン開始/ })).toBeVisible();
  });
}
