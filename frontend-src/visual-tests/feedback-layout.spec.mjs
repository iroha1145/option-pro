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

    test('catalyst cards retain news, history and keyboard view navigation', async ({ page }) => {
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('/catalysts');
      await expect(page.getByText('数据与分析说明', { exact: true })).toHaveCount(0);
      const focus = page.getByRole('region', { name: '热点追踪', exact: true });
      await expect(focus.getByRole('heading', { name: '逐股评估', exact: true })).toBeVisible();
      await expect(focus).toContainText('依据不足');
      await noPageOverflow(page);
      await capture(page, `catalyst-focus-${width}`, focus);

      const history = focus.getByRole('button', { name: /与上一轮成功的热点分析对照/ });
      await history.click();
      await expect(history).toHaveAttribute('aria-expanded', 'true');
      await expect(focus.getByRole('heading', { name: '降息交易回摆', exact: true })).toBeVisible();
      await noPageOverflow(page);
      await history.click();

      const hotspot = page.getByRole('region', { name: '市场热点', exact: true })
        .getByRole('button', { name: /查看代表新闻/ }).first();
      await hotspot.focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('dialog')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);

      const tickerFilter = page.getByPlaceholder('股票代码', { exact: true });
      const slowDevice = await page.context().newCDPSession(page);
      try {
        // 模拟较慢的设备：地址更新不能造成丢字，也不能抢走输入焦点。
        await slowDevice.send('Emulation.setCPUThrottlingRate', { rate: 8 });
        await tickerFilter.focus();
        await page.keyboard.type('NVDA', { delay: 60 });
        await expect(tickerFilter).toHaveValue('NVDA');
        await expect(tickerFilter).toBeFocused();
        await expect(page).toHaveURL(/ticker=NVDA/);
        await page.getByRole('button', { name: '取消股票代码筛选', exact: true }).click();
        await expect(tickerFilter).toHaveValue('');
        await expect(page).not.toHaveURL(/ticker=/);
        await tickerFilter.focus();
        await page.keyboard.type('nvda');
        await expect(tickerFilter).toHaveValue('NVDA');
        await expect(tickerFilter).toBeFocused();
        await expect(page).toHaveURL(/ticker=NVDA/);
      } finally {
        await slowDevice.send('Emulation.setCPUThrottlingRate', { rate: 1 });
        await slowDevice.detach();
      }
      const views = page.getByRole('tablist', { name: '新闻栏目', exact: true });
      await expect(views.getByRole('tab')).toHaveText(['新闻列表', '股票影响', '经济日历']);
      await views.getByRole('tab', { name: '新闻列表', exact: true }).focus();
      await page.keyboard.press('ArrowRight');
      await expect(views.getByRole('tab', { name: '股票影响', exact: true })).toHaveAttribute('aria-selected', 'true');
      await expect(views.getByRole('tab', { name: '股票影响', exact: true })).toBeFocused();
      await expect(page).toHaveURL(/tab=stocks/);
      await expect(page).toHaveURL(/ticker=NVDA/);
      await page.keyboard.press('End');
      await expect(views.getByRole('tab', { name: '经济日历', exact: true })).toHaveAttribute('aria-selected', 'true');
      await expect(views.getByRole('tab', { name: '经济日历', exact: true })).toBeFocused();
      await expect(page).toHaveURL(/tab=calendar/);
      // 消息来源收在栏目行右侧的「更多」菜单里：键盘打开、选中后焦点回到触发器，Esc 关闭并还焦点。
      const moreMenu = page.getByTestId('catalyst-more-menu');
      const moreTrigger = moreMenu.getByRole('button');
      await moreTrigger.focus();
      await page.keyboard.press('ArrowDown');
      await expect(page.getByRole('menuitem', { name: '消息来源', exact: true })).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(/tab=sources/);
      await expect(page).toHaveURL(/ticker=NVDA/);
      await expect(moreTrigger).toHaveText('消息来源');
      await expect(moreTrigger).toBeFocused();
      await moreTrigger.click();
      await expect(page.getByRole('menu', { name: '更多', exact: true })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('menu')).toHaveCount(0);
      await expect(moreTrigger).toBeFocused();
      // 停在消息来源时三个栏目都没选中：第一项仍可用 Tab 进入，键盘能回到新闻列表。
      await page.keyboard.press('Shift+Tab');
      await expect(views.getByRole('tab', { name: '新闻列表', exact: true })).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(views.getByRole('tab', { name: '新闻列表', exact: true })).toHaveAttribute('aria-selected', 'true');
      await expect(page).not.toHaveURL(/tab=sources/);
      await noPageOverflow(page);
      // 切换到另一页面时仍应返回页头，筛选焦点修复不能影响正常导航。
      await page.getByRole('link', { name: 'Optix Pro 首页', exact: true }).click();
      await expect(page).toHaveURL(/\/$/);
      await expect(page.locator('#main-content')).toBeFocused();
      await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
      expect(errors).toEqual([]);
    });

    test('stale sector surfaces preserve cached rows, sorting and retry feedback', async ({ page }) => {
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto('/visual-tests/support/status-notice-harness.html');
      await expect(page.getByText('数据过期', { exact: true })).toBeVisible();
      await expect(page.getByRole('status').filter({ hasText: '数据暂未更新' })).toContainText('以下为最近一次结果');
      const table = page.getByRole('table', { name: '行业隐含波动率排名表', exact: true });
      await expect(table.locator('tbody tr')).toHaveCount(2);
      await expect(table.locator('tbody tr').first()).toContainText('AAPL');
      await page.getByRole('button', { name: /切换排序/ }).click();
      await expect(table.locator('tbody tr').first()).toContainText('MSFT');
      await noPageOverflow(page);
      await capture(page, `sector-stale-notices-${width}`, page.getByText('数据过期', { exact: true }));
      const retry = page.getByRole('status').filter({ hasText: '刷新失败' }).getByRole('button', { name: '重试', exact: true });
      await retry.click();
      await expect(retry).toBeDisabled();
      await expect(page.getByLabel('刷新状态')).toHaveText('刷新中');
      await expect(table.locator('tbody tr')).toHaveCount(2);
      expect(errors).toEqual([]);
    });

    test('breakout status dropdown and score filters stay independent, summarised, and compact', async ({ page }) => {
      await page.goto('/breakouts');
      const toolbar = page.locator('[data-breakout-filters]');
      // 状态收成一个下拉：触发器写着当前状态，七个选项一个不少。
      const status = toolbar.getByRole('combobox', { name: '状态筛选', exact: true });
      await expect(status).toBeVisible();
      await expect(status).toContainText('全部');
      await expect.poll(() => activeSignalCount(page)).toBeGreaterThan(0);
      const baseline = await activeSignalCount(page);

      await status.click();
      await expect(page.getByRole('option')).toHaveCount(7);
      await page.getByRole('option', { name: '已确认', exact: true }).click();
      await expect(status).toContainText('已确认');
      await expect.poll(() => activeSignalCount(page)).toBeLessThanOrEqual(baseline);
      const confirmedCount = await activeSignalCount(page);

      // 键盘也能用：焦点在下拉上按方向键打开，方向键挪到「保持中」回车选中；再打开按 Esc 关闭，焦点回到下拉。
      await status.focus();
      await page.keyboard.press('ArrowDown');
      await expect(page.getByRole('listbox')).toBeVisible();
      await expect(page.getByRole('option', { name: '已确认', exact: true })).toBeFocused();
      await page.keyboard.press('ArrowDown');
      await expect(page.getByRole('option', { name: '保持中', exact: true })).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(status).toContainText('保持中');
      await expect(status).toBeFocused();
      await page.keyboard.press('ArrowDown');
      await expect(page.getByRole('listbox')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('listbox')).toHaveCount(0);
      await expect(status).toBeFocused();
      await expect(status).toContainText('保持中');
      // 回到「已确认」，后面的评分筛选沿用原来的基准
      await status.click();
      await page.getByRole('option', { name: '已确认', exact: true }).click();
      await expect(status).toContainText('已确认');

      // 最低评分与排序收进「更多筛选」；折叠时摘要仍写明范围、状态、最低评分和排序。
      const more = toolbar.getByTestId('breakout-more-filters');
      const summary = more.getByTestId('breakout-filter-summary');
      const scores = more.getByRole('group', { name: '评分筛选', exact: true });
      await expect(more).not.toHaveAttribute('open', '');
      await expect(scores).toBeHidden();
      await expect(summary).toContainText('范围 全部信号');
      await expect(summary).toContainText('状态 已确认');
      await expect(summary).toContainText('最低评分 不限评分');
      await expect(summary).toContainText('排序 跟随默认');
      await more.locator('summary').click();
      await expect(scores).toBeVisible();
      await expect(more.getByRole('tablist', { name: '雷达排序算法', exact: true })).toBeVisible();

      const unlimited = scores.getByRole('button', { name: '不限评分', exact: true });
      await expect(unlimited).toHaveAttribute('aria-pressed', 'true');
      const selectionColors = await unlimited.evaluate((button) => ({
        background: getComputedStyle(button).backgroundColor,
        foreground: getComputedStyle(button).color,
      }));
      // Cloud Monitor 式白色选中片，在浅灰轨道上仍有清晰层次。
      expect(selectionColors.background).toBe('rgb(255, 255, 255)');
      const trackBackground = await scores.evaluate((group) => getComputedStyle(group).backgroundColor);
      expect(trackBackground).not.toBe(selectionColors.background);
      await expect(unlimited).not.toHaveCSS('box-shadow', 'none');
      expect(selectionColors.foreground).not.toBe('rgb(255, 255, 255)');

      const eighty = scores.getByRole('button', { name: /80\s*分以上/ });
      await eighty.focus();
      await page.keyboard.press('Enter');
      await expect(eighty).toHaveAttribute('aria-pressed', 'true');
      await expect(status).toContainText('已确认');
      await expect(scores.locator('[aria-pressed="true"]')).toHaveCount(1);
      await expect(summary).toContainText('最低评分 80 分以上');
      await expect.poll(() => activeSignalCount(page)).toBeLessThanOrEqual(confirmedCount);

      // 两个维度都可以恢复，不能只更新控件外观而遗留隐藏过滤条件。
      await status.click();
      await page.getByRole('option', { name: '全部', exact: true }).click();
      await expect(status).toContainText('全部');
      await unlimited.click();
      await expect(summary).toContainText('状态 全部');
      await expect(summary).toContainText('最低评分 不限评分');
      await expect.poll(() => activeSignalCount(page)).toBe(baseline);
      const sixtyFive = scores.getByRole('button', { name: /65\s*分以上/ });
      await sixtyFive.click();
      await expect(scores.locator('[aria-pressed="true"]')).toHaveText(/65\s*分以上/);
      // aria-pressed 即时变化，CSS 颜色可能仍在本次过渡的首帧。
      // 用样式断言的自动重试等待最终状态，不以同步取样或固定休眠判断。
      await expect(sixtyFive).toHaveCSS('background-color', selectionColors.background);
      await expect(sixtyFive).toHaveCSS('color', selectionColors.foreground);

      const measure = (nodes) => nodes.evaluateAll((items) => items.map((item) => {
        const style = getComputedStyle(item);
        return {
          radius: Math.max(...[
            style.borderTopLeftRadius, style.borderTopRightRadius,
            style.borderBottomLeftRadius, style.borderBottomRightRadius,
          ].map(Number.parseFloat)),
          height: item.getBoundingClientRect().height,
        };
      }));
      // 状态下拉的触发器（combobox）与评分按钮（button）都要紧凑。
      const geometry = [
        ...await measure(toolbar.getByRole('combobox')),
        ...await measure(toolbar.getByRole('button')),
      ];
      expect(geometry.length).toBeGreaterThanOrEqual(4);
      expect(geometry.every((control) => control.radius <= (width === 390 ? 9 : 8))).toBe(true);
      expect(geometry.every((control) => control.height >= (width === 390 ? 44 : 28))).toBe(true);
      if (width === 390) {
        const raisedAndVisible = await scores.evaluate((rail) => {
          const selected = rail.querySelector('[aria-pressed="true"]').getBoundingClientRect();
          const track = rail.getBoundingClientRect();
          const viewport = rail.closest('.selection-viewport').getBoundingClientRect();
          return selected.top < track.top && selected.bottom > track.bottom
            && selected.top - viewport.top >= 4 && viewport.bottom - selected.bottom >= 4;
        });
        expect(raisedAndVisible).toBe(true);
      }
      await noPageOverflow(page);
      await capture(page, `breakouts-filters-${width}`, toolbar);
    });

    test('breakout view scope supports keyboard and touch while retaining watchlist filtering', async ({ page }) => {
      await page.goto('/breakouts');
      const scope = page.getByRole('tablist', { name: '查看范围', exact: true });
      const all = scope.getByRole('tab', { name: '全部信号', exact: true });
      const watchlist = scope.getByRole('tab', { name: '我的关注', exact: true });
      const history = page.getByRole('region', { name: '历史事件', exact: true });
      const filteredHistory = history.getByText(/· 筛选出\s*\d+\s*条/);
      await expect(all).toHaveAttribute('aria-selected', 'true');
      await expect(scope.getByRole('tab')).toHaveCount(2);
      await expect.poll(() => activeSignalCount(page)).toBeGreaterThan(0);
      await expect(history.getByRole('button', { name: /打开事件详情$/ }).first()).toBeVisible();
      const baseline = await activeSignalCount(page);
      await expect(filteredHistory).toHaveCount(0);

      await all.focus();
      await page.keyboard.press('ArrowRight');
      await expect(watchlist).toBeFocused();
      await expect(watchlist).toHaveAttribute('aria-selected', 'true');
      await expect(all).toHaveAttribute('tabindex', '-1');
      await expect(scope.locator('[role="tab"][tabindex="0"]')).toHaveCount(1);
      await expect(filteredHistory).toBeVisible();
      await expect.poll(() => activeSignalCount(page)).toBeLessThanOrEqual(baseline);

      await page.keyboard.press('Home');
      await expect(all).toBeFocused();
      await expect(all).toHaveAttribute('aria-selected', 'true');
      await expect(filteredHistory).toHaveCount(0);
      await expect.poll(() => activeSignalCount(page)).toBe(baseline);
      await page.keyboard.press('End');
      await expect(watchlist).toBeFocused();
      await expect(watchlist).toHaveAttribute('aria-selected', 'true');
      await page.keyboard.press('ArrowLeft');
      await expect(all).toBeFocused();
      await expect(all).toHaveAttribute('aria-selected', 'true');

      if (width === 390) await watchlist.tap();
      else await watchlist.click();
      await expect(watchlist).toHaveAttribute('aria-selected', 'true');
      await expect(filteredHistory).toBeVisible();
      const geometry = await scope.evaluate((list) => ({
        overflow: list.scrollWidth - list.clientWidth,
        buttons: [...list.querySelectorAll('[role="tab"]')].map((tab) => ({
          width: tab.getBoundingClientRect().width,
          height: tab.getBoundingClientRect().height,
        })),
      }));
      expect(geometry.overflow).toBeLessThanOrEqual(1);
      expect(geometry.buttons.every((tab) => tab.width >= 44 && tab.height >= (width === 390 ? 44 : 28))).toBe(true);
      await noPageOverflow(page);
      await capture(page, `breakouts-view-scope-${width}`, scope);
    });

    test('sector tabs support keyboard navigation and keep the indicator aligned after horizontal scrolling', async ({ page }) => {
      await page.goto('/sectors');
      const list = page.getByRole('tablist', { name: '行业切换', exact: true });
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
      const movers = page.getByRole('region', { name: '关注动态', exact: true });
      const cards = movers.getByTestId('watchlist-mover-card');
      const figures = movers.getByTestId('watchlist-daily-trend');
      await expect(figures.first()).toBeVisible();
      await expect.poll(() => cards.count()).toBeGreaterThan(0);
      await expect(figures).toHaveCount(await cards.count());
      await expect(cards.first()).toContainText('当日');
      await expect(cards.first()).toContainText(/近\s*30\s*个交易日/);
      await expect(figures.first()).toHaveAccessibleName(/每日走势，\d{4}-\d{2}-\d{2} 至 \d{4}-\d{2}-\d{2}，区间涨跌/);
      await expect(figures.first().locator('figcaption')).toContainText('区间涨跌');
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
      const indices = page.getByRole('region', { name: '市场指数', exact: true });
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
      await expect(page.getByText('未找到该股票', { exact: true })).toHaveCount(0);

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
