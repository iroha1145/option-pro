import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const NOW = "2026-07-13T14:30:00Z";
const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");
const SECRET_RESPONSE_PATTERN = /(?:sk-proj-[A-Za-z0-9_-]{20,}|OPENAI_API_KEY|FINNHUB_API_KEY|INTERNAL_API_TOKEN|APP_PASSWORD_HASH|APP_AUTH_TOKEN|MACROLENS_INTERNAL_TOKEN|authorization["']?\s*:\s*["']?bearer)/i;

const SCENARIOS = [
  { name: "1440x900-dark-active", width: 1440, height: 900, theme: "dark", state: "active", authenticated: true },
  { name: "1280x800-light-public-visitor", width: 1280, height: 800, theme: "light", state: "active", authenticated: false },
  { name: "1024x768-dark-active", width: 1024, height: 768, theme: "dark", state: "active", authenticated: true },
  { name: "390x844-light-stale-menu-closed", width: 390, height: 844, theme: "light", state: "stale", mobile: true, authenticated: true },
  { name: "1280x800-dark-empty", width: 1280, height: 800, theme: "dark", state: "empty", authenticated: true },
  { name: "1280x800-dark-degraded", width: 1280, height: 800, theme: "dark", state: "degraded", authenticated: true },
  { name: "1280x800-dark-focus-fallback", width: 1280, height: 800, theme: "dark", state: "focus_fallback", authenticated: true },
  { name: "1280x800-dark-unavailable", width: 1280, height: 800, theme: "dark", state: "unavailable", authenticated: true },
  { name: "1280x800-light-disabled", width: 1280, height: 800, theme: "light", state: "disabled", authenticated: true },
  { name: "1280x800-light-read-only-analysis", width: 1280, height: 800, theme: "light", state: "read_only", authenticated: true },
  { name: "390x844-light-read-only-analysis", width: 390, height: 844, theme: "light", state: "read_only", mobile: true, authenticated: true },
  { name: "1280x800-dark-manual-analysis", width: 1280, height: 800, theme: "dark", state: "manual_analysis", authenticated: true },
  { name: "1280x800-dark-prepared", width: 1280, height: 800, theme: "dark", state: "prepared", authenticated: true },
  { name: "1280x800-dark-queued", width: 1280, height: 800, theme: "dark", state: "queued", authenticated: true },
  { name: "1280x800-dark-in-progress", width: 1280, height: 800, theme: "dark", state: "in_progress", authenticated: true },
  { name: "1280x800-dark-completed", width: 1280, height: 800, theme: "dark", state: "completed", authenticated: true },
  { name: "1280x800-dark-failed", width: 1280, height: 800, theme: "dark", state: "failed", authenticated: true },
  { name: "1280x800-dark-incomplete-output", width: 1280, height: 800, theme: "dark", state: "incomplete_output", authenticated: true },
  { name: "1280x800-light-budget-blocked", width: 1280, height: 800, theme: "light", state: "budget_blocked", authenticated: true },
];

function newsItem(state) {
  const readOnly = state === "read_only";
  const unrequested = ["analysis_unrequested", "manual_analysis", "read_only"].includes(state);
  const manualAnalysis = state === "manual_analysis";
  const failed = state === "failed";
  return {
    news_id: "visual-news-1",
    source: "Reuters",
    title: "大型科技公司上调数据中心资本开支预期，供应链订单与交付节奏出现新的可验证变化",
    summary: "这是一条用于发布验收的有界本地记录，用来检查长标题换行、状态标签和信息层级。",
    published_at: "2026-07-13T13:40:00Z",
    fetched_at: "2026-07-13T13:42:00Z",
    analysis_status: failed ? "failed" : readOnly ? "queued" : unrequested ? "not_requested" : "completed",
    analysis_trigger_enabled: manualAnalysis,
    analysis_availability: readOnly ? { enabled: false, reason: "read_only_mode" } : undefined,
    analysis_job: readOnly ? {
      job_id: "aij-visual-read-only",
      job_type: "news_impact",
      status: "queued",
      model: "gpt-5.6-terra",
      reasoning: "max",
      submission_source: "manual",
      created_at: NOW,
      updated_at: NOW,
    } : undefined,
    trusted_stock_impacts: failed || unrequested ? [] : [
      { ticker: "NVDA", impact_score: 42, confidence: 76, horizon: "days", mechanism: "direct_company", validation_status: "canonical", reason: "订单能见度改善，但估值与交付节奏仍是主要风险。" },
    ],
    analysis: failed || unrequested ? null : {
      model: "gpt-5.6-terra",
      reasoning: "max",
      classification: "bullish",
      confidence: 76,
      market_relevance: 88,
      overall_sentiment: 31,
      affected_stocks: [
        { ticker: "NVDA", impact_score: 42, mechanism: "direct_company", reason: "订单能见度改善，但估值与交付节奏仍是主要风险。" },
      ],
      stock_validations: [
        { ticker: "NVDA", validation_status: "canonical", validated_at: "2026-07-13T13:44:00Z", focus_revision: 9, universe_version: "visual-v1", association_method: "llm_inference" },
      ],
    },
  };
}

function hotspot() {
  return {
    event_group_id: "visual-event-1",
    status: "prepared",
    representative_title: "数据中心资本开支预期上调并获得多来源确认",
    event_type: "company_update",
    source_count: 2,
    source_names: ["Reuters", "Bloomberg"],
    validated_tickers: ["NVDA", "AVGO"],
    hot_score: 82,
    hot_reasons: ["两个独立出版来源", "市场确认有效"],
    component_scores: { market_confirmation: 74 },
    first_published_at: "2026-07-13T13:30:00Z",
    last_published_at: "2026-07-13T13:50:00Z",
  };
}

function cycleFor(state) {
  if (!["queued", "in_progress", "completed", "failed", "incomplete_output"].includes(state)) return null;
  const cycle = {
    cycle_id: "visual-cycle-1",
    job_id: "visual-cycle-1",
    status: state,
    model: "gpt-5.6-terra",
    reasoning: "max",
    snapshot_as_of: NOW,
    created_at: "2026-07-13T14:00:00Z",
    updated_at: NOW,
  };
  if (["failed", "incomplete_output"].includes(state)) {
    cycle.error_code = state === "failed" ? "invalid_structured_output" : "max_output_tokens_reached";
    return cycle;
  }
  if (state === "completed") {
    cycle.completed_at = NOW;
    cycle.result = {
      as_of: NOW,
      market_summary: "多来源证据支持数据中心需求仍强，但冲突证据降低了方向可靠度。",
      dominant_events: [{ event_group_id: "visual-event-1", summary: "资本开支预期上调" }],
      affected_sectors: ["半导体", "云计算"],
      market_uncertainties: ["估值较高", "交付节奏可能延后"],
      focus_ticker_assessments: [{
        ticker: "NVDA",
        confidence: 76,
        summary: "支持证据占优，冲突证据已作为可靠度折减处理。",
        supporting_event_ids: ["visual-event-1"],
        conflicting_event_ids: ["visual-event-2"],
        risks: ["估值", "供应约束"],
        supporting_weight: 1,
        conflicting_weight: 0.25,
        conflict_ratio: 0.2,
        effective_reliability: 0.8,
        weighted_catalyst_context: 25.5,
      }],
    };
  }
  return cycle;
}

function jsonFor(pathname, state) {
  const publicState = ["active", "empty", "degraded", "stale", "unavailable", "disabled"].includes(state) ? state : "active";
  const focusFallback = state === "focus_fallback";
  if (pathname === "/api/market/indices") {
    return {
      indices: [
        { symbol: "^GSPC", price: 5750.21, change_pct: 0.42 },
        { symbol: "^IXIC", price: 19640.3, change_pct: 0.66 },
      ],
      as_of: NOW,
      source_status: "active",
      data_limited: false,
    };
  }
  if (pathname === "/api/market/status") {
    return { market: "closed", phase: "weekend", is_open: false, exchange_timezone: "America/New_York", as_of: NOW };
  }
  if (pathname === "/api/catalysts/status") {
    return {
      status: publicState,
      enabled: publicState !== "disabled",
      remote_status: publicState === "active" || publicState === "empty" ? "ok" : publicState,
      schema_version: "macrolens-option-pro-v2",
      data_through: NOW,
      last_sync_at: NOW,
      model: "gpt-5.6-terra",
      reasoning: "max",
      analysis_trigger_enabled: state === "manual_analysis",
      analysis_availability: state === "read_only" ? { enabled: false, reason: "read_only_mode" } : undefined,
      warnings: focusFallback
        ? ["焦点行情使用最近一次可靠日线快照，未把缺失盘中数据补成中性值。"]
        : publicState === "degraded" ? ["一个来源暂时不可用，已有本地快照继续展示。"] : [],
      sources: focusFallback ? [
        { name: "Focus Context", status: "fallback", last_success_at: NOW, consecutive_failures: 1 },
      ] : [
        { name: "Finnhub", status: publicState === "degraded" ? "degraded" : "active", last_success_at: NOW, consecutive_failures: publicState === "degraded" ? 1 : 0 },
        { name: "Massive", status: "active", last_success_at: NOW, consecutive_failures: 0 },
      ],
    };
  }
  if (pathname === "/api/catalysts/feed") {
    return {
      status: publicState,
      items: ["empty", "unavailable", "disabled"].includes(publicState) ? [] : [newsItem(state)],
      summary: { news_6h: ["empty", "unavailable", "disabled"].includes(publicState) ? 0 : 11, analyzed_24h: 7, bullish: 3, bearish: 2, pending: 1, high_impact_calendar: 2 },
    };
  }
  if (pathname.startsWith("/api/catalysts/news/")) {
    return newsItem(state);
  }
  if (pathname === "/api/catalysts/analysis-jobs/aij-visual-manual") {
    return {
      job_id: "aij-visual-manual",
      job_type: "news_impact",
      status: "queued",
      model: "gpt-5.6-terra",
      reasoning: "max",
      submission_source: "manual",
      created_at: NOW,
      updated_at: NOW,
    };
  }
  if (pathname === "/api/catalysts/analysis-jobs/aij-visual-read-only") {
    return {
      job_id: "aij-visual-read-only",
      job_type: "news_impact",
      status: "queued",
      model: "gpt-5.6-terra",
      reasoning: "max",
      submission_source: "manual",
      created_at: NOW,
      updated_at: NOW,
    };
  }
  if (pathname === "/api/catalysts/hotspots/status") {
    const noHotspots = ["empty", "unavailable", "disabled"].includes(publicState);
    const disabled = publicState === "disabled";
    const readOnly = state === "read_only";
    const budgetBlocked = state === "budget_blocked";
    return {
      status: publicState,
      manual_enabled: !disabled && !readOnly,
      analysis_availability: budgetBlocked
        ? { enabled: false, reason: "budget_exhausted", budget_available: false }
        : disabled
          ? { enabled: false, reason: "catalyst_disabled" }
          : readOnly
            ? { enabled: false, reason: "read_only_mode" }
          : { enabled: true, reason: "available", budget_available: true, worker_healthy: true, concurrency_available: true },
      prepared_hot_count: noHotspots ? 0 : 1,
      prepared_revision: noHotspots ? 7 : 8,
      last_consumed_revision: 7,
      data_through: NOW,
      last_cycle_at: "2026-07-13T12:00:00Z",
      next_scheduled_at: "2026-07-13T16:00:00Z",
    };
  }
  if (pathname === "/api/catalysts/hotspots") {
    return { status: publicState, items: ["empty", "unavailable", "disabled"].includes(publicState) ? [] : [hotspot()] };
  }
  if (pathname === "/api/catalysts/market-focus-cycles/latest") return cycleFor(state);
  if (pathname === "/api/catalysts/calendar") return { status: publicState, events: [] };
  return {};
}

async function installApiFixtures(page, state, mutationRequests, authenticated = false) {
  await page.route("**/api/**", async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== "GET") {
      if (
        state === "manual_analysis"
        && request.method() === "POST"
        && pathname === "/api/catalysts/news/visual-news-1/analysis"
      ) {
        mutationRequests.push({
          method: request.method(),
          pathname,
          headers: await request.allHeaders(),
          body: request.postDataJSON(),
        });
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "aij-visual-manual",
            job_type: "news_impact",
            status: "queued",
            model: "gpt-5.6-terra",
            reasoning: "max",
            submission_source: "manual",
            created_at: NOW,
            updated_at: NOW,
          }),
        });
        return;
      }
      await route.fulfill({ status: 405, contentType: "application/json", body: JSON.stringify({ detail: "visual_fixture_read_only" }) });
      return;
    }
    if (pathname === "/api/access/status") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ access_mode: "password", logged_in: authenticated }),
      });
      return;
    }
    const body = jsonFor(pathname, state);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

