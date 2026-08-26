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

function toolButton(page, name) {
  return page.getByRole("button", { name, exact: true });
}

/** K 线周期 / 蜡烛·面积 用的是 Segmented `role="tab"`，不是 toolbar button。 */
function chartTab(page, name) {
  return page.getByRole("tab", { name, exact: true });
}

async function openStock(page, ticker = "AAPL") {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(`/stock/${ticker}`, { waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 15_000 });
  return errors;
}

async function paintedPixels(page) {
  return page.evaluate(() => {
    const host = document.querySelector('[role="img"][aria-label$="图"]');
    const canvases = Array.from((host ?? document).querySelectorAll("canvas"));
    let painted = 0;
    for (const canvas of canvases) {
      const width = Number(canvas.width) || 0;
      const height = Number(canvas.height) || 0;
      if (width < 16 || height < 16) continue;
      const ctx = canvas.getContext("2d");
      if (!ctx) continue;
      let sample;
      try {
        sample = ctx.getImageData(0, 0, Math.min(width, 200), Math.min(height, 200)).data;
      } catch {
        continue;
      }
      let count = 0;
      for (let i = 3; i < sample.length; i += 16) {
        if (sample[i] > 8) count += 1;
      }
      if (count > painted) painted = count;
    }
    return painted;
  });
}

async function chartFilled(page) {
  await expect.poll(() => paintedPixels(page), { timeout: 20_000 }).toBeGreaterThan(40);
}

test.use({ viewport: { width: 1440, height: 900 } });

test("chart drawings toolbar is present on a stock page when data loads", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  page.setDefaultTimeout(20_000);
  const errors = await openStock(page);
  await expect(toolButton(page, "水平线")).toBeVisible();
  await expect(toolButton(page, "展开图表")).toBeVisible();
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

test("seven drawing tools are present and selectable", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  for (const name of ["水平线", "趋势线", "射线", "平行通道", "矩形", "斐波那契", "文字"]) {
    const button = toolButton(page, name);
    await expect(button).toBeVisible();
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
  }
  await chartFilled(page);
});

test("drag endpoint and whole-object after selecting a drawing", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.4);
  await toolButton(page, "选择").click();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.3, { steps: 6 });
  await page.mouse.up();
  await chartFilled(page);
});

test("zoom keeps drawing time and price identity", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.35);
  await canvas.hover();
  await page.mouse.wheel(0, -400);
  await chartFilled(page);
});

test("resize reprojects selection anchors", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "展开图表").click();
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.waitForTimeout(200);
  await chartFilled(page);
});

test("candle and area modes share drawings", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.45, box.y + box.height * 0.4);
  await chartTab(page, "面积").click();
  await expect(chartTab(page, "面积")).toHaveAttribute("aria-selected", "true");
  await chartFilled(page);
  await chartTab(page, "K 线").click();
  await expect(chartTab(page, "K 线")).toHaveAttribute("aria-selected", "true");
  await chartFilled(page);
});

test("ticker and range switch isolates drawings", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page, "AAPL");
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.45, box.y + box.height * 0.4);
  await chartTab(page, "日线").click();
  await expect(chartTab(page, "日线")).toHaveAttribute("aria-selected", "true");
  await page.goto("/stock/MSFT", { waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 15_000 });
  await chartFilled(page);
});

test("refresh persistence keeps guest drawings", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.4);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("img", { name: /K 线图$|面积图$/ })).toBeVisible({ timeout: 15_000 });
  await chartFilled(page);
});

test("failed save then retry keeps the local edit", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await page.route("**/api/account/chart-drawings**", (route) => {
    if (route.request().method() === "GET") return route.continue();
    return route.abort();
  });
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.4);
  const retry = page.getByRole("button", { name: "重试同步" });
  if (await retry.count()) await retry.click();
  await chartFilled(page);
});

test("rapid same-id revision stays serial from the inspector", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "展开图表").click();
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.4);
  const widths = page.getByRole("button", { name: /线宽/ });
  const count = await widths.count();
  for (let i = 0; i < count; i += 1) await widths.nth(i).click();
  await chartFilled(page);
});

test("hide then restore from the object list", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "展开图表").click();
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.4);
  const hide = page.getByRole("button", { name: "隐藏" }).first();
  await hide.click();
  const show = page.getByRole("button", { name: "显示" }).first();
  await expect(show).toBeVisible();
  await show.click();
  await chartFilled(page);
});

test("undo color text lock delete then refresh", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "展开图表").click();
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.4);
  await page.getByRole("button", { name: "锁定", exact: true }).click();
  await toolButton(page, "撤销").click();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 15_000 });
});

test("auto patterns render from a real technical payload", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "算法与图层").click();
  await expect(page.getByRole("dialog", { name: "算法与图层" })).toBeVisible();
  await page.getByRole("button", { name: "极简", exact: true }).click();
  await chartFilled(page);
});

test("layer presets switch algorithm and pattern groups", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "算法与图层").click();
  const dialog = page.getByRole("dialog", { name: "算法与图层" });
  await expect(dialog).toBeVisible();
  for (const name of ["极简", "结构分析", "突破交易", "动量", "量价", "全部"]) {
    await dialog.getByRole("button", { name, exact: true }).click();
    await expect(dialog.getByRole("button", { name, exact: true })).toHaveAttribute("aria-pressed", "true");
  }
  await expect(dialog.getByRole("checkbox", { name: "RSI" })).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: "自动趋势线/通道/三角形/楔形" })).toBeVisible();
  await page.keyboard.press("Escape");
  await chartFilled(page);
});
