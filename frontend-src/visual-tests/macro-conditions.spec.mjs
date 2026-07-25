// Optix 宏观环境 取证（Playwright）。
//
// 与 catalyst-desk.spec.mjs 不同，本 spec 需要**可控状态**（active / degraded /
// stale / disabled / insufficient_history），因此沿用 password-mode.spec.mjs 的做法：
// 用 page.route 拦下全部 /api/**，返回按后端契约核对过的内联 fixture。
// 完全离线：不访问 FRED、不访问真实行情源、不访问 OpenAI、不访问生产服务器。
//
// 到达 /market 的方式：本地静态产物服务器没有 SPA 回退，因此在测试里补上一个——
// 对无扩展名文档路径返回 index.html，正是生产网关 main.py::_is_spa_document_path
// 的行为。比点击指数纸带更稳：纸带是 animate-marquee 持续位移的元素。
import { expect, test } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { join } from "node:path";

const INDEX_HTML = join(process.cwd(), "..", "frontend", "index.html");

const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");
const NOW = "2026-07-24T22:30:00Z";
const TODAY = "2026-07-24";
const YESTERDAY = "2026-07-23";
const SECRET_PATTERN =
  /(?:sk-proj-[A-Za-z0-9_-]{20,}|OPENAI_API_KEY|FINNHUB_API_KEY|FRED_API_KEY|INTERNAL_API_TOKEN|APP_PASSWORD_HASH|macro-conditions\.db)/;

// 合成数值，故意与任何第三方报告的示例分数都不相同（incremental review P3）。
// Mock 文件声明「数值是合成的，不复制第三方报告示例分数」，而这里原本硬编码的
// 七个分数与那份 PDF 完全一致 —— 不影响生产算法，但会让后来的人以为这些截图在
// 复现别人的产品。改成另一组确定性合成值，注释与事实重新对上。
const MODULES = [
  { id: "liquidity", zh: "流动性", en: "LIQUIDITY", total: 5, floor: 3, score: 58.2 },
  { id: "funding", zh: "融资", en: "FUNDING", total: 6, floor: 4, score: 33.7 },
  { id: "treasury", zh: "国债", en: "TREASURY", total: 3, floor: 2, score: 71.9 },
  { id: "rates", zh: "利率", en: "RATES", total: 3, floor: 2, score: 47.5 },
  { id: "credit", zh: "信用", en: "CREDIT", total: 4, floor: 3, score: 62.4 },
  { id: "risk", zh: "风险", en: "RISK", total: 4, floor: 3, score: 39.8 },
  { id: "external", zh: "外部冲击", en: "EXTERNAL", total: 5, floor: 3, score: 66.1 },
];

function moduleRows(overrides = {}) {
  return MODULES.map((module) => ({
    module_id: module.id,
    display_name_zh: module.zh,
    display_name_en: module.en,
    score: module.id in overrides ? overrides[module.id] : module.score,
    score_change_7d: module.id in overrides && overrides[module.id] === null ? null : -1.4,
    confidence: 1,
    valid_factor_count: module.id in overrides && overrides[module.id] === null ? 1 : module.total,
    total_factor_count: module.total,
    minimum_valid_factors: module.floor,
    data_through: YESTERDAY,
    status: module.id in overrides && overrides[module.id] === null ? "insufficient_factors" : "ok",
    formatted_score: module.id in overrides && overrides[module.id] === null ? null : `${module.score} 分`,
  }));
}

function conditions(overrides = {}) {
  const modules = overrides.modules ?? moduleRows();
  const valid = modules.filter((item) => item.score !== null).length;
  const mean = modules
    .filter((item) => item.score !== null)
    .reduce((sum, item) => sum + item.score, 0) / Math.max(1, valid);
  return {
    status: "active",
    reason: null,
    as_of: NOW,
    data_through: YESTERDAY,
    scoring_version: "optix-macro-score-v1",
    history_basis: "latest_revised_backfill",
    composite: {
      score: Math.round(mean * 10) / 10,
      score_change_7d: -4.1,
      confidence: 0.91,
      regime: "中性",
      valid_module_count: valid,
      total_module_count: 7,
      snapshot_date: TODAY,
      formatted_score: `${Math.round(mean * 10) / 10} 分`,
    },
    modules,
    drivers: {
      improving: [
        { factor_id: "vix", module_id: "risk", display_name_zh: "VIX 波动率", score: 71.2, score_change_7d: 12.4 },
        { factor_id: "nfci", module_id: "credit", display_name_zh: "全国金融条件指数", score: 63.8, score_change_7d: 6.1 },
      ],
      deteriorating: [
        { factor_id: "fed_net_liquidity", module_id: "liquidity", display_name_zh: "联储净流动性", score: 22.5, score_change_7d: -18.7 },
        { factor_id: "real_rate_level", module_id: "rates", display_name_zh: "实际利率水平", score: 30.1, score_change_7d: -9.2 },
      ],
    },
    warnings: [],
    sources: [
      "Board of Governors of the Federal Reserve System (US), H.4.1",
      "Cboe Global Markets",
      "Federal Reserve Bank of Chicago",
      "Federal Reserve Bank of New York",
    ],
    ...overrides,
  };
}

