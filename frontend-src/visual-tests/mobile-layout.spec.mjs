import { expect, test } from "@playwright/test";

const VIEWPORTS = [
  { width: 320, height: 568 },
  { width: 390, height: 844 },
];

const TABLET_VIEWPORTS = [
  { width: 768, height: 1024 },
  { width: 1024, height: 900 },
  { width: 1120, height: 900 },
];

const EARNINGS_DESKTOP_VIEWPORTS = [
  { width: 1536, height: 960 },
  { width: 1600, height: 1000 },
];

async function expectNoDocumentOverflow(page) {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
}

async function openEarnings(page, width) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  if (width < 1280) {
    await page.getByRole("button", { name: "更多" }).click();
    await page.getByRole("dialog", { name: "更多功能" }).getByRole("button", { name: /财报日历/ }).click();
  } else {
    await page.getByRole("link", { name: /财报/ }).click();
  }
  await expect(page).toHaveURL(/\/earnings$/);
}

for (const viewport of VIEWPORTS) {
  test.describe(`mobile layout ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test("dock, screener and catalyst tabs stay inside the viewport", async ({ page }) => {
      await page.goto("/", { waitUntil: "domcontentloaded" });
      const dock = page.getByRole("navigation", { name: "移动端导航" });
      await expect(dock).toBeVisible();
      await expect
        .poll(() => dock.evaluate((node) => node.getBoundingClientRect().height))
        .toBeGreaterThanOrEqual(64);
      await expectNoDocumentOverflow(page);

      await page.getByRole("link", { name: "选股" }).click();
      await expect(page).toHaveURL(/\/screener$/);
      const workbench = page.locator('[aria-label="筛选工作台"]');
      await expect(workbench).toBeVisible();
      await expect
        .poll(() => workbench.evaluate((node) => getComputedStyle(node).position))
        .not.toBe("sticky");
      await expectNoDocumentOverflow(page);

      await page.getByRole("button", { name: "更多" }).click();
      const more = page.getByRole("dialog", { name: "更多功能" });
      await expect(more).toBeVisible();
      await more.getByRole("button", { name: /新闻催化/ }).click();
      await expect(page).toHaveURL(/\/catalysts$/);
      const tabs = page.getByRole("tablist", { name: "催化剂视图" });
      await expect(tabs).toBeVisible();
      const tabViewport = page.locator(".selection-viewport").filter({ has: tabs });
      await expect
        .poll(() => tabViewport.evaluate((node) => getComputedStyle(node).overflowX))
        .toMatch(/auto|scroll/);

      // The outer viewport scrolls while the raised tab itself remains unclipped.
      await tabViewport.scrollIntoViewIfNeeded();
      const scrollRange = await tabViewport.evaluate((node) => node.scrollWidth - node.clientWidth);
      await tabViewport.hover();
      await page.mouse.wheel(scrollRange + viewport.width, 0);
      if (scrollRange > 1) {
        await expect.poll(() => tabViewport.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
      }
      const tabButtons = tabs.getByRole("tab");
      const firstTab = tabButtons.first();
      const lastTab = tabButtons.last();
      const expectLastTabWithinViewport = async () => {
        await expect.poll(async () => {
          const [outer, button] = await Promise.all([tabViewport.boundingBox(), lastTab.boundingBox()]);
          return outer !== null && button !== null
            && button.x >= outer.x - 1
            && button.x + button.width <= outer.x + outer.width + 1
            && button.y >= outer.y - 1
            && button.y + button.height <= outer.y + outer.height + 1;
        }).toBe(true);
      };
      await expectLastTabWithinViewport();
      await lastTab.click();
      await expect(lastTab).toHaveAttribute("aria-selected", "true");
      await expectNoDocumentOverflow(page);

      // Every tab remains reachable with the keyboard, including beyond the rail's edge.
      await firstTab.click();
      await expect(firstTab).toBeFocused();
      await expect(lastTab).toHaveAttribute("aria-selected", "false");
      for (let index = 1; index < await tabButtons.count(); index += 1) {
        await page.keyboard.press("Tab");
        await expect(tabButtons.nth(index)).toBeFocused();
      }
      await expectLastTabWithinViewport();
      // The feed count arrives asynchronously and must not squeeze the focused tab.
      await expect(tabViewport.locator("..").getByText(/^\d+\s*条$/)).toBeVisible();
      let previousWidth = -1;
      let stableWidths = 0;
      await expect.poll(async () => {
        const width = await tabViewport.evaluate((node) => node.getBoundingClientRect().width);
        stableWidths = Math.abs(width - previousWidth) < 0.5 ? stableWidths + 1 : 0;
        previousWidth = width;
        return stableWidths;
      }).toBeGreaterThanOrEqual(2);
      await expect(lastTab).toBeFocused();
      await expectLastTabWithinViewport();
      await page.keyboard.press("Enter");
      await expect(lastTab).toHaveAttribute("aria-selected", "true");
      await expectLastTabWithinViewport();
      await expectNoDocumentOverflow(page);
    });
  });
}

for (const viewport of TABLET_VIEWPORTS) {
  test.describe(`earnings responsive layout ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test("keeps the earnings list and analysis in one unclipped column before xl", async ({ page }) => {
      await openEarnings(page, viewport.width);

      const subject = page.locator('[aria-label="财报主体"]');
      await expect(subject).toBeVisible();
      await expect
        .poll(() =>
          subject.evaluate((node) =>
            getComputedStyle(node).gridTemplateColumns.split(/\s+/).filter(Boolean).length,
          ),
        )
        .toBe(1);
      await expect
        .poll(() =>
          subject.evaluate((node) => {
            const outer = node.getBoundingClientRect();
            return Array.from(node.children).every((child) => {
              const rect = child.getBoundingClientRect();
              return rect.left >= outer.left - 1 && rect.right <= outer.right + 1;
            });
          }),
        )
        .toBe(true);
      await expectNoDocumentOverflow(page);
    });
  });
}

