// PR #113 审查阻断项的行为回归（真实 DOM 交互，不是源码字符串断言）：
//   1. 命令面板滑行高亮首开/重开立即可见，首绘不从旧位置补间；
//   2. 清除钮 Enter/Space 只清空并回焦输入框，不触发高亮结果；
//   3. 横向滚动 TierSegmented（layoutScroll）滚动后滑块落点仍对齐；
//   4. GlidePill 位置与宽度同步动画，且与按钮同级、不遮邻居文字；
//   附带：方向键/Home/End 遍历、reduced-motion 瞬切、移动端窄屏取证。
import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");
mkdirSync(SCREENSHOT_DIR, { recursive: true });

const paletteDialog = (page) => page.getByRole("dialog", { name: "命令面板" });
const paletteInput = (page) => page.getByRole("combobox", { name: "搜索股票或功能" });
const glide = (page) => page.locator('#command-palette-listbox > span[aria-hidden="true"]').first();
const paletteRow = (page, idx) => page.locator(`#command-palette-listbox [data-idx="${idx}"]`);

async function openPalette(page) {
  await page.keyboard.press("Control+k");
  await expect(paletteDialog(page)).toBeVisible();
}

async function closePalette(page) {
  await page.keyboard.press("Escape");
  await expect(paletteDialog(page)).toBeHidden();
}

/** 滑块与目标行的 viewport 矩形在 2px 公差内对齐（只比 y/高，滑块 x 有 inset）。 */
async function expectAlignedWithRow(page, row) {
  const glideBox = await glide(page).boundingBox();
  const rowBox = await row.boundingBox();
  expect(glideBox, "glide highlight must have a box").not.toBeNull();
  expect(rowBox, "active row must have a box").not.toBeNull();
  expect(Math.abs(glideBox.y - rowBox.y)).toBeLessThanOrEqual(2);
  expect(Math.abs(glideBox.height - rowBox.height)).toBeLessThanOrEqual(2);
}

test.describe("command palette glide highlight (#113 blocker 1+2)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
  });

  test("highlight is visible on first open, placed without animating from a stale spot", async ({ page }) => {
    await openPalette(page);
    /* 首开立即不透明、且矩形就是第一行——首绘是瞬放，不是从旧位置滑入 */
    await expect(glide(page)).toHaveCSS("opacity", "1");
    await expectAlignedWithRow(page, paletteRow(page, 0));
  });

  test("highlight is visible again after close and reopen", async ({ page }) => {
    await openPalette(page);
    await closePalette(page);
    await openPalette(page);
    await expect(glide(page)).toHaveCSS("opacity", "1");
    await expectAlignedWithRow(page, paletteRow(page, 0));
  });

  test("clear button Enter clears the query and refocuses the input", async ({ page }) => {
    await openPalette(page);
    await paletteInput(page).pressSequentially("NVDA");
    const clear = page.getByRole("button", { name: "清除搜索" });
    await expect(clear).toBeVisible();
    await clear.focus();
    await page.keyboard.press("Enter");
    await expect(paletteInput(page)).toHaveValue("");
    await expect(paletteInput(page)).toBeFocused();
    /* 没有被面板级 Enter 劫持：面板仍开着，没有跳到任何股票页 */
    await expect(paletteDialog(page)).toBeVisible();
    await expect(page).toHaveURL(/\/$/);
  });

  test("clear button Space clears the query as well", async ({ page }) => {
    await openPalette(page);
    await paletteInput(page).pressSequentially("TSLA");
    const clear = page.getByRole("button", { name: "清除搜索" });
    await clear.focus();
    await page.keyboard.press(" ");
    await expect(paletteInput(page)).toHaveValue("");
    await expect(paletteInput(page)).toBeFocused();
  });

  test("Enter on a focused result option fires its action exactly once", async ({ page }) => {
    await openPalette(page);
    let navigations = 0;
    page.on("framenavigated", () => {
      navigations += 1;
    });
    /* 避开「首页」项（从 / 到 / 的同路径导航不可靠）：选「自选」功能项 */
    const option = page.locator('#command-palette-listbox [role="option"]', { hasText: "自选" }).first();
    await option.focus();
    await page.keyboard.press("Enter");
    await page.waitForURL(/\/watchlist$/, { timeout: 5000 });
    await page.waitForTimeout(300);
    expect(navigations, "option Enter must navigate exactly once").toBe(1);
  });
});

