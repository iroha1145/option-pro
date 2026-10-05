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
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeAttached();
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
  const sector = page.getByRole('button', { name: /平均收益/ }).first();
  await expect(sector).toBeVisible();
  await sector.focus();
  await page.keyboard.press('Enter');
  await expect(sector).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('table th button button, table th button [role="button"]')).toHaveCount(0);
});

test('history charts expose a keyboard cursor that drives the header readout', async ({ page }) => {
  await page.goto('/cta');
  const chart = page.getByRole('slider', { name: '估算仓位历史，左右键逐日查看' });
  await expect(chart).toBeVisible();
  const frame = chart.locator('xpath=..');
  await expect(frame).toContainText('0 为多空分界 · 最新');
  const last = await chart.getAttribute('aria-valuemax');
  await expect(chart).toHaveAttribute('aria-valuenow', last);
  await chart.focus();
  await page.keyboard.press('Home');
  await expect(chart).toHaveAttribute('aria-valuenow', '0');
  await expect(frame).toContainText('0 为多空分界 · 当日');
  await page.keyboard.press('ArrowRight');
  await expect(chart).toHaveAttribute('aria-valuenow', '1');
  await page.keyboard.press('Escape');
  await expect(chart).toHaveAttribute('aria-valuenow', last);
  await expect(frame).toContainText('0 为多空分界 · 最新');
});

for (const kind of ['macro', 'position']) {
  for (const input of ['keyboard', 'mouse']) {
    test(`${kind} history preserves the visible ${input} cursor across value refreshes`, async ({ page }) => {
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto('/visual-tests/support/history-cursor.html');
      const frame = page.getByTestId(`${kind}-chart`);
      const chart = frame.getByRole('slider');
      await chart.focus();
      if (input === 'keyboard') {
        await chart.press('Home');
        await chart.press('ArrowRight');
        await chart.press('ArrowRight');
      } else {
        await page.evaluate(() => document.fonts.ready);
        const pixel = await page.evaluate((kind) => window.historyCursorHarness.pixel(kind, 2), kind);
        await page.mouse.move(pixel.x, pixel.y);
      }
      await expect(chart).toHaveAttribute('aria-valuenow', '2');
      await page.evaluate(() => window.historyCursorHarness.refresh());
      await expect(chart).toHaveAttribute('aria-valuenow', '2');
      await expect(chart).toHaveAttribute('aria-valuetext', new RegExp(`2026-01-03.*${kind === 'macro' ? '70.0' : '\\+10.0'}`));
      await expect(frame).toContainText('2026-01-03');
      await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind), kind)).toEqual({ pointerStatus: 'show', emphasized: true });
      await expect(frame.locator('.cloud-chart-tooltip')).toBeVisible();
      await expect(frame.locator('.cloud-chart-tooltip')).toContainText('2026-01-03');

      if (input === 'keyboard') await chart.press('Escape');
      else await page.getByRole('button', { name: '离开图表', exact: true }).focus();
      await page.evaluate(() => window.historyCursorHarness.refresh());
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await expect(chart).toHaveAttribute('aria-valuenow', '119');
      await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind), kind)).toEqual({ pointerStatus: 'hide', emphasized: false });
      await expect(frame.locator('.cloud-chart-tooltip')).toBeHidden();
      expect(errors).toEqual([]);
    });
  }

  test(`${kind} history discards old tooltip callbacks across a value refresh and a date change`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto('/visual-tests/support/history-cursor.html');
    const frame = page.getByTestId(`${kind}-chart`);
    const chart = frame.getByRole('slider');
    await chart.focus();
    await chart.press('Home');
    await chart.press('ArrowRight');
    await expect(chart).toHaveAttribute('aria-valuenow', '1');
    const last = await page.evaluate((kind) => window.historyCursorHarness.refreshThenReplace(kind, 30, 90), kind);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(chart).toHaveAttribute('aria-valuenow', '29');
    await expect(frame).toContainText(last);
    await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind), kind)).toEqual({ pointerStatus: 'hide', emphasized: false });
    expect(errors).toEqual([]);
  });

  test(`${kind} history discards a queued tooltip refresh when dates change`, async ({ page }) => {
    await page.goto('/visual-tests/support/history-cursor.html');
    const frame = page.getByTestId(`${kind}-chart`);
    const chart = frame.getByRole('slider');
    await expect(chart).toHaveAttribute('aria-valuemax', '119');
    await chart.focus();
    await chart.press('Home');
    await chart.press('ArrowRight');
    await expect(chart).toHaveAttribute('aria-valuenow', '1');
    const last = await page.evaluate((kind) => window.historyCursorHarness.replaceAfterResize(kind, 30, 90), kind);
    // Cross the queued refresh and the next paint; this checks the final readout, not its reset flash.
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(chart).toHaveAttribute('aria-valuenow', '29');
    await expect(frame).toContainText(last);
    await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind), kind)).toEqual({ pointerStatus: 'hide', emphasized: false });
  });

  test(`${kind} history replaces shorter and same-length dates safely while the cursor is active`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto('/visual-tests/support/history-cursor.html');
    const frame = page.getByTestId(`${kind}-chart`);
    const chart = frame.getByRole('slider');
    await expect(chart).toHaveAttribute('aria-valuemax', '119');
    const pixel = await page.evaluate((kind) => window.historyCursorHarness.pixel(kind, 100), kind);
    await page.mouse.move(pixel.x, pixel.y);
    await expect(chart).toHaveAttribute('aria-valuenow', '100');
    const lastShort = await page.evaluate(() => window.historyCursorHarness.replace(30, 90));
    await expect(chart).toHaveAttribute('aria-valuemax', '29');
    await expect(chart).toHaveAttribute('aria-valuenow', '29');
    await expect(chart).toHaveAttribute('aria-valuetext', new RegExp(lastShort));
    await expect(frame).toContainText(lastShort);
    await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind).pointerStatus, kind)).toBe('hide');

    await chart.focus();
    await chart.press('Home');
    await expect(chart).toHaveAttribute('aria-valuenow', '0');
    const lastShifted = await page.evaluate(() => window.historyCursorHarness.replace(30, 120));
    await expect(chart).toHaveAttribute('aria-valuenow', '29');
    await expect(chart).toHaveAttribute('aria-valuetext', new RegExp(lastShifted));
    await expect(frame).toContainText(lastShifted);
    await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind).pointerStatus, kind)).toBe('hide');
    expect(errors).toEqual([]);
  });

  test(`${kind} history Escape and blur release the visible axis pointer and point emphasis`, async ({ page }) => {
    await page.goto('/visual-tests/support/history-cursor.html');
    const chart = page.getByTestId(`${kind}-chart`).getByRole('slider');
    await expect(chart).toBeVisible();
    // Keep the real mouse outside the chart so only the keyboard owns this cursor.
    await page.mouse.move(0, 0);
    for (const release of ['Escape', 'blur']) {
      await chart.focus();
      await chart.press('Home');
      await expect(chart).toHaveAttribute('aria-valuenow', '0');
      await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind).pointerStatus, kind)).toBe('show');
      await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind).emphasized, kind)).toBe(true);
      if (release === 'Escape') await chart.press('Escape');
      else await page.getByRole('button', { name: '离开图表', exact: true }).focus();
      await expect(chart).toHaveAttribute('aria-valuenow', '119');
      await expect.poll(() => page.evaluate((kind) => window.historyCursorHarness.inspect(kind), kind)).toEqual({ pointerStatus: 'hide', emphasized: false });
    }
  });
}
