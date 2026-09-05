import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

// 本轮用户反馈的真实页面回归。由 review 配置提供隔离的 Vite 演示服务；
// 不连接生产行情，也不读取可能过期的 ../frontend 构建目录。
const evidenceDir = 'test-results/feedback-evidence';

async function noPageOverflow(page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth))
    .toBeLessThanOrEqual(1);
}

async function capture(page, name, focus) {
  await mkdir(evidenceDir, { recursive: true });
  if (focus) await focus.scrollIntoViewIfNeeded();
  // 只为截图等浏览器完成本帧布局；交互断言不依赖固定延迟。
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  await page.screenshot({ path: `${evidenceDir}/${name}.png`, animations: 'disabled' });
}

async function activeSignalCount(page) {
  const text = await page.getByRole('region', { name: '当日信号', exact: true })
    .getByText(/^\d+\s*个活跃(?:\s|$)/).innerText();
  return Number(text.match(/^\d+/)?.[0]);
}

async function indicatorDelta(tablist) {
  return tablist.evaluate((list) => {
    const tab = list.querySelector('[role="tab"][aria-selected="true"]');
    const indicator = tab?.parentElement?.querySelector('[data-glide-pill]');
    if (!tab || !indicator) return Number.POSITIVE_INFINITY;
    const target = tab.getBoundingClientRect();
    const actual = indicator.getBoundingClientRect();
    return Math.max(
      Math.abs(actual.x - target.x), Math.abs(actual.y - target.y),
      Math.abs(actual.width - target.width), Math.abs(actual.height - target.height),
    );
  });
}

async function scrollPosition(tablist) {
  return tablist.evaluate((list) => {
    // 内部标签条和外部带箭头容器可同时存在，寻找实际发生溢出的滚动层。
    let element = list;
    while (element) {
      const overflow = getComputedStyle(element).overflowX;
      if (/auto|scroll/.test(overflow) && element.scrollWidth > element.clientWidth + 2) {
        return { left: element.scrollLeft, max: element.scrollWidth - element.clientWidth };
      }
      element = element.parentElement;
    }
    return { left: 0, max: 0 };
  });
}