test.describe("spring tabs glide (#113 blocker 3+4)", () => {
  test.use({ reducedMotion: "no-preference" });

  async function openScreener(page) {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    /* 窄屏（<1280）桌面导航收起，走底部移动端导航（同 mobile-layout.spec 口径） */
    const viewport = page.viewportSize();
    if (viewport && viewport.width < 1280) {
      await page.getByRole("navigation", { name: "移动端导航" }).getByRole("link", { name: "选股" }).click();
    } else {
      await page.getByRole("link", { name: "选股", exact: true }).first().click();
    }
    await expect(page).toHaveURL(/\/screener$/);
    await expect(page.locator('[aria-label="筛选工作台"]')).toBeVisible();
  }

  const tierList = (page) => page.locator('[role="tablist"][aria-label^="强度分档"]');
  const tierTab = (page, name) => tierList(page).getByRole("tab", { name, exact: false });
  const tierPill = (page) =>
    tierList(page).locator('span[aria-hidden="true"]').filter({ hasNot: page.locator("*") });

  /** 当前激活 tab 与其滑块的完整矩形（位置 + 尺寸）在公差内一致。 */
  async function expectPillMatchesTab(pill, tab, tolerance = 3) {
    const [pillBox, tabBox] = await Promise.all([pill.boundingBox(), tab.boundingBox()]);
    expect(pillBox).not.toBeNull();
    expect(tabBox).not.toBeNull();
    for (const key of ["x", "y", "width", "height"]) {
      expect(Math.abs(pillBox[key] - tabBox[key]), `${key} must track the active tab`).toBeLessThanOrEqual(tolerance);
    }
  }

  /** 逐轴取 max 的落点 poll：等弹簧在四轴上全部进入公差，避免尾部竞态。 */
  async function pollPillSettled(pill, tab, tolerance = 4, timeout = 4000) {
    await expect
      .poll(
        async () => {
          const [p, t] = await Promise.all([pill.boundingBox(), tab.boundingBox()]);
          return Math.max(
            Math.abs(p.x - t.x),
            Math.abs(p.y - t.y),
            Math.abs(p.width - t.width),
            Math.abs(p.height - t.height),
          );
        },
        { timeout },
      )
      .toBeLessThanOrEqual(tolerance);
  }

  test("pill animates width in flight instead of snapping, then settles on the active tab", async ({ page }) => {
    await openScreener(page);
    const narrow = tierTab(page, /^S /).or(tierTab(page, "S")).first();
    const wide = tierTab(page, "全部");
    const pill = tierPill(page);
    await narrow.click();
    await expect(narrow).toHaveAttribute("aria-selected", "true");
    /* 弹簧需要先落定向左（否则会拿飞行中间帧当基准） */
    await pollPillSettled(pill, narrow);

    await wide.click();
    const finalBox = await wide.boundingBox();
    /* 弹簧飞行中段采样：位置或尺寸必须还在补间（不等于终点），不是瞬跳 */
    await page.waitForTimeout(60);
    const midBox = await pill.boundingBox();
    expect(
      Math.abs(midBox.width - finalBox.width) > 1 || Math.abs(midBox.x - finalBox.x) > 1,
      "mid-flight pill must still be tweening (position or size), not snapped to the target",
    ).toBe(true);
    await pollPillSettled(pill, wide);
    await expectPillMatchesTab(pill, wide, 4);
  });

  test("pill is a sibling of the tab buttons, never painted above their labels", async ({ page }) => {
    await openScreener(page);
    /* 结构：滑块不得是任何按钮的后代 */
    await expect(tierList(page).locator('.t-tab span[aria-hidden="true"]')).toHaveCount(0);
    /* 层级：按钮 z-index 为数值（1+），滑块 z-auto 垫在全部按钮之下 */
    const pillZ = await tierPill(page).evaluate((node) => getComputedStyle(node).zIndex);
    const tabZ = await tierTab(page, "全部").evaluate((node) => getComputedStyle(node).zIndex);
    expect(pillZ).toBe("auto");
    expect(Number.parseInt(tabZ, 10)).toBeGreaterThanOrEqual(1);
  });

  test("scrolled horizontal tabs keep the pill aligned (layoutScroll)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openScreener(page);
    /* 数据无关地强制溢出：把分档条夹窄，保证横向可滚动 */
    await page.addStyleTag({ content: '[aria-label^="强度分档"] { max-width: 150px !important; }' });
    const list = tierList(page);
    await expect
      .poll(() => list.evaluate((node) => node.scrollWidth - node.clientWidth))
      .toBeGreaterThan(30);

    /* 滚到最右，选最右的 C：滑块落点必须与 C 对齐 */
    await list.evaluate((node) => node.scrollTo({ left: node.scrollWidth }));
    const last = tierTab(page, /^C /).or(tierTab(page, "C")).first();
    await last.click();
    await expect(last).toHaveAttribute("aria-selected", "true");
    await pollPillSettled(tierPill(page), last);
    await expectPillMatchesTab(tierPill(page), last, 4);

    /* 再滚回最左选「全部」，反向也要对齐 */
    await list.evaluate((node) => node.scrollTo({ left: 0 }));
    const first = tierTab(page, "全部");
    await first.click();
    await pollPillSettled(tierPill(page), first);
    await expectPillMatchesTab(tierPill(page), first, 4);

    /* 移动端窄屏取证（合并门槛 #7） */
    await page.locator('[aria-label="筛选工作台"]').screenshot({
      path: join(SCREENSHOT_DIR, "tier-tabs-scrolled-pill-aligned-390.png"),
    });
  });

  test("arrow keys, Home and End traverse every tab in both tablists", async ({ page }) => {
    await openScreener(page);
    const order = ["全部", "S", "A", "B", "C"];
    await tierTab(page, "全部").click();
    /* ArrowRight 走到底，再按一次环绕回「全部」 */
    for (const name of order.slice(1)) {
      await page.keyboard.press("ArrowRight");
      await expect(tierTab(page, name)).toHaveAttribute("aria-selected", "true");
      await expect(tierTab(page, name)).toBeFocused();
    }
    await page.keyboard.press("ArrowRight");
    await expect(tierTab(page, "全部")).toHaveAttribute("aria-selected", "true");
    /* ArrowLeft 反向环绕 */
    await page.keyboard.press("ArrowLeft");
    await expect(tierTab(page, /^C/).first()).toHaveAttribute("aria-selected", "true");
    /* Home / End */
    await page.keyboard.press("End");
    await expect(tierTab(page, /^C/).first()).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Home");
    await expect(tierTab(page, "全部")).toHaveAttribute("aria-selected", "true");
    /* ArrowDown/ArrowUp 等价于右/左 */
    await page.keyboard.press("ArrowDown");
    await expect(tierTab(page, /^S/).first()).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("ArrowUp");
    await expect(tierTab(page, "全部")).toHaveAttribute("aria-selected", "true");

    /* 行2 的偏好 Segmented（共享组件）同样可遍历（标签：稳健/均衡/进取） */
    const profile = page.locator('[role="tablist"]', { has: page.getByRole("tab", { name: "稳健" }) });
    await profile.getByRole("tab", { name: "均衡" }).click();
    await page.keyboard.press("ArrowRight");
    await expect(profile.getByRole("tab", { name: "进取" })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Home");
    await expect(profile.getByRole("tab", { name: "稳健" })).toHaveAttribute("aria-selected", "true");
  });
});

