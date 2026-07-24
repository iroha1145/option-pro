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
    await page.getByRole("link", { name: "05 财报" }).click();
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
      await expect
        .poll(() => tabs.evaluate((node) => getComputedStyle(node).overflowX))
        .toMatch(/auto|scroll/);
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

    test("keeps every earnings column and the analysis rail fully visible", async ({ page }) => {
      await openEarnings(page, viewport.width);

      const subject = page.locator('[aria-label="财报主体"]');
      const list = page.locator('[aria-label="即将公布"]');
      const analysis = page.locator('[aria-label="AI 影响分析"]');
      await expect(subject).toBeVisible();
      await expect(list).toBeVisible();
      await expect(analysis).toBeVisible();

      const header = list.locator(":scope > div").first();
      for (const column of [
        "代码",
        "时间",
        "EPS 预期 vs 实际",
        "营收预期",
        "市值",
        "预期波动",
        "AI 影响",
      ]) {
        await expect(header.getByText(column, { exact: true })).toBeVisible();
      }
      const rowAction = list.getByRole("button", { name: / AI 影响分析$/ }).first();
      await expect(rowAction).toBeVisible();

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