function history(points = 120) {
  const items = [];
  for (let index = points; index >= 0; index -= 1) {
    const day = new Date(Date.UTC(2026, 6, 24) - index * 86_400_000)
      .toISOString()
      .slice(0, 10);
    const score = 50 + 14 * Math.sin(index / 11);
    const moduleScores = {};
    for (const module of MODULES) {
      moduleScores[module.id] = Math.round((score + module.score / 6 - 8) * 10) / 10;
    }
    items.push({
      date: day,
      score: Math.round(score * 10) / 10,
      confidence: 1,
      regime: score >= 55 ? "偏松" : score >= 45 ? "中性" : "偏紧",
      history_basis: index > 60 ? "latest_revised_backfill" : "local_point_in_time",
      module_scores: moduleScores,
    });
  }
  return { status: "active", days: points, points: items };
}

function moduleDetail(moduleId) {
  const module = MODULES.find((item) => item.id === moduleId) ?? MODULES[0];
  const factors = Array.from({ length: module.total }, (_value, index) => ({
    factor_id: `${moduleId}_factor_${index + 1}`,
    module_id: moduleId,
    display_name_zh: `${module.zh}因子 ${index + 1}`,
    description_zh: "取证 fixture：合成示例，不是真实数据。",
    formula_version: "optix-macro-factor-v1",
    raw_value: 1.234 + index,
    formatted_value: `${(1.234 + index).toFixed(3)} 个百分点`,
    signed_value: null,
    formatted_signed_value: null,
    unit: { unit: "percentage_points", symbol_zh: "个百分点", decimals: 3 },
    score: 40 + index * 7,
    score_method: "supportive_low_percentile",
    direction: "low",
    raw_change_7d: 0.012,
    formatted_raw_change_7d: "+0.012 个百分点",
    score_change_7d: index % 2 === 0 ? 3.4 : -2.7,
    confidence: 1,
    valid_observations: 1258,
    minimum_history: 252,
    status: "ok",
    data_through: YESTERDAY,
    history_basis: "latest_revised_backfill",
    missing_inputs: [],
    stale_inputs: [],
    source: ["纽约联储"],
  }));
  return {
    status: "active",
    module_id: moduleId,
    display_name_zh: module.zh,
    display_name_en: module.en,
    as_of: NOW,
    snapshot_date: TODAY,
    scoring_version: "optix-macro-score-v1",
    module: moduleRows().find((item) => item.module_id === moduleId) ?? null,
    factors,
  };
}

/** 其余 /market 页面依赖的公共只读端点：给最小合法信封，页面才能渲染到宏观区块。 */
function otherResponses(pathname) {
  if (pathname === "/api/market/indices") {
    return {
      indices: [
        { symbol: "^GSPC", price: 5908.23, change_percent: -1.07 },
        { symbol: "^NDX", price: 21263.81, change_percent: -0.95 },
      ],
      as_of: NOW,
    };
  }
  if (pathname === "/api/market/status") {
    return { market: "closed", phase: "weekend", is_open: false, as_of: NOW };
  }
  if (pathname === "/api/strength/market") {
    return {
      market_regime: {
        index_trend_score: 72,
        market_momentum_score: 68,
        market_breadth_score: 61,
        market_volume_score: 55,
        risk_appetite_score: 58,
        risk_on_spread_score: 63,
      },
      aggregate_available: false,
      histogram: [],
    };
  }
  if (pathname === "/api/signals/market") return { by_type: [], total_today: 0 };
  if (pathname === "/api/access/status") {
    return { access_mode: "password", logged_in: false, ai_available: false };
  }
  return {};
}

