// Catalyst Desk 取证（自旧 frontend/visual-tests/catalyst-desk.spec.mjs 移植收敛）。
//
// 旧版对 vanilla 前端铺了完整的 fixture 场景矩阵；新版 React SPA 的取证目标改为
// 「对 OPTIX_VISUAL_BASE_URL 指向的真实后端拍证据」：访问 / 、/catalysts、/breakouts、
// /market，等网络空闲后截图入档。真实数据不可预置，因此只做宽松断言
// （文档标题、导航壳存在、URL 正确），不断言具体行情内容。
//
// 双模运行：
// - OPTIX_VISUAL_BASE_URL 已设（CI：docker 后端 127.0.0.1:2000）→ 网关带 SPA 回退，
//   深层路由直接 page.goto；
// - 未设（本地默认 python http.server 服务 ../frontend）→ 无 SPA 回退，只 goto /，
//   /catalysts 与 /breakouts 走 Navbar 应用内导航（BrowserRouter 客户端路由），
//   /market 在静态模式下依赖真实行情入口，该用例跳过并注明原因。
import { expect, test } from "@playwright/test";
import { captureEvidence } from "./support/evidence.mjs";

const HAS_REAL_BACKEND = Boolean(process.env.OPTIX_VISUAL_BASE_URL);
// 旧 spec 的防泄露扫描（htmlContainsProjectKey）按原样保留：证据页面绝不允许出现服务机密
const SECRET_PATTERN = /(?:sk-proj-[A-Za-z0-9_-]{20,}|OPENAI_API_KEY|FINNHUB_API_KEY|INTERNAL_API_TOKEN|APP_PASSWORD_HASH)/;

test.use({ viewport: { width: 1440, height: 900 } });

async function screenshot(page, name) {
  await captureEvidence(page, name, { fullPage: false });
}

// 宽松壳断言：标题、Logo、主导航（真实数据不可预置，不断言行情内容）
async function expectShell(page) {
  await expect(page).toHaveTitle("Optix Pro — 投资研究工作台");
  await expect(page.getByRole("link", { name: "Optix Pro 首页" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  expect(SECRET_PATTERN.test(await page.content())).toBe(false);
}

test("Catalyst Desk visual evidence: home /", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  // R4 起 / 是真实首页仪表盘（App.tsx <Route index element={<Home />}>），
  // 不再重定向 /watchlist。
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("region", { name: "指数概览" })).toBeVisible();
  await expectShell(page);
  await screenshot(page, "1440x900-home");
});

test("Catalyst Desk visual evidence: /catalysts", async ({ page }) => {
  if (HAS_REAL_BACKEND) {
    await page.goto("/catalysts", { waitUntil: "domcontentloaded" });
  } else {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: /催化/ }).click();
    await page.waitForLoadState("domcontentloaded");
  }
  await expect(page).toHaveURL(/\/catalysts$/);
  await expectShell(page);
  // NavLink 激活态（react-router 自动设置 aria-current="page"）
  await expect(page.getByRole("link", { name: /催化/ })).toHaveAttribute("aria-current", "page");
  await screenshot(page, "1440x900-catalysts");
});

test("Catalyst Desk visual evidence: /breakouts", async ({ page }) => {
  if (HAS_REAL_BACKEND) {
    await page.goto("/breakouts", { waitUntil: "domcontentloaded" });
  } else {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: /雷达/ }).click();
    await page.waitForLoadState("domcontentloaded");
  }
  await expect(page).toHaveURL(/\/breakouts$/);
  await expectShell(page);
  await expect(page.getByRole("link", { name: /雷达/ })).toHaveAttribute("aria-current", "page");
  await screenshot(page, "1440x900-breakouts");
});

test("Catalyst Desk visual evidence: /market", async ({ page }) => {
  // /market 不在 Navbar 编号导航里（入口是 IndexTape 指数条，依赖真实行情数据），
  // 静态 http.server 无 SPA 回退也无数据 → 仅在真实后端环境取证。
  test.skip(!HAS_REAL_BACKEND, "http.server 无 SPA 回退，/market 需真实后端（OPTIX_VISUAL_BASE_URL）");
  await page.goto("/market", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/market$/);
  await expectShell(page);
  await screenshot(page, "1440x900-market");
});
