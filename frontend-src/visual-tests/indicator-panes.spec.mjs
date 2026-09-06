import { expect, test } from '@playwright/test';

async function open(page, scenario = '') {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto(`/visual-tests/support/indicator-harness.html?scenario=${scenario}`);
  await expect(page.locator('[data-indicator-header="volume"]')).toBeVisible();
  await expect.poll(() => page.evaluate(() => !!window.indicatorTest?.getChart())).toBe(true);
  await page.waitForTimeout(400); // Initial layout/observer and chart-entry animation settle.
  return errors;
}
async function geometry(page) {
  return page.evaluate(() => {
    const chart = window.indicatorTest.getChart(), option = chart.getOption();
    const grids = option.grid.map((_, i) => { const r = chart.getModel().getComponent('grid', i).coordinateSystem.getRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; });
    const container = chart.getDom().getBoundingClientRect();
    const headers = [...document.querySelectorAll('[data-indicator-header]')].map(el => {
      const rect = el.getBoundingClientRect();
      return { top: rect.top - container.top, bottom: rect.bottom - container.top, scroll: el.scrollHeight, height: el.clientHeight };
    });
    const rightText = chart.getZr().storage.getDisplayList(true).filter(item => item.type === 'tspan' && item.style?.text).map(item => {
      const r = item.getBoundingRect().clone(); r.applyTransform(item.getComputedTransform());
      return { text: String(item.style.text), x: r.x, y: r.y, width: r.width, height: r.height };
    }).filter(r => r.x >= grids[0].x + grids[0].width && r.y >= grids[1].y && r.width > 0);
    return { grids, headers, rightText, height: container.height, fullWidth: document.documentElement.scrollWidth, viewport: innerWidth };
  });
}
for (const width of [320, 390, 768, 1440]) {
  test(`single and expanded indicators remain readable at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors = await open(page);
    await expect(page.locator('[data-indicator-header="macd"]')).toBeVisible();
    const single = await geometry(page);
    expect(single.grids).toHaveLength(3);
    await page.getByRole('tab', { name: '全部展开', exact: true }).click();
    await expect(page.locator('[data-indicator-header]')).toHaveCount(7);
    const all = await geometry(page);
    expect(all.grids).toHaveLength(8);
    expect(all.grids[0]).toEqual(single.grids[0]);
    expect(all.fullWidth).toBeLessThanOrEqual(all.viewport);
    for (let i = 1; i < all.grids.length; i++) {
      expect(all.grids[i].height).toBeGreaterThanOrEqual(i === 1 ? 72 : 108);
      expect(all.headers[i - 1].bottom).toBeLessThanOrEqual(all.grids[i].y + 1);
      expect(all.headers[i - 1].scroll).toBeLessThanOrEqual(all.headers[i - 1].height + 1);
      const ticks = all.rightText.filter(r => r.y + r.height / 2 >= all.grids[i].y && r.y + r.height / 2 <= all.grids[i].y + all.grids[i].height).sort((a,b) => a.y - b.y);
      for (let j = 1; j < ticks.length; j++) expect(ticks[j].y).toBeGreaterThanOrEqual(ticks[j - 1].y + ticks[j - 1].height - 1);
    }
    await page.screenshot({ path: info.outputPath(`indicators-all-${width}.png`), fullPage: true });
    await page.getByRole('tab', { name: '单项切换', exact: true }).click();
    await page.getByRole('combobox', { name: '选择副图指标' }).click();
    await page.getByRole('option', { name: 'OBV', exact: true }).click();
    await expect(page.locator('[data-indicator-header="obv"]')).toBeVisible();
    await expect(page.locator('[data-indicator-header="macd"]')).toHaveCount(0);
    await page.screenshot({ path: info.outputPath(`indicators-single-${width}.png`), fullPage: true });
    expect(errors).toEqual([]);
  });
}

test('crosshair values stay aligned, preserve missing warmup values, and do not rebuild the chart', async ({ page }) => {
  const errors = await open(page);
  await page.evaluate(() => {
    const chart = indicatorTest.getChart();
    chart.dispatchAction({ type: 'dataZoom', startValue: 0, endValue: 100 });
    indicatorTest.resetCounts();
  });
  const moveTo = async index => {
    const xy = await page.evaluate(index => {
      const chart = indicatorTest.getChart(), p = chart.convertToPixel({ gridIndex: 0 }, [index, indicatorTest.bars[index].c]);
      const r = chart.getDom().getBoundingClientRect(); return { x: r.left + p[0], y: r.top + p[1] };
    }, index);
    await page.mouse.move(xy.x, xy.y);
  };
  await moveTo(50);
  await expect(page.locator('[data-indicator-header="macd"]')).toContainText('光标读数');
  const value = (2 * Math.sin(50 / 9)).toFixed(2).replace(/\.?0+$/, '').replace('-', '−');
  await expect(page.locator('[data-indicator-value="MACD"]')).toContainText(value);
  await moveTo(10);
  await expect(page.locator('[data-indicator-value="MACD"]')).toContainText('—');
  await page.mouse.move(0, 0);
  await expect(page.locator('[data-indicator-header="macd"]')).toContainText('末根读数');
  expect((await page.evaluate(() => indicatorTest.counts())).fullUpdates).toBe(0);
  expect(errors).toEqual([]);
});

test('pane switching keeps hand drawings, original line labels, zoom and the live reference', async ({ page }) => {
  const errors = await open(page);
  await page.getByRole('button', { name: '水平线', exact: true }).click();
  const spot = await page.evaluate(() => {
    const chart = indicatorTest.getChart(), p = chart.convertToPixel({ gridIndex: 0 }, [90, 221]);
    const r = chart.getDom().getBoundingClientRect(); return { x: r.left + p[0], y: r.top + p[1] };
  });
  await page.mouse.click(spot.x, spot.y);
  await page.keyboard.press('Escape');
  const marks = () => page.evaluate(() => indicatorTest.getChart().getOption().series[0].markLine?.data ?? []);
  await expect.poll(async () => (await marks()).length).toBeGreaterThan(0);
  const before = await marks();
  await page.evaluate(() => indicatorTest.getChart().dispatchAction({ type: 'dataZoom', startValue: 60, endValue: 120 }));
  await page.getByRole('tab', { name: '全部展开', exact: true }).click();
  expect(await marks()).toEqual(before);
  expect(await page.evaluate(() => indicatorTest.getChart().getOption().dataZoom[0])).toMatchObject({ startValue: 60, endValue: 120 });
  await page.evaluate(() => indicatorTest.resetCounts());
  await page.getByRole('button', { name: '模拟实时价格' }).click();
  await expect.poll(() => page.evaluate(() => indicatorTest.counts().quoteUpdates)).toBeGreaterThan(2);
  expect((await page.evaluate(() => indicatorTest.counts())).fullUpdates).toBe(0);
  expect(await marks()).toEqual(before);
  expect(await page.evaluate(() => indicatorTest.getChart().getOption().dataZoom[0])).toMatchObject({ startValue: 60, endValue: 120 });
  await page.getByRole('button', { name: '模拟实时价格' }).click();
  expect(errors).toEqual([]);
});

test('area mode hides indicator UI and switching back restores the selected pane', async ({ page }) => {
  const errors = await open(page);
  await page.getByRole('combobox', { name: '选择副图指标' }).click();
  await page.getByRole('option', { name: 'RSI', exact: true }).click();
  await page.getByRole('tab', { name: '面积', exact: true }).click();
  await expect(page.locator('[data-indicator-controls]')).toHaveCount(0);
  await expect(page.locator('[data-indicator-header]')).toHaveCount(0);
  await page.getByRole('tab', { name: 'K 线', exact: true }).click();
  await expect(page.locator('[data-indicator-header="rsi"]')).toBeVisible();
  expect(errors).toEqual([]);
});

test('tiny indicator values remain distinguishable, empty panes do not invent a series', async ({ page }) => {
  let errors = await open(page, 'tiny');
  await expect(page.locator('[data-indicator-value="MACD"]')).toContainText('e-');
  expect(errors).toEqual([]);
  errors = await open(page, 'empty');
  await expect(page.locator('[data-indicator-header]')).toHaveCount(1);
  await expect(page.getByText('未启用指标副图')).toBeVisible();
  expect(errors).toEqual([]);
});

test('expanded workspace scrolls tall indicators instead of squeezing them', async ({ page }) => {
  const errors = await open(page);
  await page.getByRole('button', { name: '展开绘图工作区', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '绘图工作区' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('tab', { name: '全部展开', exact: true }).click();
  const sizes = await geometry(page);
  expect(sizes.grids.slice(2).every(grid => grid.height >= 108)).toBe(true);
  const scroll = dialog.locator('section[aria-label="AAPL K 线图"]');
  expect(await scroll.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
  await scroll.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expect(dialog.locator('[data-indicator-header="range_persistence"]')).toBeInViewport();
  expect(errors).toEqual([]);
});
