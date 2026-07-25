// 口令模式端到端（自旧 frontend/visual-tests/password-mode.spec.mjs 移植，面向 React SPA 重写）。
//
// 服务端：visual-tests/support/password_server.py —— 真实后端（HTTPS、mode=password），
// FRONTEND_DIR = 仓库 ../frontend（Vite 构建产物，VITE_API_MODE=live）。
// 数据面：沿用旧 spec 的 API 拦截 fixture 思路 —— page.route 拦下除 /api/access/* 外的
// 全部 /api/**，返回按 AUDIT-live.md 契约核对过形状的内联 fixture；
// 访问域（status/login/logout）放行给真实后端，验证真实 Cookie 会话。
//
// UI 流程按新 SPA 重写，选择器全部用文案/角色定位（不改业务源码、不加 data-testid）：
// - 访客标记：Navbar.tsx L145-150 的「登录」链接（to=/login）
// - Owner 标记：Navbar 的「退出」按钮 + 读取真实后端可用性的 AI 徽标
//   （fixture 可用时 title="分析服务可用"）
// - 登录页：Login.tsx L370-381 密码输入（aria-label="访问密码"）、L396-421 提交按钮
//   （idle 文案「登录」）、L424-433 状态行（tone=error 时 role="alert"；
//   mapError L215-222：未知业务码 →「密码不正确」，渲染时追加「，请重试」）
// - 旧 spec 的 #owner-ai-toggle（runtime-settings 乐观锁 PUT + version_conflict 重试）
//   在新 UI 无对应控件：AUDIT-live.md「当前无页面消费 settings/updateSettings/history/
//   rollback」，Navbar 仅有只读 AI 徽标。该交互不移植（不伪造 UI）；owner 专属交互改由
//   Watchlist.tsx L88-107「强制刷新」按钮的 disabled/title 状态验证（乐观锁逻辑仍在
//   src/api/modules/runtime.ts，等未来设置页接入后再补 E2E）。
import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";


const PASSWORD_BASE_URL = process.env.OPTIX_PASSWORD_BASE_URL || "https://127.0.0.1:8768";
const OWNER_PASSWORD = "optix-browser-test-password-2026";
const NOW = "2026-07-16T12:00:00Z";
const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");


function catalystNews() {
  return {
    news_id: "password-e2e-news",
    source: "Local Fixture",
    title_zh: "公开浏览可见的中文新闻标题",
    summary_zh: "这条本地记录用于检查公开阅读与所有者操作边界，不会调用外部服务。",
    published_at: NOW,
    fetched_at: NOW,
    analysis_status: "not_requested",
    analysis_trigger_enabled: true,
    analysis_availability: { enabled: true, reason: "available" },
  };
}


