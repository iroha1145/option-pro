import { expect, test } from "@playwright/test";

const VIEWPORTS = [
  { width: 320, height: 568 },
  { width: 390, height: 844 },
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