/** 复刻生产网关的 SPA 回退：无扩展名文档路径回 index.html。 */
async function stubSpaFallback(page) {
  const shell = await readFile(INDEX_HTML, "utf8");
  await page.route(/\/market(\?.*)?$/, (route) => {
    if (route.request().resourceType() !== "document") return route.continue();
    return route.fulfill({ contentType: "text/html", body: shell });
  });
}

async function stubApi(page, macro) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    if (pathname === "/api/macro/conditions") {
      return route.fulfill({ json: macro.conditions });
    }
    if (pathname === "/api/macro/conditions/history") {
      return route.fulfill({ json: macro.history ?? { status: "unavailable", points: [] } });
    }
    if (pathname.startsWith("/api/macro/conditions/modules/")) {
      const moduleId = pathname.split("/").pop();
      return route.fulfill({ json: moduleDetail(moduleId) });
    }
    if (pathname.startsWith("/api/macro/")) {
      return route.fulfill({ status: 401, json: { code: "owner_login_required" } });
    }
    return route.fulfill({ json: otherResponses(pathname) });
  });
}

async function openMarket(page) {
  await stubSpaFallback(page);
  await page.goto("/market", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/market/);
  await expect(page.locator('section[aria-label="宏观环境"]')).toBeVisible();
}

async function shot(page, name) {
  await mkdir(SCREENSHOT_DIR, { recursive: true });
  const section = page.locator('section[aria-label="宏观环境"]');
  await section.scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(SCREENSHOT_DIR, `${name}.png`), fullPage: true });
  expect(SECRET_PATTERN.test(await page.content())).toBe(false);
}

async function expectNoHorizontalOverflow(page) {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
}

/* -------------------------------- 桌面取证 -------------------------------- */