async function installCatalystFailureFixtures(page) {
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith("/api/catalysts/")) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "catalyst_unavailable", message: "Catalyst fixture unavailable" }),
      });
      return;
    }
    let body = jsonFor(pathname, "active");
    if (pathname === "/api/stocks/watchlist") {
      body = {
        groups: [{
          id: "core",
          name: "核心观察",
          stocks: [{
            ticker: "NVDA",
            name: "英伟达",
            price: 142.35,
            change: 1.2,
            change_percent: 0.85,
            spark: [138, 139, 141, 142.35],
            quote_as_of: NOW,
            quote_session: "regular",
          }],
        }],
        as_of: NOW,
        data_through: NOW,
        oldest_quote_at: NOW,
        latest_quote_at: NOW,
        quote_interval: "5m",
        source_status: "active",
        attempted: 1,
        succeeded: 1,
        failed: 0,
        failed_tickers: [],
        delayed: 0,
        delayed_tickers: [],
      };
    } else if (pathname === "/api/earnings/upcoming") {
      body = { earnings: [] };
    } else if (pathname === "/api/options/unusual") {
      body = { results: [], attempted: 0, as_of: NOW };
    } else if (pathname === "/api/stocks/NVDA") {
      body = { ticker: "NVDA", price: 142.35, change_percent: 0.85, volume: 48_000_000, market_cap: 3_500_000_000_000 };
    } else if (pathname === "/api/stocks/NVDA/chart") {
      body = { bars: [{ t: NOW, o: 140, h: 143, l: 139, c: 142.35, v: 48_000_000 }], as_of: NOW, last_bar_at: NOW, exchange_timezone: "America/New_York" };
    } else if (pathname === "/api/stocks/NVDA/signals") {
      body = { score: 68, overall: "bullish", signals: { rsi: { value: 58 } } };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

function installSecretResponseScanner(page, pendingScans, leaks) {
  page.on("response", response => {
    const scan = (async () => {
      const resource = new URL(response.url()).pathname;
      const headers = JSON.stringify(response.headers());
      if (SECRET_RESPONSE_PATTERN.test(headers)) leaks.push({ resource, surface: "headers" });
      const contentType = response.headers()["content-type"] || "";
      if (!/(?:json|javascript|text|css|html)/i.test(contentType)) return;
      const body = await response.text();
      if (SECRET_RESPONSE_PATTERN.test(body)) leaks.push({ resource, surface: "body" });
    })().catch(() => {
      leaks.push({ resource: "unreadable-response", surface: "scan" });
    });
    pendingScans.push(scan);
  });
}

async function assertStableViewport(page, errors) {
  await expect(page.locator("#cat-connection")).not.toHaveText("读取中");
  await expect(page.locator(".cat-desk")).toBeVisible();
  const viewport = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    overflowingElements: [...document.querySelectorAll("body *")]
      .map(element => {
        const rect = element.getBoundingClientRect();
        return {
          selector: element.id ? `#${element.id}` : element.classList.length
            ? `${element.tagName.toLowerCase()}.${[...element.classList].join(".")}`
            : element.tagName.toLowerCase(),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      })
      .filter(({ left, right }) => left < -1 || right > window.innerWidth + 1)
      .slice(0, 12),
  }));
  expect(
    viewport.scrollWidth,
    `the document must not overflow the viewport horizontally: ${JSON.stringify(viewport.overflowingElements)}`,
  ).toBeLessThanOrEqual(viewport.innerWidth + 1);
  const browserState = await page.evaluate(() => ({
    localKeys: Object.keys(localStorage).sort(),
    sessionKeys: Object.keys(sessionStorage).sort(),
    cookie: document.cookie,
    htmlContainsProjectKey: /sk-proj-[A-Za-z0-9_-]{20,}/.test(document.documentElement.innerHTML),
  }));
  expect(browserState).toEqual({
    localKeys: ["optix.theme"],
    sessionKeys: [],
    cookie: "",
    htmlContainsProjectKey: false,
  });
  expect(errors).toEqual([]);
}

