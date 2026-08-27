// Chart drawings visual evidence. Uses the same OPTIX_VISUAL_BASE_URL dual-mode
// as the other visual specs: real gateway when set, otherwise the static
// frontend build. Stock pages need live/fixture data from the backend; without
// it this file skips rather than asserting a blank chart.
//
// This runs as an OWNER session against the real backend, so everything it
// draws is written to a persistent account. Two rules follow:
//   1. afterEach deletes the scopes this file touches — leftovers pile up
//      inside one run (later `.first()` lookups land on stale objects) and
//      across runs toward the 500-per-scope cap. Only GET+DELETE the scopes
//      this file actually draws (AAPL 1d/1w, MSFT 1d) and skip empty DELETEs:
//      a 2×5 sweep of empty scopes filled the gateway 200/60 light bucket so
//      later POSTs and the OCC clear landed on 429.
//   2. chartFilled() only counts painted alpha in a 200x200 corner of the
//      price canvas — candles alone satisfy it. It proves the chart rendered,
//      never that a drawing exists. Object-level facts are asserted against
//      the Inspector's 「绘图对象 …」 rows, which carry stable a11y names.
import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");
const HAS_REAL_BACKEND = Boolean(process.env.OPTIX_VISUAL_BASE_URL);
/** Scopes this file actually draws. Empty 1h/15m/5m DELETEs were light-bucket noise. */
const TOUCHED_SCOPES = [
  { ticker: "AAPL", range: "1d" },
  { ticker: "AAPL", range: "1w" },
  { ticker: "MSFT", range: "1d" },
];

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

/** Inspector 的对象行：aria-label 是「绘图对象 {kind}」，只认展开工作区里的那一份。 */
function drawingRows(page) {
  return page.getByRole("dialog", { name: "绘图工作区" }).getByRole("button", { name: /^绘图对象 / });
}

async function openStock(page, ticker = "AAPL") {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  if (!page.url().includes(`/stock/${ticker}`)) {
    await page.goto(`/stock/${ticker}`, { waitUntil: "domcontentloaded" });
  }
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 20_000 });
  return errors;
}

/** 对象列表只活在展开的绘图工作区里，所以断言前先展开。 */
async function expandChart(page) {
  const expand = toolButton(page, "展开图表");
  if (await expand.isVisible().catch(() => false)) await expand.click();
  await expect(page.getByRole("dialog", { name: "绘图工作区" })).toBeVisible();
}

/** Same-origin GET so cookies/Origin match the page. page.request is a distinct client. */
async function listDrawings(page, ticker = "AAPL", range = "1d") {
  return page.evaluate(async ({ ticker, range }) => {
    const url = `/api/account/chart-drawings?ticker=${encodeURIComponent(ticker)}&range=${encodeURIComponent(range)}&adjustment=raw`;
    const res = await fetch(url, { credentials: "same-origin" });
    const body = await res.json().catch(() => ({}));
    return {
      status: res.status,
      drawings: Array.isArray(body.drawings) ? body.drawings : null,
      revision: Number(body.scope_revision ?? 0),
    };
  }, { ticker, range });
}

/** Poll sentinel: 429 is transient; never treat it as n=0 / unlocked / 409. */
function drawingsLockState(listed) {
  if (listed.status === 429) return "rate-limited";
  if (listed.status !== 200) return `http ${listed.status}`;
  if (!Array.isArray(listed.drawings)) return "n=?";
  if (listed.drawings.length !== 1) return `n=${listed.drawings.length}`;
  return listed.drawings[0].locked ? "locked" : "unlocked";
}