for (const width of [390, 1440]) {
  test.describe(`feedback layouts at ${width}px`, () => {
    test.use({
      viewport: { width, height: 1000 },
      isMobile: width === 390,
      hasTouch: width === 390,
      // reducedMotion 属于 BrowserContextOptions；顶层 use.reducedMotion 不会传给浏览器。
      contextOptions: { reducedMotion: 'reduce' },
    });

    test('breakout status and score filters retain independent pressed states and compact corners', async ({ page }) => {
      await page.goto('/breakouts');
      const toolbar = page.locator('[data-breakout-filters]');
      const statuses = toolbar.getByRole('group', { name: '状态筛选', exact: true });
      const scores = toolbar.getByRole('group', { name: '评分筛选', exact: true });
      await expect(statuses).toBeVisible();
      await expect(scores).toBeVisible();
      await expect.poll(() => activeSignalCount(page)).toBeGreaterThan(0);
      const baseline = await activeSignalCount(page);

      const all = statuses.getByRole('button', { name: '全部', exact: true });
      const confirmed = statuses.getByRole('button', { name: '已确认', exact: true });
      await expect(all).toHaveAttribute('aria-pressed', 'true');
      const selectionColors = await all.evaluate((button) => ({
        background: getComputedStyle(button).backgroundColor,
        foreground: getComputedStyle(button).color,
      }));
      expect(selectionColors.background).not.toBe('rgb(255, 255, 255)');
      expect(selectionColors.foreground).not.toBe('rgb(255, 255, 255)');
      await confirmed.focus();
      await page.keyboard.press('Space');
      await expect(confirmed).toHaveAttribute('aria-pressed', 'true');
      await expect(all).toHaveAttribute('aria-pressed', 'false');
      await expect(statuses.locator('[aria-pressed="true"]')).toHaveCount(1);
      await expect.poll(() => activeSignalCount(page)).toBeLessThanOrEqual(baseline);
      const confirmedCount = await activeSignalCount(page);

      const eighty = scores.getByRole('button', { name: /80\s*分以上/ });
      await eighty.focus();
      await page.keyboard.press('Enter');
      await expect(eighty).toHaveAttribute('aria-pressed', 'true');
      await expect(confirmed).toHaveAttribute('aria-pressed', 'true');
      await expect(scores.locator('[aria-pressed="true"]')).toHaveCount(1);
      await expect.poll(() => activeSignalCount(page)).toBeLessThanOrEqual(confirmedCount);

      // 两个维度都可以恢复，不能只更新按钮外观而遗留隐藏过滤条件。
      await all.click();
      await scores.getByRole('button', { name: '评分不限', exact: true }).click();
      await expect.poll(() => activeSignalCount(page)).toBe(baseline);
      const sixtyFive = scores.getByRole('button', { name: /65\s*分以上/ });
      await sixtyFive.click();
      await expect(scores.locator('[aria-pressed="true"]')).toHaveText(/65\s*分以上/);
      // aria-pressed 即时变化，CSS 颜色可能仍在本次过渡的首帧。
      // 用样式断言的自动重试等待最终状态，不以同步取样或固定休眠判断。
      for (const selected of [all, sixtyFive]) {
        await expect(selected).toHaveCSS('background-color', selectionColors.background);
        await expect(selected).toHaveCSS('color', selectionColors.foreground);
      }

      const geometry = await toolbar.getByRole('button').evaluateAll((buttons) => buttons.map((button) => {
        const style = getComputedStyle(button);
        return {
          radius: Math.max(...[
            style.borderTopLeftRadius, style.borderTopRightRadius,
            style.borderBottomLeftRadius, style.borderBottomRightRadius,
          ].map(Number.parseFloat)),
          height: button.getBoundingClientRect().height,
        };
      }));
      expect(geometry.length).toBeGreaterThanOrEqual(10);
      expect(geometry.every((button) => button.radius <= 8)).toBe(true);
      expect(geometry.every((button) => button.height >= (width === 390 ? 44 : 28))).toBe(true);
      await noPageOverflow(page);
      await capture(page, `breakouts-filters-${width}`, toolbar);
    });

    test('sector tabs support keyboard navigation and keep the indicator aligned after horizontal scrolling', async ({ page }) => {
      await page.goto('/sectors');
      const list = page.getByRole('tablist', { name: '板块切换', exact: true });
      await expect(list).toBeVisible();
      const tabs = list.getByRole('tab');
      await expect.poll(() => tabs.count()).toBeGreaterThan(2);
      await expect(list.locator('[role="tab"][tabindex="0"]')).toHaveCount(1);

      await list.locator('[role="tab"][aria-selected="true"]').focus();
      await page.keyboard.press('Home');
      await expect(tabs.first()).toBeFocused();
      await expect(tabs.first()).toHaveAttribute('aria-selected', 'true');
      await page.keyboard.press('ArrowRight');
      await expect(tabs.nth(1)).toBeFocused();
      await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');

      await page.keyboard.press('End');
      await expect(tabs.last()).toBeFocused();
      await expect(tabs.last()).toHaveAttribute('aria-selected', 'true');
      const position = await scrollPosition(list);
      // 紧凑标签在 1440px 可完整放下；这时不应为了满足测试制造滚动。
      // 手机视口必须覆盖真实横滚，其他视口按实际是否溢出检查。
      if (width === 390) expect(position.max).toBeGreaterThan(0);
      if (position.max > 0) {
        await expect.poll(async () => (await scrollPosition(list)).left).toBeGreaterThan(0);
      } else {
        expect(position.left).toBe(0);
        await expect(tabs.last()).toBeInViewport();
      }
      await expect.poll(() => indicatorDelta(list)).toBeLessThanOrEqual(1.5);

      // 窄屏在实际水平偏移后选择；宽屏同时验证无滚动时的正确落点。
      await page.keyboard.press('ArrowLeft');
      const previous = tabs.nth((await tabs.count()) - 2);
      await expect(previous).toBeFocused();
      await expect(previous).toHaveAttribute('aria-selected', 'true');
      await expect.poll(() => indicatorDelta(list)).toBeLessThanOrEqual(1.5);
      await expect(list.locator('[role="tab"][tabindex="0"]')).toHaveCount(1);
      await expect(list.locator('[data-glide-pill]')).toHaveCount(1);
      await expect(list.locator('[data-glide-pill]')).toHaveAttribute('aria-hidden', 'true');
      await noPageOverflow(page);
      await capture(page, `sectors-scrolled-selection-${width}`, previous);

      await page.keyboard.press('End');
      await page.keyboard.press('ArrowRight');
      await expect(tabs.first()).toBeFocused();
      await expect(tabs.first()).toHaveAttribute('aria-selected', 'true');
      await expect.poll(() => indicatorDelta(list)).toBeLessThanOrEqual(1.5);
    });

    test('home daily charts have a substantial plot and distinguish daily change from the displayed period', async ({ page }) => {
      await page.goto('/');
      await expect.poll(() => page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches))
        .toBe(true);
      const movers = page.getByRole('region', { name: '关注池异动', exact: true });
      const cards = movers.getByTestId('watchlist-mover-card');
      const figures = movers.getByTestId('watchlist-daily-trend');
      await expect(figures.first()).toBeVisible();
      await expect.poll(() => cards.count()).toBeGreaterThan(0);
      await expect(figures).toHaveCount(await cards.count());
      await expect(cards.first()).toContainText('当日');
      await expect(cards.first()).toContainText(/近\s*30\s*个交易日/);
      await expect(figures.first()).toHaveAccessibleName(/日线走势，\d{4}-\d{2}-\d{2} 至 \d{4}-\d{2}-\d{2}，区间涨跌/);
      await expect(figures.first().locator('figcaption')).toContainText('区间');
      await expect(figures.first().locator('figcaption')).toContainText(/\d{2}-\d{2}\s*—\s*\d{2}-\d{2}/);

      // figcaption 的涨跌箭头也是 SVG；只有 figure 直属 SVG 才是走势图。
      const charts = figures.locator(':scope > svg');
      await expect(charts).toHaveCount(await figures.count());
      const plots = await charts.evaluateAll((charts) => charts.map((chart) => {
        const curve = chart.querySelector('path[fill="none"]');
        return {
          width: chart.getBoundingClientRect().width,
          height: chart.getBoundingClientRect().height,
          path: curve?.getAttribute('d') ?? '',
          pathLength: curve?.getAttribute('pathLength'),
          dashOffset: curve ? Number.parseFloat(getComputedStyle(curve).strokeDashoffset) : null,
          hidden: chart.getAttribute('aria-hidden'),
        };
      }));
      expect(plots.every((plot) => plot.width >= 150 && plot.height >= 80)).toBe(true);
      expect(plots.every((plot) => /^M/.test(plot.path) && !/NaN|Infinity/.test(plot.path))).toBe(true);
      // 归一化整条曲线，不能用固定 300px 虚线把较长日线截成多段。
      // 本组采用减少动态效果，静态最终态必须完整显示、没有残余偏移。
      expect(plots.every((plot) => plot.pathLength === '1')).toBe(true);
      await expect.poll(() => charts.evaluateAll((charts) => charts.every((chart) => {
        const curve = chart.querySelector('path[fill="none"]');
        return curve?.getAttribute('pathLength') === '1'
          && Number.parseFloat(getComputedStyle(curve).strokeDashoffset) === 0;
      }))).toBe(true);
      expect(plots.every((plot) => plot.hidden === 'true')).toBe(true);
      await noPageOverflow(page);
      await capture(page, `home-daily-charts-${width}`, cards.first());
    });

    test('market SPX card opens the actual GSPC index rather than a stock fallback', async ({ page }) => {
      test.setTimeout(60_000);
      await page.goto('/market');
      const indices = page.getByRole('region', { name: '指数概览', exact: true });
      const spx = indices.getByRole('button', { name: /SPX.*详情/ });
      await expect(spx).toBeVisible();
      await noPageOverflow(page);
      await capture(page, `market-indices-${width}`, spx);
      await spx.focus();
      await page.keyboard.press('Enter');
      await expect.poll(() => decodeURIComponent(new URL(page.url()).pathname)).toBe('/stock/^GSPC');
      const heading = page.getByRole('heading', { level: 1 }).first();
      await expect(heading).toContainText('^GSPC');
      await expect(heading).not.toContainText('NVDA');
      await expect(heading).not.toContainText('英伟达');
      await expect(page.getByText('代码不存在', { exact: true })).toHaveCount(0);

      // 路由正确还不够：指数图必须有实际绘制内容，不能停在详情空壳。
      await expect.poll(() => page.locator('canvas').evaluateAll((canvases) => canvases.some((canvas) => {
        if (canvas.width < 100 || canvas.height < 100) return false;
        const context = canvas.getContext('2d');
        return context && context.getImageData(0, 0, canvas.width, canvas.height).data
          .some((value, index) => index % 4 === 3 && value > 0);
      }))).toBe(true);
      await noPageOverflow(page);
      await capture(page, `market-gspc-detail-${width}`, heading);
    });
  });
}