test.describe("spring tabs under reduced motion", () => {
  /* playwright.config 全局 reducedMotion: "reduce"：MotionConfig duration 0，
     滑块瞬切不做弹簧（duration-0 的静态契约见 motion-tokens.test）。
     headless 软渲染会饿死 rAF，落点用 poll 等而不是定死 100ms。 */
  test("pill lands on the active tab without spring travel", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: "选股", exact: true }).first().click();
    await expect(page).toHaveURL(/\/screener$/);
    const list = page.locator('[role="tablist"][aria-label^="强度分档"]');
    const target = list.getByRole("tab", { name: /^B/ }).first();
    await target.click();
    const pill = list.locator('span[aria-hidden="true"]').filter({ hasNot: page.locator("*") });
    await expect
      .poll(
        async () => {
          const [p, t] = await Promise.all([pill.boundingBox(), target.boundingBox()]);
          return Math.max(Math.abs(p.x - t.x), Math.abs(p.width - t.width));
        },
        { timeout: 2500 },
      )
      .toBeLessThanOrEqual(4);
    /* 落定即终点：再等 200ms 复测仍在原位（无弹簧过冲/回摆） */
    await page.waitForTimeout(200);
    const [p, t] = await Promise.all([pill.boundingBox(), target.boundingBox()]);
    expect(Math.abs(p.x - t.x)).toBeLessThanOrEqual(4);
    expect(Math.abs(p.width - t.width)).toBeLessThanOrEqual(4);
  });
});