for (const scenario of SCENARIOS) {
  test(`Catalyst Desk visual evidence: ${scenario.name}`, async ({ page }) => {
    const errors = [];
    const responseScans = [];
    const responseLeaks = [];
    const mutationRequests = [];
    page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
    page.on("pageerror", error => errors.push(error.message));
    installSecretResponseScanner(page, responseScans, responseLeaks);
    await page.setViewportSize({ width: scenario.width, height: scenario.height });
    await page.addInitScript(theme => localStorage.setItem("optix.theme", theme), scenario.theme);
    await installApiFixtures(page, scenario.state, mutationRequests, scenario.authenticated);
    await page.goto("/#catalysts", { waitUntil: "networkidle" });
    await assertStableViewport(page, errors);

    if (scenario.authenticated) {
      await expect(page.locator("#owner-login")).toBeHidden();
      await expect(page.locator("#owner-ai-toggle")).toBeVisible();
      await expect(page.locator("#owner-logout")).toBeVisible();
      await expect(page.locator("#cat-runtime-settings")).toBeVisible();
      await expect(page.locator("#cat-owner-operations")).toBeVisible();
    } else {
      await expect(page.locator("#owner-login")).toBeVisible();
      await expect(page.locator("#owner-ai-toggle")).toBeHidden();
      await expect(page.locator("#owner-logout")).toBeHidden();
      await expect(page.locator("[data-catalyst-news]").first()).toBeVisible();
      await expect(page.locator("#cat-runtime-settings")).toBeHidden();
      await expect(page.locator("#cat-owner-operations")).toBeHidden();
      await expect(page.locator("[data-worker-action]").first()).toBeHidden();
      await expect(page.locator("[data-catalyst-analyze]")).toHaveCount(0);
      await expect(page.locator("#cat-focus-run")).toHaveCount(0);
    }

    if (scenario.authenticated && ["stale", "unavailable"].includes(scenario.state)) {
      const focusAction = page.locator("#cat-focus-run");
      await expect(focusAction).toBeDisabled();
      await expect(focusAction).toHaveText("热点快照暂不可用");
    }
    if (scenario.state === "disabled") {
      await expect(page.locator("#cat-focus-run")).toHaveCount(0);
      await expect(page.locator("#cat-focus-body")).toContainText("分析功能未启用");
    }
    if (scenario.state === "budget_blocked") {
      await expect(page.locator("#cat-focus-run")).toBeDisabled();
      await expect(page.locator("#cat-focus-run")).toHaveText("今日预算已用完");
    }
    if (scenario.state === "prepared") {
      await expect(page.locator("#cat-focus-run")).toBeEnabled();
      await expect(page.locator("#cat-focus-run")).toContainText("重新分析");
    }
    for (const operationType of ["news", "calendar", "source_health"]) {
      const refresh = page.locator(`[data-cat-refresh="${operationType}"]`);
      await expect(refresh).toHaveCount(1);
      if (scenario.authenticated) await expect(refresh).toBeVisible();
      else await expect(refresh).toBeHidden();
    }
    const expectedFocusActions = scenario.authenticated
      && !["disabled", "read_only"].includes(scenario.state)
      ? 1
      : 0;
    await expect(page.locator("#cat-focus-run")).toHaveCount(expectedFocusActions);
    if (scenario.state === "read_only") {
      await expect(page.locator("#cat-focus-body")).toContainText("当前为只读模式");
      await expect(page.locator("[data-catalyst-analyze]")).toHaveCount(0);
      await page.locator("[data-catalyst-news]").first().click();
      await expect(page.locator("#cat-analysis-body")).toContainText("当前为只读模式");
      await expect(page.locator("#cat-analysis-body [data-cat-analyze]")).toHaveCount(0);
      const cancelJob = page.locator("#cat-analysis-body [data-cat-cancel-job]");
      if (scenario.authenticated) await expect(cancelJob).toBeVisible();
      else await expect(cancelJob).toHaveCount(0);
    }
    if (scenario.state === "manual_analysis") {
      await page.locator("[data-catalyst-news]").first().click();
      const analyze = page.locator("#cat-analysis-body [data-cat-analyze]");
      await expect(analyze).toBeVisible();
      await expect(analyze).toBeEnabled();
      await expect(analyze).toHaveText("生成分析");
      const confirmations = [];
      page.once("dialog", async dialog => {
        confirmations.push(dialog.message());
        await dialog.accept();
      });
      await analyze.click();
      await expect.poll(() => mutationRequests.length).toBe(1);
      expect(confirmations).toHaveLength(1);
      expect(confirmations[0]).toContain("可能产生模型费用");
      expect(mutationRequests[0].method).toBe("POST");
      expect(mutationRequests[0].pathname).toBe("/api/catalysts/news/visual-news-1/analysis");
      expect(mutationRequests[0].body).toEqual({ force: false });
      expect(mutationRequests[0].headers["content-type"]).toContain("application/json");
      expect(mutationRequests[0].headers["x-optix-action"]).toBe("1");
      await expect(page.locator("#cat-analysis-body")).toContainText("分析任务正在运行");
    }
    if (scenario.state === "failed") {
      const focusBody = page.locator("#cat-focus-body");
      await expect(focusBody).toContainText("分析任务暂未完成，请稍后重试");
      await expect(focusBody).not.toContainText("invalid_structured_output");
    }
    if (scenario.state === "focus_fallback") {
      await expect(page.getByText("兜底源", { exact: true })).toBeVisible();
      await expect(page.locator("#cat-model-note")).toContainText(
        "焦点行情使用最近一次可靠日线快照",
      );
    }

    if (scenario.mobile) {
      const nav = page.locator(".deck-nav");
      await expect(nav).not.toHaveClass(/open/);
      expect(await nav.evaluate(element => getComputedStyle(element).visibility)).toBe("hidden");
      expect(await nav.evaluate(element => element.getBoundingClientRect().bottom <= 0)).toBe(true);
      for (let step = 0; step < 14; step += 1) {
        await page.keyboard.press("Tab");
        expect(await page.evaluate(() => document.activeElement && document.activeElement.closest(".deck-nav") !== null)).toBe(false);
      }
    }

    await mkdir(SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({
      path: join(SCREENSHOT_DIR, `${scenario.name}.png`),
      animations: "disabled",
      fullPage: false,
    });
    await page.waitForLoadState("networkidle");
    await Promise.all(responseScans);
    expect(responseLeaks).toEqual([]);
  });
}

test("research drawer traps keyboard focus and restores the opener", async ({ page }) => {
  const mutationRequests = [];
  await installApiFixtures(page, "active", mutationRequests);
  await page.goto("/#catalysts", { waitUntil: "networkidle" });
  const opener = page.locator("[data-catalyst-news]").first();
  await opener.click();
  const drawer = page.locator("#drawer");
  const close = page.locator("#drawer-close");
  await expect(drawer).toBeVisible();
  await expect(close).toBeFocused();

  const lastFocusable = drawer.locator("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])").last();
  await lastFocusable.focus();
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(lastFocusable).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(opener).toBeFocused();
});

test("server task state survives a full page reload without creating another task", async ({ page }) => {
  const mutationRequests = [];
  const latestRequests = [];
  page.on("request", request => {
    if (new URL(request.url()).pathname === "/api/catalysts/market-focus-cycles/latest") latestRequests.push(request.method());
  });
  await installApiFixtures(page, "queued", mutationRequests);
  await page.goto("/#catalysts", { waitUntil: "networkidle" });
  await expect(page.locator("#cat-focus-body")).toContainText("分析任务正在运行");
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.locator("#cat-focus-body")).toContainText("分析任务正在运行");
  expect(latestRequests).toEqual(["GET", "GET"]);
  expect(mutationRequests).toEqual([]);
});

test("Catalyst outage stays inside Catalyst Desk", async ({ page }) => {
  await installCatalystFailureFixtures(page);
  await page.goto("/#catalysts", { waitUntil: "networkidle" });
  await expect(page.locator("#cat-read-state")).toContainText(
    /新闻读取失败|催化剂数据暂不可用/,
  );

  await page.locator('.deck-nav a[data-route="watchlist"]').click();
  await expect(page.locator(".view-head h1")).toContainText("自选观察");
  await expect(page.locator('[data-card="NVDA"]')).toBeVisible();
  await page.locator('[data-card="NVDA"]').click();
  await expect(page.locator('[data-card="NVDA"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#cat-read-state")).toHaveCount(0);
});