test.describe("macro conditions desktop", () => {
  test.use({ viewport: { width: 1440, height: 1200 } });

  test("active state renders the composite, seven modules and drivers", async ({ page }) => {
    await stubApi(page, { conditions: conditions(), history: history() });
    await openMarket(page);

    await expect(page.getByText("宏观环境 · MACRO CONDITIONS")).toBeVisible();
    await expect(page.getByRole("region", { name: "宏观环境综合分", exact: true })).toBeVisible();
    await expect(
      page
        .getByRole("region", { name: "宏观环境综合分", exact: true })
        .getByText("中性")
        .first(),
    ).toBeVisible();
    // 七个模块卡
    for (const module of MODULES) {
      await expect(page.getByRole("article", { name: `${module.zh} 模块`, exact: true })).toBeVisible();
    }
    // 「历史分位，不是预测」必须在页面上
    await expect(
      page.getByText(/分数是过去 5 年的历史分位，不是预测/).first(),
    ).toBeVisible();
    // 驱动因素两张卡
    await expect(page.getByRole("region", { name: "7 日分数改善最多", exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "7 日分数恶化最多", exact: true })).toBeVisible();
    // 访客看不到 Owner 刷新按钮
    await expect(page.getByRole("button", { name: /刷新宏观数据/ })).toHaveCount(0);
    await expect(page.getByText("登录后可手动刷新")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await shot(page, "macro-active-1440-light");
  });

  test("history chart exposes ranges and the revision-basis note", async ({ page }) => {
    await stubApi(page, { conditions: conditions(), history: history() });
    await openMarket(page);
    const chart = page.getByRole("region", { name: "宏观环境历史", exact: true });
    await expect(chart).toBeVisible();
    for (const range of ["30D", "90D", "1Y", "5Y"]) {
      await expect(chart.getByRole("button", { name: range })).toBeVisible();
    }
    await expect(chart.getByRole("img", { name: "宏观环境综合分历史曲线" })).toBeVisible();
    await expect(
      page.getByText(/虚线段表示该区间按当前修订值回算/),
    ).toBeVisible();
    await chart.getByRole("button", { name: "5Y" }).click();
    await expect(chart.getByRole("button", { name: "5Y" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await shot(page, "macro-history-1440");
  });

  test("factor details expand into a table with units and formula copy", async ({ page }) => {
    await stubApi(page, { conditions: conditions(), history: history() });
    await openMarket(page);
    const accordion = page.getByLabel("因子详情", { exact: true });
    const trigger = accordion.locator("h3 > button").filter({ hasText: "融资" });
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.click();
    await expect(trigger).toHaveAttribute("aria-expanded", "true");
    await expect(accordion.getByRole("columnheader", { name: "历史分位" })).toBeVisible();
    await expect(accordion.getByRole("columnheader", { name: "7 日原值变化" })).toBeVisible();
    await expect(accordion.getByText("融资因子 1").first()).toBeVisible();
    await expect(accordion.getByText("1.234 个百分点").first()).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await shot(page, "macro-factor-details-expanded-1440");
  });

  test("an info hint is reachable and readable from the keyboard", async ({ page }) => {
    await stubApi(page, { conditions: conditions(), history: history() });
    await openMarket(page);
    const hint = page
      .getByRole("button", { name: /宏观环境综合分（0–100 分）：查看评分说明/ })
      .first();
    await hint.focus();
    await expect(hint).toBeFocused();
    const tooltip = page.getByRole("tooltip").filter({ hasText: "至少 5 个模块有效才出正式分" });
    await expect(tooltip.first()).toBeVisible();
    // Enter 切换、Escape 关闭
    await hint.press("Enter");
    await hint.press("Escape");
    await expectNoHorizontalOverflow(page);
  });
});

/* -------------------------------- 降级取证 -------------------------------- */

test.describe("macro conditions degraded states", () => {
  test.use({ viewport: { width: 1440, height: 1200 } });

  test("degraded keeps the panel and surfaces the warning", async ({ page }) => {
    const overrides = { risk: null };
    await stubApi(page, {
      conditions: conditions({
        status: "degraded",
        modules: moduleRows(overrides),
        warnings: ["fred_unavailable", "etf_history_unavailable"],
      }),
      history: history(),
    });
    await openMarket(page);
    await expect(page.getByText("部分数据缺失")).toBeVisible();
    await expect(page.getByText(/上游告警：/)).toBeVisible();
    // 图表未被清空，模块卡仍在，缺分模块如实说明门槛
    await expect(page.getByRole("region", { name: "宏观环境历史", exact: true })).toBeVisible();
    await expect(page.getByLabel("风险 模块")).toBeVisible();
    await expect(page.getByText(/有效因子不足.*门槛，本模块不出分（不按 50 补齐）/)).toBeVisible();
    await shot(page, "macro-degraded-1440");
  });

  test("stale marks the data as old without zeroing anything", async ({ page }) => {
    await stubApi(page, {
      conditions: conditions({ status: "stale", warnings: ["fred_unavailable"] }),
      history: history(),
    });
    await openMarket(page);
    await expect(page.getByText("数据陈旧")).toBeVisible();
    await expect(page.getByRole("region", { name: "宏观环境综合分", exact: true })).toBeVisible();
    await shot(page, "macro-stale-1440");
  });

  test("disabled explains the missing key without any key management entry", async ({ page }) => {
    await stubApi(page, {
      conditions: {
        status: "disabled",
        reason: "fred_api_key_missing",
        as_of: null,
        data_through: null,
        scoring_version: "optix-macro-score-v1",
        history_basis: null,
        composite: null,
        modules: [],
        drivers: { improving: [], deteriorating: [] },
        warnings: ["fred_api_key_missing"],
        sources: [],
      },
      history: { status: "disabled", points: [] },
    });
    await openMarket(page);
    await expect(page.getByText("宏观数据源尚未配置")).toBeVisible();
    await expect(page.getByText(/配置只能在服务器端完成/)).toBeVisible();
    // 页面不得出现任何密钥输入或密钥值
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    expect(SECRET_PATTERN.test(await page.content())).toBe(false);
    await shot(page, "macro-disabled-1440");
  });

  test("no official composite is shown when modules fall short", async ({ page }) => {
    const overrides = { risk: null, credit: null, external: null };
    await stubApi(page, {
      conditions: conditions({
        status: "insufficient_history",
        modules: moduleRows(overrides),
        composite: null,
        warnings: ["macro_insufficient_modules"],
      }),
      history: { status: "unavailable", points: [] },
    });
    await openMarket(page);
    await expect(page.getByText("暂无正式综合分")).toBeVisible();
    await expect(
      page.getByText(/有效模块不足 5 个时不输出正式综合分/),
    ).toBeVisible();
    // 历史为空时如实说明正在积累，而不是画一条 0 线
    await expect(page.getByText(/历史正在积累/)).toBeVisible();
    await shot(page, "macro-insufficient-1440");
  });

  test("empty drivers show an honest empty state instead of zeros", async ({ page }) => {
    await stubApi(page, {
      conditions: conditions({ drivers: { improving: [], deteriorating: [] } }),
      history: history(),
    });
    await openMarket(page);
    await expect(
      page.getByText(/暂无可比的 7 日历史快照/).first(),
    ).toBeVisible();
    await expect(page.getByText("0.0 分")).toHaveCount(0);
  });
});

/* -------------------------------- 响应式取证 -------------------------------- */

for (const viewport of [
  { width: 320, height: 900 },
  { width: 390, height: 900 },
  { width: 768, height: 1024 },
  { width: 1024, height: 1000 },
]) {
  test.describe(`macro conditions responsive ${viewport.width}`, () => {
    test.use({ viewport });

    test("the panel fits the viewport with no horizontal scroll", async ({ page }) => {
      await stubApi(page, { conditions: conditions(), history: history(60) });
      await openMarket(page);
      const section = page.locator('section[aria-label="宏观环境"]');
      await expect(section).toBeVisible();
      await expect(page.getByRole("region", { name: "宏观环境综合分", exact: true })).toBeVisible();
      for (const module of MODULES) {
        await expect(page.getByRole("article", { name: `${module.zh} 模块`, exact: true })).toBeVisible();
      }
      await expectNoHorizontalOverflow(page);
      // 展开因子详情后仍然不允许横向滚动（320 用纵向卡片而非表格）
      await page
        .getByLabel("因子详情", { exact: true })
        .locator("h3 > button")
        .filter({ hasText: "流动性" })
        .click();
      // 桌面表格与移动卡片都在 DOM 里，由 CSS 隐藏其一；断言「可见的那一个」，
      // 并顺带证明窄屏走的是纵向卡片而不是会横向溢出的表格。
      await expect(
        page.getByText("流动性因子 1").filter({ visible: true }),
      ).toHaveCount(1);
      const visibleFactor = page.getByText("流动性因子 1").filter({ visible: true });
      await expect(visibleFactor).toBeVisible();
      if (viewport.width < 768) {
        await expect(visibleFactor.locator("xpath=ancestor::li[1]")).toBeVisible();
      }
      await expectNoHorizontalOverflow(page);
      await shot(page, `macro-active-${viewport.width}`);
    });
  });
}

/* ---------------------------- 配色偏好与减少动效 ---------------------------- */

test.describe("macro conditions colour scheme and motion", () => {
  test.use({
    viewport: { width: 1440, height: 1200 },
    colorScheme: "dark",
    reducedMotion: "reduce",
  });

  test("renders under a dark colour-scheme preference without breaking", async ({ page }) => {
    // 说明：当前 Optix SPA 只有一套「纸面终端」亮色主题（tailwind darkMode 已配置
    // 但没有任何地方给根元素加 .dark，也没有主题切换器）。此用例证明宏观面板在
    // prefers-color-scheme: dark 下依然完整可读、不破版，配合前端契约测试
    // 「macro components use design tokens only」保证零硬编码颜色。
    await stubApi(page, { conditions: conditions(), history: history() });
    await openMarket(page);
    await expect(page.getByRole("region", { name: "宏观环境综合分", exact: true })).toBeVisible();
    for (const module of MODULES) {
      await expect(page.getByRole("article", { name: `${module.zh} 模块`, exact: true })).toBeVisible();
    }
    await expectNoHorizontalOverflow(page);
    await shot(page, "macro-active-1440-dark-preference");
  });

  test("reduced motion is respected: the score settles at its real value", async ({ page }) => {
    // playwright.config.mjs 全局 reducedMotion: "reduce"
    await stubApi(page, { conditions: conditions(), history: history() });
    await openMarket(page);
    // 断言可观察结果而不是 matchMedia：本版 Playwright 的 reducedMotion emulation
    // 不反映到页面的 matchMedia，探测它只能测到 harness 而不是应用行为。
    // useCountUp 的 reduced-motion 分支由 macro-conditions-contract.test.mjs 在源码层守护。
    const composite = page.getByRole("region", { name: "宏观环境综合分", exact: true });
    await expect(composite.locator("span.text-data-xxl")).not.toHaveText("0.0");
    await expect(composite.locator("span.text-data-xxl")).toHaveText(/^\d+\.\d$/);
  });
});