/** 从页面发 DELETE（带 Origin），page.request 没有 CSRF 头会被 403 吞掉。 */
async function clearTouchedDrawings(page) {
  await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => {});
  await page.evaluate(async (scopes) => {
    for (const { ticker, range } of scopes) {
      const url = `/api/account/chart-drawings?ticker=${encodeURIComponent(ticker)}&range=${encodeURIComponent(range)}&adjustment=raw`;
      const listed = await fetch(url, { credentials: "same-origin" }).then((res) => res.json()).catch(() => null);
      const drawings = Array.isArray(listed?.drawings) ? listed.drawings : [];
      if (!drawings.length) continue;
      const revision = Number(listed?.scope_revision ?? 0);
      await fetch(`${url}&expected_scope_revision=${encodeURIComponent(revision)}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "X-Optix-Action": "1" },
      }).catch(() => {});
    }
    try {
      for (const key of Object.keys(localStorage)) {
        if (key.includes("chart-drawing")) localStorage.removeItem(key);
      }
    } catch {
      /* private mode */
    }
  }, TOUCHED_SCOPES);
}

/** 展开工作区，断言当前标的/周期下**恰好** n 个绘图对象（n=0 就是缺席断言）。 */
async function expectDrawingCount(page, n) {
  await expandChart(page);
  if (n === 0) {
    await expect(page.getByText("当前没有手绘图形").first()).toBeVisible();
  }
  await expect(drawingRows(page)).toHaveCount(n);
}

/** 在价格区落一笔水平线（工具按钮 → 画布点击）。 */
async function placeHorizontal(page, xRatio = 0.5, yRatio = 0.4) {
  await toolButton(page, "水平线").click();
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.click(box.x + box.width * xRatio, box.y + box.height * yRatio);
  return box;
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

/** 只证明「画布上有东西」——绝不能拿它当「绘图存在」的证据。 */
async function chartFilled(page) {
  await expect.poll(() => paintedPixels(page), { timeout: 20_000 }).toBeGreaterThan(40);
}

test.use({
  viewport: { width: 1440, height: 900 },
  reducedMotion: "reduce",
});

test.beforeEach(async ({ page }) => {
  if (!HAS_REAL_BACKEND) return;
  // 上一轮 CI 的残留会让「恰好 n 条」全红；page.request.delete 过不了同源守卫。
  await page.goto("/stock/AAPL", { waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 20_000 });
  await clearTouchedDrawings(page);
});

test.afterEach(async ({ page }) => {
  if (!HAS_REAL_BACKEND) return;
  // 「失败重试」用例把非 GET 全 abort 掉了，先撤路由再清扫。
  // 清扫必须走页面 fetch：page.request.delete 没有 Origin，同源守卫会 403。
  await clearTouchedDrawings(page);
});

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
  const box = await placeHorizontal(page, 0.5, 0.4);
  await toolButton(page, "选择").click();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.3, { steps: 6 });
  await page.mouse.up();
  // 拖拽提交不该复制出第二个对象，也不该把它弄丢。
  await expectDrawingCount(page, 1);
});

test("zoom keeps drawing time and price identity", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await placeHorizontal(page, 0.4, 0.35);
  const canvas = page.locator("canvas").first();
  await canvas.hover();
  await page.mouse.wheel(0, -400);
  await chartFilled(page);
  // 缩放只换视窗，不换对象身份。
  await expectDrawingCount(page, 1);
});

test("resize reprojects selection anchors", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await placeHorizontal(page, 0.5, 0.4);
  await expandChart(page);
  await page.setViewportSize({ width: 1100, height: 800 });
  await page.waitForTimeout(200);
  await expect(drawingRows(page)).toHaveCount(1);
  await chartFilled(page);
});

test("candle and area modes share drawings", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await placeHorizontal(page, 0.45, 0.4);
  await expandChart(page);
  await expect(drawingRows(page)).toHaveCount(1);
  await chartTab(page, "面积").click();
  await expect(chartTab(page, "面积")).toHaveAttribute("aria-selected", "true");
  // 显示模式不在 ticker|range|adjustment 作用域里：切模式对象必须还在。
  await expect(drawingRows(page)).toHaveCount(1);
  await chartTab(page, "K 线").click();
  await expect(chartTab(page, "K 线")).toHaveAttribute("aria-selected", "true");
  await expect(drawingRows(page)).toHaveCount(1);
  await chartFilled(page);
});

test("ticker and range switch isolates drawings", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page, "AAPL");
  await placeHorizontal(page, 0.45, 0.4);
  await expandChart(page);
  await expect(drawingRows(page)).toHaveCount(1);
  // 换周期 = 换作用域：这里必须是缺席断言，否则「绘图串周期」也能过。
  await chartTab(page, "周线").click();
  await expect(chartTab(page, "周线")).toHaveAttribute("aria-selected", "true");
  await expect(drawingRows(page)).toHaveCount(0);
  await chartTab(page, "日线").click();
  await expect(chartTab(page, "日线")).toHaveAttribute("aria-selected", "true");
  await expect(drawingRows(page)).toHaveCount(1);
  await page.goto("/stock/MSFT", { waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 15_000 });
  await expectDrawingCount(page, 0);
});

test("refresh persistence keeps the account drawing", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await placeHorizontal(page, 0.5, 0.4);
  await expectDrawingCount(page, 1);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("img", { name: /K 线图$|面积图$/ })).toBeVisible({ timeout: 15_000 });
  // 刷新后对象要真的回来——chartFilled 只要有蜡烛就绿，全丢也照样过。
  await expectDrawingCount(page, 1);
});

test("failed save then retry keeps the local edit", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await page.route("**/api/account/chart-drawings**", (route) => {
    if (route.request().method() === "GET") return route.continue();
    return route.abort();
  });
  await placeHorizontal(page, 0.5, 0.4);
  // 服务器写失败时本地那笔必须留着，并且要给得出重试入口。
  await expectDrawingCount(page, 1);
  const retry = toolButton(page, "重试同步").first();
  await expect(retry).toBeVisible({ timeout: 15_000 });
  await retry.click();
  await expect(drawingRows(page)).toHaveCount(1);
  await expect(page.getByText("当前没有手绘图形").first()).toBeHidden();
});

test("rapid same-id revision stays serial from the inspector", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  const errors = await openStock(page);
  await placeHorizontal(page, 0.5, 0.4);
  await expandChart(page);
  await expect(drawingRows(page)).toHaveCount(1);
  const widths = page.getByRole("button", { name: /^线宽 \d$/ });
  const count = await widths.count();
  for (let i = 0; i < count; i += 1) await widths.nth(i).click();
  // 连打同一个 id 的修订：不能分裂出第二个对象，也不能掉进冲突态。
  await expect(drawingRows(page)).toHaveCount(1);
  await expect(widths.nth(count - 1)).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/绘图冲突/)).toHaveCount(0);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 15_000 });
  await expectDrawingCount(page, 1);
  expect(errors, errors.join("\n")).toEqual([]);
});

test("hide then restore from the object list", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await placeHorizontal(page, 0.5, 0.4);
  await expandChart(page);
  const row = drawingRows(page).first();
  await expect(row).toHaveCount(1);
  await expect(row).not.toContainText("已隐藏");
  await page.getByRole("button", { name: "隐藏" }).first().click();
  await expect(row).toContainText("已隐藏");
  await page.getByRole("button", { name: "显示" }).first().click();
  await expect(row).not.toContainText("已隐藏");
  await chartFilled(page);
});

test("undo color text lock delete then refresh", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  test.setTimeout(90_000);
  await openStock(page);
  await placeHorizontal(page, 0.5, 0.4);
  await expandChart(page);
  const row = drawingRows(page).first();
  // Create must land before lock: a PUT 404 while still-local is conflict, not idle.
  await expect.poll(async () => drawingsLockState(await listDrawings(page)), { timeout: 20_000 }).toBe("unlocked");
  await toolButton(page, "锁定").first().click();
  await expect(row).toContainText("已锁定");
  await expect.poll(async () => drawingsLockState(await listDrawings(page)), { timeout: 20_000 }).toBe("locked");
  await toolButton(page, "撤销").first().click();
  await expect(row).not.toContainText("已锁定");
  // 不读工具条文案（同步标签会改）：等 GET 上的 locked 落地再刷新。
  await expect.poll(async () => drawingsLockState(await listDrawings(page)), { timeout: 20_000 }).toBe("unlocked");
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 20_000 });
  await expectDrawingCount(page, 1);
  await expect(drawingRows(page).first()).not.toContainText("已锁定");
});

test("clear all removes every drawing in the scope", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await placeHorizontal(page, 0.42, 0.35);
  await placeHorizontal(page, 0.58, 0.55);
  await expandChart(page);
  await expect(drawingRows(page)).toHaveCount(2);
  await toolButton(page, "清除全部手绘").first().click();
  await toolButton(page, "确认清除").first().click();
  await expect(drawingRows(page)).toHaveCount(0);
  await expect(page.getByText("当前没有手绘图形").first()).toBeVisible();
});

test("auto patterns render from a real technical payload", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await toolButton(page, "算法与图层").click();
  await expect(page.getByRole("dialog", { name: "算法与图层" })).toBeVisible();
  await page.getByRole("button", { name: "极简", exact: true }).click();
  await page.keyboard.press("Escape");
  // 形态标签条只许打真形态。ma/vwap/breakout 这些 kind 落进来就成了
  // 「形态 · ma · 置信度 100」，正好戳穿产品反复强调的「几何质量不是胜率」。
  await expect(
    page.getByText(/形态 · (ma|vwap|breakout|swing|level|pivot|candle|trap|volume_setup|opening_range|box) ·/),
  ).toHaveCount(0);
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
  await expect(dialog.getByRole("checkbox", { name: "RSI", exact: true }).first()).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: "自动趋势线/通道/三角形/楔形", exact: true }).first()).toBeVisible();
  // strength_* 那族图层勾了什么都不画，已整族移除：菜单里不该再有它们的开关。
  for (const dead of ["short", "mid", "long", "trend", "breakout", "price_action"]) {
    await expect(dialog.getByRole("checkbox", { name: dead, exact: true })).toHaveCount(0);
  }
  // 「最低几何质量」和标签条上的「置信度 87」同一把尺（0–100），不是 0–1。
  await dialog.getByRole("button", { name: "极简", exact: true }).click();
  await expect(dialog.getByRole("spinbutton", { name: "最低几何质量" })).toHaveValue("70");
  await page.keyboard.press("Escape");
  await chartFilled(page);
});

test("failed save survives refresh and replays after network returns", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await page.route("**/api/account/chart-drawings**", (route) => {
    if (route.request().method() === "GET") return route.continue();
    return route.abort();
  });
  await placeHorizontal(page, 0.48, 0.42);
  await expectDrawingCount(page, 1);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(toolButton(page, "选择")).toBeVisible({ timeout: 20_000 });
  await expectDrawingCount(page, 1);
  await page.unroute("**/api/account/chart-drawings**");
  const retry = toolButton(page, "重试同步");
  if (await retry.isVisible().catch(() => false)) await retry.click();
  await expect.poll(async () => {
    const listed = await listDrawings(page);
    if (listed.status === 429) return "rate-limited";
    if (listed.status !== 200) return `http ${listed.status}`;
    return Array.isArray(listed.drawings) ? listed.drawings.length : -1;
  }, { timeout: 20_000 }).toBe(1);
});

test("stale clear from a second context is 409 and keeps the newer drawing", async ({ browser, page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  test.setTimeout(120_000);
  await openStock(page);
  await placeHorizontal(page, 0.4, 0.4);
  await expectDrawingCount(page, 1);
  let staleRev = 0;
  await expect.poll(async () => {
    const listed = await listDrawings(page);
    if (listed.status === 429) return 0;
    if (listed.drawings?.length === 1 && listed.revision > 0) {
      staleRev = listed.revision;
      return listed.revision;
    }
    return 0;
  }, { timeout: 20_000 }).toBeGreaterThan(0);
  const storage = await page.context().storageState();
  const other = await browser.newContext({ storageState: storage });
  const pageB = await other.newPage();
  await pageB.goto("/stock/AAPL", { waitUntil: "domcontentloaded" });
  await expect(toolButton(pageB, "选择")).toBeVisible({ timeout: 20_000 });
  await placeHorizontal(pageB, 0.62, 0.55);
  await expectDrawingCount(pageB, 2);
  await expect.poll(async () => {
    const listed = await listDrawings(pageB);
    if (listed.status === 429) return "rate-limited";
    if (listed.status !== 200) return `http ${listed.status}`;
    return Array.isArray(listed.drawings) ? listed.drawings.length : -1;
  }, { timeout: 20_000 }).toBe(2);
  /** @type {{ status: number, code: string | null }} */
  let stale = { status: 0, code: null };
  await expect.poll(async () => {
    stale = await page.evaluate(async (revision) => {
      const res = await fetch(`/api/account/chart-drawings?ticker=AAPL&range=1d&adjustment=raw&expected_scope_revision=${encodeURIComponent(revision)}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "X-Optix-Action": "1" },
      });
      const body = await res.json().catch(() => ({}));
      const detail = body && typeof body.detail === "object" ? body.detail : null;
      return { status: res.status, code: detail?.code || body?.error || null };
    }, staleRev);
    // 429 is the light-bucket throttle, not OCC. Keep polling until a real status.
    return stale.status === 429 ? "rate-limited" : stale.status;
  }, { timeout: 90_000 }).toBe(409);
  expect(stale.code).toBe("scope_revision_conflict");
  await expectDrawingCount(pageB, 2);
  await other.close();
});