for (const viewport of EARNINGS_DESKTOP_VIEWPORTS) {
  test.describe(`earnings desktop layout ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test("keeps every earnings column and the analysis rail fully visible", async ({ page, request }) => {
      test.setTimeout(60_000);
      // 财报列表需要真实 /api/earnings/upcoming（CI 的 :2000 后端提供）。
      // 本地默认的 python http.server 静态取证服务器没有 API，会 404 →
      // 页面如实渲染错误态，这不是布局回归——显式跳过而不是假红。
      const probe = await request
        .get("/api/earnings/upcoming", { timeout: 45_000 })
        .catch(() => null);
      test.skip(
        !probe || !probe.ok(),
        "需要真实后端提供 /api/earnings/upcoming（静态取证服务器无 API）",
      );
      await openEarnings(page, viewport.width);

      const subject = page.locator('[aria-label="财报主体"]');
      const list = page.locator('[aria-label="即将公布"]');
      const analysis = page.locator('[aria-label="AI 影响分析"]');
      await expect(subject).toBeVisible();
      // The first owner read may wait for the unified worker's cold earnings
      // snapshot. Keep the visual assertion on the real list instead of
      // mistaking its loading skeleton for a layout failure.
      await expect(list).toBeVisible({ timeout: 30_000 });
      await expect(analysis).toBeVisible({ timeout: 30_000 });

      const header = list.locator(":scope > div").first();
      for (const column of [
        "代码",
        "时间",
        "EPS 预期 vs 实际",
        "营收预期",
        "市值",
        "AI 影响",
      ]) {
        await expect(header.getByText(column, { exact: true })).toBeVisible();
      }
      const rowAction = list.getByRole("button", { name: / AI 影响分析$/ }).first();
      await expect(rowAction).toBeVisible();
      // 「预期波动」列跟随数据出现（EarningsList：至少一行有真实数值才渲染整列）。
      // 直板口径只认 bid/ask 中价：周末/盘后全部报价失效时列会整体消失，这是
      // 产品行为不是布局回归。两个方向互证：列头与 ±x.x% 数值单元格同生同灭。
      const moveHeader = header.getByText("预期波动", { exact: true });
      if ((await list.getByText(/^±\d+(\.\d+)?%$/).count()) > 0) {
        await expect(moveHeader).toBeVisible();
      } else {
        await expect(moveHeader).toHaveCount(0);
      }

      await expect
        .poll(() =>
          list.evaluate((node) => node.scrollWidth - node.clientWidth),
        )
        .toBeLessThanOrEqual(1);
      await expect
        .poll(() =>
          subject.evaluate((node) => {
            const outer = node.getBoundingClientRect();
            return Array.from(node.children).every((child) => {
              const rect = child.getBoundingClientRect();
              return rect.left >= outer.left - 1 && rect.right <= outer.right + 1;
            });
          }),
        )
        .toBe(true);
      await expectNoDocumentOverflow(page);
    });
  });
}
