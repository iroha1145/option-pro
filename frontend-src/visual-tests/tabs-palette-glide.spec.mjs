// PR #113 审查阻断项的行为回归（真实 DOM 交互，不是源码字符串断言）：
//   1. 命令面板滑行高亮首开/重开立即可见，且**不从旧位置补间**；
//   2. 清除钮 Enter/Space 只清空并回焦输入框，不触发高亮结果；
//   3. 横向滚动 TierSegmented 滚动后滑块落点仍对齐；
//   4. GlidePill 位置与宽度同步动画，且与按钮同级、不遮邻居文字；
//   附带：方向键/Home/End 遍历、reduced-motion 瞬切、移动端窄屏取证。
//
// 两条方法论纪律（都是本 PR 审查里踩过的）：
//   · 「有没有补间」必须看**真实帧序列**。只断言落定态是自欺：Playwright 的
//     toHaveCSS/boundingBox 都会自动重试，等它们通过时弹簧早落定了，从旧位置
//     滑过来的 bug 照样全绿。用装在动作**之前**的 rAF 采样器判定。
//   · 定位元素用稳定的 data-* 句柄，不用「无子元素的 aria-hidden span」这类
//     结构指纹，也不断言 z-index 具体数值——给滑块加个装饰子元素或改用
//     isolation 分层，指纹就静默失配，而实际行为没变。
import { expect, test } from "@playwright/test";
import { captureEvidence } from "./support/evidence.mjs";

const paletteDialog = (page) => page.getByRole("dialog", { name: "命令面板" });
const paletteInput = (page) => page.getByRole("combobox", { name: "搜索股票或功能" });
const GLIDE_SELECTOR = "#command-palette-listbox [data-glide-list]";
const glide = (page) => page.locator(GLIDE_SELECTOR);
const paletteRow = (page, idx) => page.locator(`#command-palette-listbox [data-idx="${idx}"]`);

async function openPalette(page) {
  await page.keyboard.press("Control+k");
  await expect(paletteDialog(page)).toBeVisible();
}

async function closePalette(page) {
  await page.keyboard.press("Escape");
  await expect(paletteDialog(page)).toBeHidden();
}

/**
 * 装上按帧采样器（必须在触发动作**之前**调用）：元素不存在的帧自动跳过，
 * 上限 120 帧防止长测试堆积。
 *
 * 采的是**滑块相对当前高亮行的偏移**，不是视口绝对坐标：面板自己有入场
 * 动画（t-modal 的位移/缩放），绝对坐标在开场那几帧会整体漂几个像素，用它
 * 判「有没有从旧行滑过来」会把面板入场误判成滑块走位。相对量则两者同步
 * 移动、恒为 0，而真出 bug 时（从上一次的 active 行补间过来）差值有整行高。
 */
async function startGlideDeltaSampler(page) {
  await page.evaluate((sel) => {
    window.__rectSamples = [];
    const tick = () => {
      const glide = document.querySelector(sel);
      const row = document.querySelector('#command-palette-listbox [role="option"][aria-selected="true"]');
      if (glide && row && window.__rectSamples.length < 120) {
        const g = glide.getBoundingClientRect();
        const r = row.getBoundingClientRect();
        window.__rectSamples.push({ dy: g.y - r.y, dh: g.height - r.height });
      }
      window.__rectRaf = requestAnimationFrame(tick);
    };
    tick();
  }, GLIDE_SELECTOR);
}

async function startRectSampler(page, selector) {
  await page.evaluate((sel) => {
    window.__rectSamples = [];
    const tick = () => {
      const el = document.querySelector(sel);
      if (el && window.__rectSamples.length < 120) {
        const r = el.getBoundingClientRect();
        window.__rectSamples.push({ x: r.x, y: r.y, w: r.width, h: r.height });
      }
      window.__rectRaf = requestAnimationFrame(tick);
    };
    tick();
  }, selector);
}

async function stopRectSampler(page) {
  /* 断言（toHaveAttribute 等）可能在滑块/选中行刚落地的同一帧内就满足，此时 rAF
     还没来得及记下任何样本就被 stop——"sampler must have caught at least one frame"
     偶发零样本即此。停之前先让出两帧，保证至少记到一帧稳定态。 */
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)));
  }));
  return page.evaluate(() => {
    cancelAnimationFrame(window.__rectRaf);
    return window.__rectSamples ?? [];
  });
}