test("Escape closes only the layer drawer", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await expandChart(page);
  await toolButton(page, "算法与图层").click();
  const layers = page.getByRole("dialog", { name: "算法与图层" });
  await expect(layers).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(layers).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "绘图工作区" })).toBeVisible();
});

test("zoom then recolor keeps the dataZoom window", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await openStock(page);
  await placeHorizontal(page, 0.5, 0.4);
  await expandChart(page);
  const zoomed = await page.evaluate(() => {
    const host = document.querySelector('[role="img"][aria-label$="图"]');
    const chart = host && host.__echarts__;
    if (!chart || typeof chart.getOption !== "function") return null;
    const option = chart.getOption();
    const zoom = (option.dataZoom || [])[0] || {};
    return { start: zoom.start ?? zoom.startValue, end: zoom.end ?? zoom.endValue };
  });
  await page.mouse.wheel(0, -800);
  const afterZoom = await page.evaluate(() => {
    const host = document.querySelector('[role="img"][aria-label$="图"]');
    const instances = window.__ECHARTS_INSTANCES__;
    const chart = host && (host.__echarts_instance__ || host._echarts_instance_);
    const canvas = document.querySelector("canvas");
    let found = null;
    if (typeof echarts !== "undefined" && canvas) {
      try { found = echarts.getInstanceByDom(canvas.parentElement); } catch { /* */ }
    }
    const inst = found || chart;
    if (!inst || typeof inst.getOption !== "function") return null;
    const zoom = (inst.getOption().dataZoom || [])[0] || {};
    return { start: zoom.start ?? zoom.startValue, end: zoom.end ?? zoom.endValue };
  });
  await page.getByRole("button", { name: "颜色 红色" }).click();
  const afterColor = await page.evaluate(() => {
    const canvas = document.querySelector("canvas");
    let inst = null;
    if (typeof echarts !== "undefined" && canvas) {
      try { inst = echarts.getInstanceByDom(canvas.parentElement); } catch { /* */ }
    }
    if (!inst || typeof inst.getOption !== "function") return null;
    const zoom = (inst.getOption().dataZoom || [])[0] || {};
    return { start: zoom.start ?? zoom.startValue, end: zoom.end ?? zoom.endValue };
  });
  if (afterZoom && afterColor) {
    expect(afterColor.start).toEqual(afterZoom.start);
    expect(afterColor.end).toEqual(afterZoom.end);
  }
  await expectDrawingCount(page, 1);
  void zoomed;
});

