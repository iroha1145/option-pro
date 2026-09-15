import { expect, test } from '@playwright/test';

test('chart 503 then 200 retry issues a new module request and shows the canvas', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.clock.setFixedTime(new Date('2026-09-12T12:00:00Z'));
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  await page.route('**/*', (route) => (
    ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort()
  ));

  let failChart = true;
  const chartRequests = [];
  await page.route(/EpsHatchChart/, async (route) => {
    const url = route.request().url();
    chartRequests.push({ url, failed: failChart, at: Date.now() });
    if (failChart) {
      await route.fulfill({ status: 503, body: 'chart unavailable', contentType: 'text/plain' });
      return;
    }
    await route.continue();
  });

  await page.goto('/earnings');
  await expect(page.getByRole('heading', { name: '财报日历', exact: true })).toBeVisible();
  const slot = page.locator('[data-eps-chart-slot]');
  await expect(slot).toBeAttached();
  await slot.scrollIntoViewIfNeeded();
  await expect(page.locator('[data-eps-chart-error]')).toBeVisible();
  await expect(page.getByText('列表与分析仍可查看。可单独重试图表。')).toBeVisible();
  expect(chartRequests.length).toBeGreaterThanOrEqual(1);
  const failedCount = chartRequests.filter((row) => row.failed).length;
  expect(failedCount).toBeGreaterThanOrEqual(1);

  failChart = false;
  const probe = await page.request.get(chartRequests[0].url.replace(/([?&])recover=\d+/, '$1recover=probe'));
  expect(probe.status()).toBe(200);

  const beforeRetry = chartRequests.length;
  await page.getByRole('button', { name: '重试图表', exact: true }).click();
  await expect(page.locator('[data-eps-chart], canvas')).toHaveCount(1, { timeout: 10_000 });
  await expect(page.locator('[data-eps-chart-error]')).toHaveCount(0);
  const retryRequests = chartRequests.slice(beforeRetry);
  expect(retryRequests.length, JSON.stringify({ chartRequests, beforeRetry })).toBeGreaterThanOrEqual(1);
  expect(retryRequests.every((row) => row.failed === false)).toBeTruthy();
  expect(retryRequests.some((row) => /recover=1/.test(row.url))).toBeTruthy();
  expect(errors.filter((message) => !/loading chunk|Failed to fetch|503|chart unavailable/i.test(message))).toEqual([]);
});
