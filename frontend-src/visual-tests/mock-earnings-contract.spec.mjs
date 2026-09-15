import { expect, test } from '@playwright/test';

test('real mock calendar keeps one report per company while retaining expansion and calendar selection', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.setFixedTime(new Date('2026-09-12T12:00:00Z'));
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto('/earnings');
  await expect(page.getByRole('heading', { name: '财报日历', exact: true })).toBeVisible();
  await expect(page.getByText('演示模式 · 当前行情与信号为示例数据', { exact: true })).toBeVisible();
  const fixture = await page.evaluate(async () => {
    const { getEarningsUpcoming } = await import('/src/mocks/fixtures2.ts');
    const rows = getEarningsUpcoming();
    return { count: rows.length, unique: new Set(rows.map(row => row.ticker)).size,
      nvdaDates: rows.filter(row => row.ticker === 'NVDA').map(row => row.date) };
  });
  expect(fixture.unique).toBe(fixture.count);
  expect(fixture.nvdaDates).toEqual(['2026-09-14']);
  const list = page.getByRole('region', { name: '即将公布', exact: true });
  const visibleRows = list.locator('[role="button"][aria-pressed]').filter({ visible: true });
  await expect(visibleRows).toHaveCount(24);
  await page.getByRole('button', { name: /显示更多/ }).click();
  await expect.poll(() => visibleRows.count()).toBeGreaterThan(24);

  await page.getByRole('tab', { name: '月', exact: true }).click();
  const calendar = page.getByRole('region', { name: '月历', exact: true });
  await expect(calendar.getByText('+1', { exact: true }).first()).toBeVisible();
  await calendar.getByRole('button', { name: '上个月', exact: true }).click();
  await expect(calendar.getByRole('button', { name: /^NVDA .*财报/ })).toHaveCount(0);
  await calendar.getByRole('button', { name: '下个月', exact: true }).click();
  await calendar.getByRole('button', { name: /^NVDA .*财报/ }).click();
  await expect(page.getByRole('complementary', { name: 'AI 影响分析', exact: true })).toContainText('财报日 2026-09-14');
  await expect(list.locator('[role="button"][aria-pressed="true"]').filter({ visible: true })).toHaveCount(1);
  await expect(list.locator('[role="button"][aria-pressed="true"]').filter({ visible: true })).toContainText('NVDA');

  await calendar.getByRole('button', { name: /^CRM .*财报/ }).click();
  await expect(page.getByRole('tab', { name: /^全部公司/ })).toHaveAttribute('aria-selected', 'true');
  await expect(list.locator('[role="button"][aria-pressed="true"]').filter({ visible: true })).toHaveCount(1);
  await expect(list.locator('[role="button"][aria-pressed="true"]').filter({ visible: true })).toContainText('CRM');
  expect(errors).toEqual([]);
});

for (const viewport of [
  { width: 320, height: 568 },
  { width: 768, height: 1024 },
  { width: 1536, height: 960 },
]) {
  test.describe(`expected move layout ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test('keeps the expected move label before and after expanding the list', async ({ page }) => {
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.clock.setFixedTime(new Date('2026-09-12T12:00:00Z'));
      await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
      await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
      await page.goto('/earnings');

      const list = page.getByRole('region', { name: '即将公布', exact: true });
      const visibleRows = list.locator('[aria-pressed]').filter({ visible: true });
      await expect(visibleRows).toHaveCount(24);
      if (viewport.width < 768) {
        await expect(visibleRows.first().getByText('预期波动', { exact: true })).toBeVisible();
      } else {
        await expect(list.locator(':scope > div').first().getByText('预期波动', { exact: true })).toBeVisible();
      }
      await expect(
        visibleRows.first().getByText(/^(?:±\d+(?:\.\d+)?%|报价不足|无合适到期合约|报价已过期|报价时间缺失|暂无估算|暂无数据|数据暂不可用)$/),
      ).toBeVisible();

      await page.getByRole('button', { name: /显示更多/ }).click();
      await expect.poll(() => visibleRows.count()).toBeGreaterThan(24);
      if (viewport.width < 768) {
        await expect(visibleRows.last().getByText('预期波动', { exact: true })).toBeVisible();
      } else {
        await expect(list.locator(':scope > div').first().getByText('预期波动', { exact: true })).toBeVisible();
      }
      await expect.poll(() => list.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
      expect(errors).toEqual([]);
    });
  });
}