/** 每一帧滑块都贴在它那一行上——从没有从别处滑过来。 */
function expectGlideNeverTravelled(samples, tolerance = 2) {
  expect(samples.length, "sampler must have caught at least one frame").toBeGreaterThan(0);
  for (const sample of samples) {
    expect(
      Math.abs(sample.dy),
      `glide must sit on its row every frame: saw a ${sample.dy}px offset`,
    ).toBeLessThanOrEqual(tolerance);
    expect(Math.abs(sample.dh), `glide height must match its row: saw ${sample.dh}px`).toBeLessThanOrEqual(tolerance);
  }
}

/** 等滑块落到目标行上（同批内换行是**允许**滑行的，所以要等它落定）。 */
async function pollGlideOnRow(page, row) {
  await expect
    .poll(async () => {
      const [g, r] = await Promise.all([glide(page).boundingBox(), row.boundingBox()]);
      if (!g || !r) return Number.POSITIVE_INFINITY;
      return Math.abs(g.y - r.y);
    })
    .toBeLessThanOrEqual(2);
}

test.describe("command palette glide highlight (#113 blocker 1+2)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
  });

  test("highlight is visible on first open, placed without animating from a stale spot", async ({ page }) => {
    await startGlideDeltaSampler(page);
    await openPalette(page);
    await expect(glide(page)).toHaveCSS("opacity", "1");
    const samples = await stopRectSampler(page);
    /* 首绘是瞬放：采样器从面板出现的第一帧起，滑块就一直贴在高亮行上 */
    expectGlideNeverTravelled(samples);
    await expect(paletteRow(page, 0)).toHaveAttribute("aria-selected", "true");
  });

  test("reopening after moving the highlight does not tween it from the old row", async ({ page }) => {
    /* 关键是先把高亮挪下去再关：面板 mounted 与 open 同一次渲染翻真，而
       active 归零在 passive effect 里，所以重开时高亮很容易先按上一次的
       active 落笔、再滑回第一行——只断言落定态的测试看不见这一段。 */
    await openPalette(page);
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await expect(paletteRow(page, 2)).toHaveAttribute("aria-selected", "true");
    await closePalette(page);

    await startGlideDeltaSampler(page);
    await openPalette(page);
    await expect(glide(page)).toHaveCSS("opacity", "1");
    const samples = await stopRectSampler(page);
    expectGlideNeverTravelled(samples);
    await expect(paletteRow(page, 0)).toHaveAttribute("aria-selected", "true");
  });

  test("clearing the query re-places the highlight instantly on the new batch", async ({ page }) => {
    /* 换批（股票批 → 最近/功能批）是单次渲染完成的，中间没有空列表可以
       复位「已落笔」标志，所以这条路径同样会从旧股票行滑过来。 */
    await openPalette(page);
    await paletteInput(page).pressSequentially("NVDA");
    const clear = page.getByRole("button", { name: "清除搜索" });
    await expect(clear).toBeVisible();
    await page.keyboard.press("ArrowDown");

    await startGlideDeltaSampler(page);
    await clear.click();
    await expect(paletteInput(page)).toHaveValue("");
    await expect(paletteRow(page, 0)).toHaveAttribute("aria-selected", "true");
    const samples = await stopRectSampler(page);
    expectGlideNeverTravelled(samples);
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

  test("keyboard focus keeps the visible highlight and Enter in agreement", async ({ page }) => {
    /* 面板级 Enter 让原生元素自己派发 click，所以焦点行必须始终就是那条
       唯一可见的高亮行——否则 Tab 到第 1 行、Enter 打开的却是第 0 行。 */
    await openPalette(page);
    const second = paletteRow(page, 1);
    await second.focus();
    await expect(second).toHaveAttribute("aria-selected", "true");
    await expect(paletteRow(page, 0)).toHaveAttribute("aria-selected", "false");
    /* 同一批内换行是允许滑行的，所以等它落定再比几何，而不是拿补间中途帧 */
    await pollGlideOnRow(page, second);
  });

  test("IME composition keystrokes are not hijacked by the panel", async ({ page }) => {
    /* 中文名搜索是产品自己在空态里推荐的用法：组词确认的那次回车必须留给
       输入法，不能被面板抢去「打开当前高亮项」并把组词内容丢掉。 */
    await openPalette(page);
    const input = paletteInput(page);
    await input.focus();
    await input.evaluate((el) => {
      el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
      el.value = "ying";
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await input.evaluate((el) => {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 229, isComposing: true, bubbles: true }));
    });
    await expect(paletteDialog(page), "composing Enter must not close the palette").toBeVisible();
    await expect(page).toHaveURL(/\/$/);
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

  const TIER_LIST = '[role="tablist"][aria-label^="强度分档"]';
  const TIER_PILL = `${TIER_LIST} [data-glide-pill]`;
  const tierList = (page) => page.locator(TIER_LIST);
  const tierTab = (page, name) => tierList(page).getByRole("tab", { name, exact: false });
  const tierPill = (page) => page.locator(TIER_PILL);

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

  test("pill animates position and width in flight instead of snapping", async ({ page }) => {
    await openScreener(page);
    const narrow = tierTab(page, /^S /).or(tierTab(page, "S")).first();
    const wide = tierTab(page, "全部");
    const pill = tierPill(page);
    await narrow.click();
    await expect(narrow).toHaveAttribute("aria-selected", "true");
    /* 弹簧需要先落定向左（否则会拿飞行中间帧当基准） */
    await pollPillSettled(pill, narrow);

    /* 采样器先装上，再点击：中途帧由真实 rAF 序列给出，不靠挂钟猜时刻
       （CI 争抢时 waitForTimeout(60) 之后弹簧可能早已落定，必翻）。 */
    await startRectSampler(page, TIER_PILL);
    await wide.click();
    await pollPillSettled(pill, wide);
    const samples = await stopRectSampler(page);
    const last = samples[samples.length - 1];
    const travelled = samples.some(
      (s) => Math.abs(s.x - last.x) > 1 || Math.abs(s.w - last.w) > 1,
    );
    expect(travelled, "pill must tween position AND size, not snap to the target").toBe(true);
    /* 尺寸确实补间过：宽度不是一步到位（position-only 投影会让宽度瞬跳） */
    const widthsSeen = new Set(samples.map((s) => Math.round(s.w)));
    expect(widthsSeen.size, "width must interpolate, not jump in one step").toBeGreaterThan(1);
    await expectPillMatchesTab(pill, wide, 4);
  });

  test("pill is a sibling of the tab buttons, never painted above their labels", async ({ page }) => {
    await openScreener(page);
    /* 结构：滑块不得是任何按钮的后代 */
    await expect(tierList(page).locator(".t-tab [data-glide-pill]")).toHaveCount(0);
    /* 行为（不看 z-index 数值）：激活标签文字中心点上，命中测试拿到的必须是
       按钮自己（或其后代），而不是滑块。 */
    const active = tierTab(page, "全部");
    await pollPillSettled(tierPill(page), active);
    const topmostIsTab = await active.evaluate((node) => {
      const r = node.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return Boolean(hit && (hit === node || node.contains(hit)));
    });
    expect(topmostIsTab, "the active tab label must be the topmost element at its own centre").toBe(true);
  });

  test("scrolled horizontal tabs keep the pill aligned", async ({ page }) => {
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
    await captureEvidence(page.locator('[aria-label="筛选工作台"]'), "tier-tabs-scrolled-pill-aligned-390");
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
  /* playwright.config 全局 reducedMotion: "reduce"：GlidePill 自持的
     useReducedMotion 把 transition 归零，滑块瞬切不做弹簧。
     headless 软渲染会饿死 rAF，落点用 poll 等而不是定死 100ms。 */
  test("pill lands on the active tab without spring travel", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: "选股", exact: true }).first().click();
    await expect(page).toHaveURL(/\/screener$/);
    const list = page.locator('[role="tablist"][aria-label^="强度分档"]');
    const target = list.getByRole("tab", { name: /^B/ }).first();
    const pill = page.locator('[role="tablist"][aria-label^="强度分档"] [data-glide-pill]');
    await target.click();
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
