import { expect, test } from "@playwright/test";


const PASSWORD_BASE_URL = process.env.OPTIX_PASSWORD_BASE_URL || "https://127.0.0.1:8768";
const OWNER_PASSWORD = "optix-browser-test-password-2026";
const NOW = "2026-07-16T12:00:00Z";
let runtimeVersion;
let manualAnalysisEnabled;
let scheduledAnalysisEnabled;


function resetRuntimeFixture() {
  runtimeVersion = 1;
  manualAnalysisEnabled = true;
  scheduledAnalysisEnabled = true;
}


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
        stocks: [{ ticker: "NVDA", name: "英伟达", price: 142.35, change: 1.2, change_percent: 0.85, spark: [140, 141, 142.35] }],
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
  if (pathname === "/api/stocks/NVDA") return { ticker: "NVDA", price: 142.35, change_percent: 0.85, volume: 48_000_000, market_cap: 3_500_000_000_000 };
  if (pathname === "/api/stocks/NVDA/chart") return { bars: [{ t: NOW, o: 140, h: 143, l: 139, c: 142.35, v: 48_000_000 }], as_of: NOW, last_bar_at: NOW, exchange_timezone: "America/New_York" };
  if (pathname === "/api/stocks/NVDA/signals") return { score: 68, overall: "bullish", signals: { rsi: { value: 58 } } };
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
  if (pathname === "/api/catalysts/calendar") return { status: "active", events: [] };
  if (pathname === "/api/runtime-settings") {
    return {
      version: runtimeVersion,
      settings: {
        ai: {
          manual_analysis_enabled: manualAnalysisEnabled,
          daily_max_jobs: 4,
          daily_budget_usd: 2,
          daily_token_limit: 10000000,
          manual_analysis_cooldown_seconds: 30,
        },
        catalyst: {
          scheduled_analysis_enabled: scheduledAnalysisEnabled,
          scheduled_times_et: Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, "0")}:00`),
        },
      },
    };
  }
  if (pathname === "/api/runtime-settings/history") return { revisions: [] };
  if (pathname === "/api/worker/status") {
    return {
      healthy: true,
      tasks: ["focus_refresh", "strength_refresh", "breakout_refresh", "retention"].map(task_name => ({ task_name, enabled: true })),
    };
  }
  if (pathname === "/api/worker/actions") return { items: [] };
  return {};
}


async function installLocalDataFixtures(page) {
  await page.route("**/api/**", async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/api/access/")) {
      await route.continue();
      return;
    }
    if (pathname === "/api/runtime-settings" && request.method() === "PUT") {
      const update = request.postDataJSON();
      const settings = update && update.settings || {};
      if (settings.ai && typeof settings.ai.manual_analysis_enabled === "boolean") {
        manualAnalysisEnabled = settings.ai.manual_analysis_enabled;
      }
      if (settings.catalyst && typeof settings.catalyst.scheduled_analysis_enabled === "boolean") {
        scheduledAnalysisEnabled = settings.catalyst.scheduled_analysis_enabled;
      }
      runtimeVersion += 1;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(responseFor(pathname)),
    });
  });
}


async function expectGuestShell(page) {
  await expect(page.locator("#owner-login")).toBeVisible();
  await expect(page.locator("#owner-ai-toggle")).toBeHidden();
  await expect(page.locator("#owner-logout")).toBeHidden();
}


async function expectGuestCatalystControls(page) {
  await expect(page.locator("#cat-runtime-settings")).toBeHidden();
  await expect(page.locator("#cat-owner-operations")).toBeHidden();
  await expect(page.locator('[data-cat-refresh="news"]')).toBeHidden();
  await expect(page.locator('[data-cat-refresh="calendar"]')).toBeHidden();
  await expect(page.locator('[data-cat-refresh="source_health"]')).toBeHidden();
  await expect(page.locator("[data-worker-action]").first()).toBeHidden();
  await expect(page.locator("[data-catalyst-analyze]")).toHaveCount(0);
  await expect(page.locator("#cat-focus-run")).toHaveCount(0);
  await expect(page.locator("#cat-analysis-run")).toHaveCount(0);
  await expect(page.locator("#cat-analysis-cancel")).toHaveCount(0);
}


test("password mode keeps public research readable and reserves analysis controls for the owner", async ({ page, context }) => {
  resetRuntimeFixture();
  await installLocalDataFixtures(page);
  await page.goto(`${PASSWORD_BASE_URL}/`, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/`);
  await expect(page.getByText("本地验收", { exact: true })).toBeVisible();
  await expect(page.getByText("NVDA", { exact: true }).first()).toBeVisible();
  await expectGuestShell(page);

  await page.goto(`${PASSWORD_BASE_URL}/#catalysts`, { waitUntil: "networkidle" });
  await expect(page.getByText("公开浏览可见的中文新闻标题", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看分析 →" })).toBeVisible();
  await expectGuestShell(page);
  await expectGuestCatalystControls(page);

  await page.locator("#owner-login").click();
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/login.html`);

  const password = page.locator("#owner-password");
  await expect(password).toHaveValue("");
  expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });

  const loginResponsePromise = page.waitForResponse(response => (
    new URL(response.url()).pathname === "/api/access/login"
    && response.request().method() === "POST"
  ));
  await password.fill(OWNER_PASSWORD);
  await page.locator("#owner-login-submit").click();
  const loginResponse = await loginResponsePromise;
  expect(loginResponse.status()).toBe(200);
  const setCookie = await loginResponse.headerValue("set-cookie");
  expect(setCookie).toMatch(/optix_owner_session=/i);
  expect(setCookie).toMatch(/HttpOnly/i);
  expect(setCookie).toMatch(/Secure/i);
  expect(setCookie).toMatch(/SameSite=Strict/i);
  expect(setCookie).toMatch(/Path=\//i);
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/`);

  const ownerCookie = (await context.cookies(PASSWORD_BASE_URL)).find(cookie => cookie.name === "optix_owner_session");
  expect(ownerCookie).toMatchObject({ httpOnly: true, secure: true, sameSite: "Strict", path: "/" });
  expect(await page.evaluate(() => document.cookie)).toBe("");
  expect(await page.evaluate(testPassword => ({
    local: JSON.stringify(localStorage),
    session: JSON.stringify(sessionStorage),
    leaked: [...Object.values(localStorage), ...Object.values(sessionStorage)].some(value => String(value).includes(testPassword)),
  }), OWNER_PASSWORD)).toEqual({ local: "{}", session: "{}", leaked: false });

  await page.goto(`${PASSWORD_BASE_URL}/#catalysts`, { waitUntil: "networkidle" });
  await expect(page.locator("#owner-login")).toBeHidden();
  await expect(page.locator("#owner-logout")).toBeVisible();
  await expect(page.locator("#owner-ai-toggle")).toBeVisible();
  await expect(page.locator("#owner-ai-toggle")).toBeEnabled();
  await expect(page.locator("#owner-ai-toggle")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#owner-ai-toggle")).toHaveText("分析：开启");
  await expect(page.locator("#cat-runtime-settings")).toBeVisible();
  await expect(page.locator("#cat-owner-operations")).toBeVisible();
  await expect(page.locator('[data-cat-refresh="news"]')).toBeVisible();
  await expect(page.locator('[data-cat-refresh="calendar"]')).toBeVisible();
  await expect(page.locator('[data-cat-refresh="source_health"]')).toBeVisible();
  await expect(page.locator("[data-catalyst-analyze]")).toBeVisible();
  await expect(page.locator("#cat-focus-run")).toBeVisible();
  await expect(page.locator("#cat-focus-run")).toBeEnabled();

  const disableRequest = page.waitForRequest(request => (
    new URL(request.url()).pathname === "/api/runtime-settings"
    && request.method() === "PUT"
  ));
  await page.locator("#owner-ai-toggle").click();
  expect((await disableRequest).postDataJSON()).toMatchObject({
    expected_version: 1,
    settings: {
      ai: { manual_analysis_enabled: false },
      catalyst: { scheduled_analysis_enabled: false },
    },
  });
  await expect(page.locator("#owner-ai-toggle")).toHaveText("分析：关闭");
  await expect(page.locator("#owner-ai-toggle")).toHaveAttribute("aria-pressed", "false");

  const enableRequest = page.waitForRequest(request => (
    new URL(request.url()).pathname === "/api/runtime-settings"
    && request.method() === "PUT"
  ));
  await page.locator("#owner-ai-toggle").click();
  expect((await enableRequest).postDataJSON()).toEqual({
    expected_version: 2,
    settings: {
      ai: { manual_analysis_enabled: true },
      catalyst: {
        scheduled_analysis_enabled: true,
        scheduled_times_et: Array.from(
          { length: 24 },
          (_, hour) => `${String(hour).padStart(2, "0")}:00`,
        ),
      },
    },
  });
  await expect(page.locator("#owner-ai-toggle")).toHaveText("分析：开启");
  await expect(page.locator("#owner-ai-toggle")).toHaveAttribute("aria-pressed", "true");

  const logoutResponsePromise = page.waitForResponse(response => (
    new URL(response.url()).pathname === "/api/access/logout"
    && response.request().method() === "POST"
  ));
  await page.locator("#owner-logout").click();
  expect((await logoutResponsePromise).status()).toBe(200);
  await expect(page).toHaveURL(`${PASSWORD_BASE_URL}/`);
  expect((await context.cookies(PASSWORD_BASE_URL)).some(cookie => cookie.name === "optix_owner_session")).toBe(false);
  await expect(page.getByText("本地验收", { exact: true })).toBeVisible();
  await expectGuestShell(page);

  await page.goto(`${PASSWORD_BASE_URL}/#catalysts`, { waitUntil: "networkidle" });
  await expect(page.getByText("公开浏览可见的中文新闻标题", { exact: true })).toBeVisible();
  await expectGuestShell(page);
  await expectGuestCatalystControls(page);
});