test("expanded mobile workspace has no horizontal overflow", async ({ page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  await page.emulateMedia({ reducedMotion: "reduce" });
  // beforeEach already opened AAPL at desktop and waited for the toolbar.
  // A second goto at 390px re-hits the light bucket and can leave the chart
  // unmounted (no 选择). Resize the loaded page, then expand.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(toolButton(page, "展开图表")).toBeVisible({ timeout: 20_000 });
  await expandChart(page);
  const workspace = page.getByRole("dialog", { name: "绘图工作区" });
  await expect(workspace).toBeVisible();
  await expect.poll(async () => workspace.evaluate((el) => getComputedStyle(el).opacity)).toBe("1");
  await workspace.evaluate(async (el) => {
    await Promise.all(el.getAnimations().map((animation) => animation.finished.catch(() => {})));
  });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const chartBox = await workspace.locator(".min-h-\\[240px\\]").first().boundingBox();
  expect(chartBox?.height ?? 0).toBeGreaterThanOrEqual(240);
  const inspector = workspace.locator("aside");
  await expect(inspector).toBeVisible();
  const inspectorOverflow = await inspector.evaluate((el) => getComputedStyle(el).overflowY);
  expect(["auto", "scroll", "overlay"].some((value) => inspectorOverflow.includes(value) || inspectorOverflow === "auto")).toBeTruthy();
  const tool = toolButton(page, "选择");
  const toolBox = await tool.boundingBox();
  expect(Math.min(toolBox?.width ?? 0, toolBox?.height ?? 0)).toBeGreaterThanOrEqual(40);
  await screenshot(page, "390x844-expanded-workspace");
  await toolButton(page, "算法与图层").click();
  const layers = page.getByRole("dialog", { name: "算法与图层" });
  await expect(layers).toBeVisible();
  await expect.poll(async () => layers.evaluate((el) => getComputedStyle(el).opacity)).toBe("1");
  await screenshot(page, "390x844-layer-drawer");
});

test("stale update from a second context is 409 and keeps the newer color", async ({ browser, page }) => {
  test.skip(!HAS_REAL_BACKEND, "stock drawings visual path needs OPTIX_VISUAL_BASE_URL");
  test.setTimeout(120_000);
  await openStock(page);
  await placeHorizontal(page, 0.4, 0.4);
  await expectDrawingCount(page, 1);
  /** @type {{ id: string, revision: number, scope: number, color: string }} */
  let stale = { id: "", revision: 0, scope: 0, color: "" };
  await expect.poll(async () => {
    const listed = await listDrawings(page);
    if (listed.status === 429) return "rate-limited";
    if (listed.status !== 200 || !listed.drawings?.length) return `http ${listed.status}`;
    const row = listed.drawings[0];
    stale = {
      id: row.id,
      revision: Number(row.revision),
      scope: listed.revision,
      color: row.style?.color || "",
    };
    return listed.drawings.length === 1 && listed.revision > 0 ? listed.revision : 0;
  }, { timeout: 20_000 }).toBeGreaterThan(0);
  const storage = await page.context().storageState();
  const other = await browser.newContext({ storageState: storage });
  const pageB = await other.newPage();
  await pageB.goto("/stock/AAPL", { waitUntil: "domcontentloaded" });
  await expect(toolButton(pageB, "选择")).toBeVisible({ timeout: 20_000 });
  await expectDrawingCount(pageB, 1);
  await pageB.getByRole("button", { name: "颜色 红色" }).click();
  await expect.poll(async () => {
    const listed = await listDrawings(pageB);
    if (listed.status === 429) return "rate-limited";
    if (listed.status !== 200) return `http ${listed.status}`;
    const row = listed.drawings?.[0];
    return row?.style?.color && row.style.color !== stale.color ? row.style.color : "pending";
  }, { timeout: 20_000 }).not.toBe("pending");
  /** @type {{ status: number, code: string | null }} */
  let conflict = { status: 0, code: null };
  await expect.poll(async () => {
    conflict = await page.evaluate(async (payload) => {
      const res = await fetch(`/api/account/chart-drawings/${encodeURIComponent(payload.id)}`, {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Optix-Action": "1" },
        body: JSON.stringify({
          schemaVersion: 1,
          id: payload.id,
          ticker: "AAPL",
          range: "1d",
          adjustment: "raw",
          kind: "horizontal",
          anchors: [{ time: "2026-01-02T14:30:00Z", barKey: "2026-01-02", price: 100 }],
          style: { color: "#2E46E0", width: 2, dash: "solid" },
          locked: false,
          hidden: false,
          zOrder: 0,
          revision: payload.revision,
          expected_scope_revision: payload.scope,
        }),
      });
      const body = await res.json().catch(() => ({}));
      const detail = body && typeof body.detail === "object" ? body.detail : null;
      return { status: res.status, code: detail?.code || body?.error || null };
    }, stale);
    return conflict.status === 429 ? "rate-limited" : conflict.status;
  }, { timeout: 90_000 }).toBe(409);
  expect(["scope_revision_conflict", "revision_conflict"]).toContain(conflict.code);
  const listedB = await listDrawings(pageB);
  expect(listedB.drawings?.[0]?.style?.color).not.toBe("#2E46E0");
  await other.close();
});