// 契约形状核对依据：frontend-src/AUDIT-live.md 审计表 + src/api/modules/* 的 live 归一器
// （snake_case 信封：watchlist {groups[].stocks[]}、indices {indices[]}、
//   catalysts feed {items,summary}、market status {market,phase,...}）。
function responseFor(pathname) {
  if (pathname === "/api/market/indices") {
    return { indices: [], as_of: NOW, source_status: "active", data_limited: false };
  }
  if (pathname === "/api/market/status") {
    return { market: "closed", phase: "weekend", is_open: false, exchange_timezone: "America/New_York", as_of: NOW };
  }
  if (pathname === "/api/stocks/watchlist") {
    return {
      groups: [{
        id: "core",
        name: "本地验收",
        stocks: [{ ticker: "NVDA", name: "英伟达", price: 142.35, change: 1.2, change_percent: 0.85, spark: [140, 141, 142.35], quote_as_of: NOW }],
      }],
      as_of: NOW,
      data_through: NOW,
      oldest_quote_at: NOW,
      latest_quote_at: NOW,
      source_status: "active",
      attempted: 1,
      succeeded: 1,
      failed: 0,
      failed_tickers: [],
      delayed: 0,
      delayed_tickers: [],
    };
  }
  if (pathname === "/api/earnings/upcoming") return { earnings: [] };
  if (pathname === "/api/options/unusual") return { results: [], attempted: 0, as_of: NOW };
  // 新 Watchlist 页额外轮询的两个公共只读端点（旧 spec 无；形状按契约信封给最小值）
  if (pathname === "/api/signals/market") return { by_type: [], total_today: 0 };
  if (pathname === "/api/strength/market") return {};
  if (pathname === "/api/stocks/NVDA") return { ticker: "NVDA", price: 142.35, change_percent: 0.85, volume: 48_000_000, market_cap: 3_500_000_000_000 };
  if (pathname === "/api/stocks/NVDA/chart") return { bars: [{ t: NOW, o: 140, h: 143, l: 139, c: 142.35, v: 48_000_000 }], as_of: NOW, last_bar_at: NOW, exchange_timezone: "America/New_York" };
  if (pathname === "/api/stocks/NVDA/signals") return { signals: [] };
  if (pathname === "/api/ai/status") {
    return {
      enabled: true,
      status: "available",
      provider_capability_supported: true,
      sdk_capability_supported: true,
      methods: {},
      model: "gpt-5.6-terra",
      reasoning: "max",
      execution_mode: "background",
      background_poll_timeout_seconds: 1800,
    };
  }
  if (pathname === "/api/catalysts/status") {
    return {
      status: "active",
      enabled: true,
      remote_status: "ok",
      data_through: NOW,
      last_sync_at: NOW,
      model: "gpt-5.6-terra",
      reasoning: "max",
      analysis_trigger_enabled: true,
      analysis_availability: { enabled: true, reason: "available" },
      sources: [{ name: "Local Fixture", status: "active", last_success_at: NOW, consecutive_failures: 0 }],
    };
  }
  if (pathname === "/api/catalysts/feed") {
    return { status: "active", items: [catalystNews()], summary: { news_6h: 1, analyzed_24h: 0, bullish: 0, bearish: 0, pending: 1, high_impact_calendar: 0 } };
  }
  if (pathname === "/api/catalysts/news/password-e2e-news") return catalystNews();
  if (pathname === "/api/catalysts/hotspots/status") {
    return {
      status: "active",
      manual_enabled: true,
      analysis_availability: { enabled: true, reason: "available", budget_available: true, worker_healthy: true, concurrency_available: true },
      prepared_hot_count: 1,
      prepared_revision: 2,
      last_consumed_revision: 1,
      data_through: NOW,
    };
  }
  if (pathname === "/api/catalysts/hotspots") {
    return {
      status: "active",
      items: [{
        event_group_id: "password-e2e-hotspot",
        status: "prepared",
        representative_title: "本地密码模式验收热点",
        event_type: "company_update",
        source_count: 1,
        source_names: ["Local Fixture"],
        validated_tickers: ["NVDA"],
        hot_score: 70,
        hot_reasons: ["本地验收数据"],
        first_published_at: NOW,
        last_published_at: NOW,
      }],
    };
  }
  if (pathname === "/api/catalysts/market-focus-cycles/latest") return null;
  if (pathname === "/api/catalysts/analysis-progress") {
    return {
      status: "active",
      scope: "latest_submission_batch",
      batch_id: "aib_password_e2e",
      batch_source: "scheduled",
      total: 4,
      finished: 2,
      succeeded: 1,
      awaiting_validation: 0,
      rejected: 1,
      failed: 0,
      waiting: 1,
      in_progress: 1,
      cancelled: 0,
      insufficient_context: 0,
      budget_blocked: 0,
      progress_percent: 50,
      current_index: 3,
      current_news_id: 103,
      current_phase: "provider_processing",
      queue_total: 2,
      queue_waiting: 1,
      queue_in_progress: 1,
      started_at: NOW,
      last_updated_at: NOW,
      as_of: NOW,
    };
  }
  if (pathname === "/api/catalysts/calendar") return { status: "active", events: [] };
  if (pathname === "/api/runtime-settings") {
    // 契约形状保留（乐观锁 version 信封）；新 UI 当前无页面消费，仅作契约文档与兜底。
    return {
      version: 1,
      settings: {
        ai: {
          manual_analysis_enabled: true,
          daily_max_jobs: 4,
          daily_budget_usd: 2,
          daily_token_limit: 10000000,
          manual_analysis_cooldown_seconds: 30,
        },
        catalyst: {
          scheduled_analysis_enabled: true,
          scheduled_times_et: Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, "0")}:00`),
        },
      },
    };
  }
  if (pathname === "/api/runtime-settings/history") return { revisions: [] };
  if (pathname === "/api/worker/status") {
    return {
      healthy: true,
      tasks: ["focus_refresh", "strength_refresh", "breakout_refresh", "macro_conditions", "retention"].map(task_name => ({ task_name, enabled: true })),
    };
  }
  if (pathname === "/api/worker/actions") return { items: [] };
  return {};
}


async function installLocalDataFixtures(page) {
  await page.route("**/api/**", async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    // 访问域走真实后端：登录/登出/状态是本用例要验证的真实会话面
    if (pathname.startsWith("/api/access/")) {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(responseFor(pathname)),
    });
  });
}


// 访客壳断言：Navbar 出现「登录」链接，无「退出」按钮、无 AI 徽标
async function expectVisitorShell(page) {
  await expect(page.getByRole("link", { name: "登录" })).toBeVisible();
  await expect(page.getByRole("button", { name: "退出" })).toHaveCount(0);
  await expect(page.getByTitle("分析服务可用")).toHaveCount(0);
}


// Owner 壳断言：「退出」按钮 + AI 徽标出现，「登录」链接消失
async function expectOwnerShell(page) {
  await expect(page.getByRole("button", { name: "退出" })).toBeVisible();
  await expect(page.getByTitle("分析服务可用")).toBeVisible();
  await expect(page.getByRole("link", { name: "登录" })).toHaveCount(0);
}


async function screenshot(page, name) {
  await mkdir(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: join(SCREENSHOT_DIR, `${name}.png`) });
}


test("password mode keeps public research readable and reserves owner controls for the owner", async ({ page, context }) => {
  test.setTimeout(120_000);
  await installLocalDataFixtures(page);

  // ── 网关契约（真实后端直连断言）───────────────────────────────────────────
  // 匿名 GET /api/access/status → {access_mode:"password", logged_in:false}
  const anonStatus = await page.request.get(`${PASSWORD_BASE_URL}/api/access/status`);
  expect(anonStatus.status()).toBe(200);
  expect(await anonStatus.json()).toEqual({ access_mode: "password", logged_in: false });
  // SPA 无扩展名深层路径由网关回退 index.html（公共只读文档面）
  const deepDocument = await page.request.get(`${PASSWORD_BASE_URL}/catalysts`);
  expect(deepDocument.status()).toBe(200);
  expect(await deepDocument.text()).toContain('<div id="root">');

  // ── 匿名访问 / ────────────────────────────────────────────────────────────
  // 当前网关对口令模式匿名 HTML 采用「公共只读」策略：200 返回壳，SPA 首页重定向
  // /watchlist；若网关收紧为 303 → /login，本断言同样放行（两种到达态都接受）。
  await page.goto(`${PASSWORD_BASE_URL}/`, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/(watchlist|login)$/);
  if (new URL(page.url()).pathname === "/login") {
    // 303 分支：从登录页走「以访客身份浏览（只读）」回到公开研究面（Login.tsx L442-447）
    await page.getByRole("button", { name: "以访客身份浏览（只读）" }).click();
    await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/watchlist`);
  }

  // 访客可读研究数据（默认卡片视图显示代码与公司名）
  await expect(page.getByText("NVDA", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("英伟达", { exact: true }).first()).toBeVisible();
  await expectVisitorShell(page);
  // Owner 专属「强制刷新」对访客禁用（Watchlist.tsx L88-107）
  await expect(page.getByTitle("登录 Owner 后可强制刷新")).toBeDisabled();
  await screenshot(page, "password-visitor-watchlist");

  // 应用内导航到催化页（http 文档不再变化，走 BrowserRouter 客户端路由）
  await page.getByRole("link", { name: "06 催化" }).click();
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/catalysts`);
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("公开浏览可见的中文新闻标题").first()).toBeVisible();
  await expectVisitorShell(page);
  await screenshot(page, "password-visitor-catalysts");

  // ── 登录 ─────────────────────────────────────────────────────────────────
  await page.getByRole("link", { name: "登录" }).click();
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/login`);
  await expect(page.getByRole("heading", { name: "进入终端" })).toBeVisible();

  const password = page.getByLabel("访问密码");
  await expect(password).toHaveValue("");
  // 登录页（Layout 外）不得有任何浏览器存储痕迹
  expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });

  // 错误口令 → 真实后端 401 invalid_owner_password → mapError 兜底文案（Login.tsx L221/L432）
  const failedLogin = page.waitForResponse(response => (
    new URL(response.url()).pathname === "/api/access/login"
    && response.request().method() === "POST"
  ));
  await password.fill("wrong-password-for-e2e");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  expect((await failedLogin).status()).toBe(401);
  await expect(page.getByRole("alert")).toHaveText("密码不正确，请重试");
  await screenshot(page, "password-login-error");

  // 正确口令 → 200 + HttpOnly 会话 Cookie → SPA 回到 /watchlist
  const loginResponsePromise = page.waitForResponse(response => (
    new URL(response.url()).pathname === "/api/access/login"
    && response.request().method() === "POST"
    && response.status() === 200
  ));
  await password.fill(OWNER_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const loginResponse = await loginResponsePromise;
  const setCookie = await loginResponse.headerValue("set-cookie");
  expect(setCookie).toMatch(/optix_owner_session=/i);
  expect(setCookie).toMatch(/HttpOnly/i);
  expect(setCookie).toMatch(/Secure/i);
  expect(setCookie).toMatch(/SameSite=Strict/i);
  expect(setCookie).toMatch(/Path=\//i);
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/watchlist`);

  // 登录成功后不得残留延迟跳转：过去的 400ms 定时器会把随后打开的页面拉回 /watchlist。
  await page.getByRole("link", { name: "06 催化" }).click();
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/catalysts`);
  await page.waitForTimeout(500);
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/catalysts`);
  await page.getByRole("link", { name: "01 自选" }).click();
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/watchlist`);

  // 会话仅存于 HttpOnly Cookie：脚本不可见、口令不落任何浏览器存储
  const ownerCookie = (await context.cookies(PASSWORD_BASE_URL)).find(cookie => cookie.name === "optix_owner_session");
  expect(ownerCookie).toMatchObject({ httpOnly: true, secure: true, sameSite: "Strict", path: "/" });
  expect(await page.evaluate(() => document.cookie)).toBe("");
  // 注：SPA 壳允许把非敏感 UI 状态写入 localStorage（如 CommandPalette 最近代码，
  // CommandPalette.tsx L35-37），因此这里只断言「口令绝不落存储」，不再要求存储为空。
  expect(await page.evaluate(testPassword => (
    [...Object.values(localStorage), ...Object.values(sessionStorage)].some(value => String(value).includes(testPassword))
  ), OWNER_PASSWORD)).toBe(false);

  const ownerStatus = await page.request.get(`${PASSWORD_BASE_URL}/api/access/status`);
  expect(await ownerStatus.json()).toEqual({ access_mode: "password", logged_in: true });

  // ── Owner 壳与 owner 专属控件 ─────────────────────────────────────────────
  await expectOwnerShell(page);
  await expect(page.getByTitle("强制刷新自选快照")).toBeEnabled();
  await screenshot(page, "password-owner-watchlist");

  await page.getByRole("link", { name: "06 催化" }).click();
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/catalysts`);
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("公开浏览可见的中文新闻标题").first()).toBeVisible();
  await expectOwnerShell(page);
  const analysisProgress = page.getByLabel("新闻分析进度");
  await expect(analysisProgress).toContainText("正在处理第 3 / 4 条");
  await expect(analysisProgress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  await page.setViewportSize({ width: 320, height: 568 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
  // 分析进度每五秒会换成最新服务端快照。直接执行同步 DOM 滚动，
  // 避免 Playwright 等待元素稳定时恰逢快照刷新、旧节点被替换。
  await analysisProgress.evaluate(element => element.scrollIntoView({ block: "center" }));
  await expect(page.getByLabel("新闻分析进度")).toBeVisible();
  await screenshot(page, "password-owner-analysis-progress-mobile");
  await page.setViewportSize({ width: 1280, height: 720 });
  await screenshot(page, "password-owner-catalysts");

  // ── 登出 → 回到访客 ──────────────────────────────────────────────────────
  const logoutResponsePromise = page.waitForResponse(response => (
    new URL(response.url()).pathname === "/api/access/logout"
    && response.request().method() === "POST"
  ));
  await page.getByRole("button", { name: "退出" }).click();
  expect((await logoutResponsePromise).status()).toBe(200);
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/watchlist`);
  expect((await context.cookies(PASSWORD_BASE_URL)).some(cookie => cookie.name === "optix_owner_session")).toBe(false);
  await expectVisitorShell(page);
  await expect(page.getByText("英伟达", { exact: true }).first()).toBeVisible();

  // 登出后公共研究面仍可读
  await page.getByRole("link", { name: "06 催化" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("公开浏览可见的中文新闻标题").first()).toBeVisible();
  await expectVisitorShell(page);
  await screenshot(page, "password-logged-out-catalysts");
});
