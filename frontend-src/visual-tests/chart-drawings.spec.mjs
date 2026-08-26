// Chart drawings visual evidence. Uses the same OPTIX_VISUAL_BASE_URL dual-mode
// as the other visual specs: real gateway when set, otherwise the static
// frontend build. Stock pages need live/fixture data from the backend; without
// it this file skips rather than asserting a blank chart.
import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");
const HAS_REAL_BACKEND = Boolean(process.env.OPTIX_VISUAL_BASE_URL);

async function screenshot(page, name) {
  await mkdir(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: join(SCREENSHOT_DIR, `${name}.png`), fullPage: false });
}

test.use({ viewport: { width: 1440, height: 900 } });

test("chart drawings toolbar is present on a stock page when data loads", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  page.setDefaultTimeout(20_000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto("/stock/AAPL", { waitUntil: "domcontentloaded" });
  const draw = page.getByRole("button", { name: "选择" });
  await expect(draw).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "水平线" })).toBeVisible();
  await expect(page.getByRole("button", { name: "展开图表" })).toBeVisible();
  await screenshot(page, "1440x900-chart-drawings");
  expect(errors, errors.join("\n")).toEqual([]);
});

test("chart drawings mobile viewport shows the draw entry", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/stock/AAPL", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("绘图").first()).toBeVisible({ timeout: 15_000 });
  await screenshot(page, "390x844-chart-drawings");
});
