import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const routes = ['/', '/watchlist', '/screener', '/breakouts', '/sectors', '/earnings', '/catalysts', '/market', '/cta', '/stock/AAPL'];
const harness = '/visual-tests/support/ui-harness.html';

for (const width of [390, 768, 1440]) {
  test(`all research pages remain usable at ${width}px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    for (const route of routes) {
      await page.goto(route);
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
      await expect(page.getByText('演示模式 · 当前行情与信号为示例数据')).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      if (route === '/' || route === '/screener' || route === '/stock/AAPL') {
        await expect(page.locator('#main-content [data-state="loading"]')).toHaveCount(0);
        if (route === '/stock/AAPL') {
          await expect.poll(() => page.locator('canvas').evaluateAll((canvases) => canvases.filter((c) => {
            if (c.width <= 100 || c.height <= 100) return false;
            const ctx = c.getContext('2d');
            return ctx && ctx.getImageData(0, 0, c.width, c.height).data.some((v, i) => i % 4 === 3 && v > 0);
          }).length)).toBeGreaterThan(0);
        }
        // Evidence should show settled content, including Framer's short opacity fade.
        // Behaviour assertions above do not depend on this screenshot-only delay.
        await page.waitForTimeout(700);
        await mkdir('test-results/review-evidence', { recursive: true });
        await page.screenshot({ path: `test-results/review-evidence/${width}-${route.replaceAll('/', '-') || 'home'}.png`, animations: 'disabled' });
      }
    }
    expect(errors).toEqual([]);
  });
}

test('skip link moves keyboard focus to the content', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();
});

test('select escapes clipped parent, restores focus and preserves numeric values', async ({ page }) => {
  await page.goto(harness);
  const trigger = page.getByRole('combobox', { name: '测试选择' });
  await trigger.focus();
  await page.keyboard.press('ArrowDown');
  const list = page.getByRole('listbox');
  await expect(list).toBeVisible();
  await expect(page.getByRole('option', { name: '全部', exact: true })).toBeFocused();
  await page.keyboard.press('End');
  await expect(page.getByRole('option', { name: '前二十项', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status', { name: '选择结果' })).toHaveText('20');
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(page.getByRole('option', { name: '前二十项', exact: true })).toBeFocused();
  await page.keyboard.press('Home');
  await expect(page.getByRole('option', { name: '全部', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status', { name: '选择结果' })).toHaveText('0');
  await trigger.click();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(list).toBeHidden();
});

test('table retains rows, real keyboard controls and missing values last in both directions', async ({ page }) => {
  await page.goto(harness);
  const rows = page.getByRole('table').locator('tbody tr');
  await expect(rows).toHaveCount(3);
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(4);
  await page.getByRole('button', { name: '强度', exact: true }).click();
  await expect(rows.last()).toContainText('B');
  await page.getByRole('button', { name: '强度', exact: true }).click();
  await expect(rows.first()).toContainText('C');
  await expect(rows.last()).toContainText('B');
  const action = page.getByRole('button', { name: 'C', exact: true });
  await action.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status', { name: '选中标的' })).toHaveText('C');
});

test('toast bursts stay bounded and reading pauses dismissal', async ({ page }) => {
  await page.goto(harness);
  await page.getByRole('button', { name: '批量通知' }).click();
  await expect(page.locator('.t-toast')).toHaveCount(4);
  await expect(page.locator('.t-toast').last()).toContainText('通知 12');
  await page.getByRole('button', { name: '错误通知' }).click();
  const notice = page.getByRole('alert');
  await expect(notice).toContainText('加载失败');
  await expect(notice).toBeVisible();
  await expect(notice).toHaveAttribute('data-open', 'true');
  const close = notice.getByRole('button', { name: '关闭通知' });
  await close.focus();
  await expect(close).toBeFocused();
  await page.waitForTimeout(8300);
  await expect(close).toBeFocused();
  await expect(notice).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(notice).toBeHidden();
});

test('stock chart paints after the ECharts security upgrade', async ({ page }) => {
  await page.goto('/stock/AAPL');
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
  await expect.poll(() => page.locator('canvas').evaluateAll((canvases) => canvases.filter((c) => {
    if (c.width <= 100 || c.height <= 100) return false;
    const ctx = c.getContext('2d');
    return ctx && ctx.getImageData(0, 0, c.width, c.height).data.some((v, i) => i % 4 === 3 && v > 0);
  }).length)).toBeGreaterThan(0);
  const area = page.getByRole('tab', { name: '面积', exact: true });
  await area.click();
  await expect(area).toHaveAttribute('aria-selected', 'true');
});


test('stock and sector tables retain independent keyboard actions', async ({ page }) => {
  await page.goto('/watchlist');
  const tableTab = page.getByRole('tab', { name: '表格', exact: true });
  await expect(tableTab).toBeVisible();
  await tableTab.click();
  const stock = page.getByRole('link', { name: /打开 .* 详情/ }).first();
  await stock.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/stock\//);
  await page.goto('/sectors');
  const list = page.getByRole('tab', { name: '列表', exact: true });
  await expect(list).toBeVisible();
  await list.click();
  const sector = page.locator('table tbody button[aria-pressed]').first();
  await sector.focus();
  await page.keyboard.press('Enter');
  await expect(sector).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('table th button button, table th button [role="button"]')).toHaveCount(0);
});
