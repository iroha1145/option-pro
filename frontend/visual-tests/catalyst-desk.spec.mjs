import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const NOW = "2026-07-13T14:30:00Z";
const SCREENSHOT_DIR = join(process.cwd(), "test-results", "visual-evidence");

const SCENARIOS = [
  { name: "1440x900-dark-active", width: 1440, height: 900, theme: "dark", state: "active" },
  { name: "1280x800-light-active", width: 1280, height: 800, theme: "light", state: "active" },
  { name: "1024x768-dark-active", width: 1024, height: 768, theme: "dark", state: "active" },
  { name: "390x844-light-stale-menu-closed", width: 390, height: 844, theme: "light", state: "stale", mobile: true, authenticated: true },
  { name: "1280x800-dark-empty", width: 1280, height: 800, theme: "dark", state: "empty" },
  { name: "1280x800-dark-degraded", width: 1280, height: 800, theme: "dark", state: "degraded" },
  { name: "1280x800-dark-focus-fallback", width: 1280, height: 800, theme: "dark", state: "focus_fallback" },
  { name: "1280x800-dark-unavailable", width: 1280, height: 800, theme: "dark", state: "unavailable", authenticated: true },
  { name: "1280x800-light-disabled", width: 1280, height: 800, theme: "light", state: "disabled", authenticated: true },
  { name: "1280x800-light-anonymous-analysis", width: 1280, height: 800, theme: "light", state: "analysis_unrequested" },
  { name: "1280x800-dark-prepared", width: 1280, height: 800, theme: "dark", state: "prepared", authenticated: true },
  { name: "1280x800-dark-queued", width: 1280, height: 800, theme: "dark", state: "queued" },
  { name: "1280x800-dark-in-progress", width: 1280, height: 800, theme: "dark", state: "in_progress" },
  { name: "1280x800-dark-completed", width: 1280, height: 800, theme: "dark", state: "completed" },
  { name: "1280x800-dark-failed", width: 1280, height: 800, theme: "dark", state: "failed" },
  { name: "1280x800-dark-incomplete-output", width: 1280, height: 800, theme: "dark", state: "incomplete_output" },
  { name: "1280x800-light-budget-blocked", width: 1280, height: 800, theme: "light", state: "budget_blocked", authenticated: true },
];

function newsItem(state) {
  const unrequested = state === "analysis_unrequested";
  const failed = state === "failed";
  return {
    news_id: "visual-news-1",
    source: "Reuters",
    title: "大型科技公司上调数据中心资本开支预期，供应链订单与交付节奏出现新的可验证变化",
    summary: "这是一条用于发布验收的有界本地记录，用来检查长标题换行、状态标签和信息层级。",
    published_at: "2026-07-13T13:40:00Z",
    fetched_at: "2026-07-13T13:42:00Z",
    analysis_status: failed ? "failed" : unrequested ? "not_requested" : "completed",
    analysis_trigger_enabled: false,
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
      analysis_trigger_enabled: false,
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
  if (pathname === "/api/catalysts/hotspots/status") {
    const noHotspots = ["empty", "unavailable", "disabled"].includes(publicState);
    const disabled = publicState === "disabled";
    const budgetBlocked = state === "budget_blocked";
    return {
      status: publicState,
      capability: budgetBlocked ? "budget_configuration_required" : disabled ? "disabled" : "enabled",
      action_enabled: !disabled,
      manual_enabled: !disabled,
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

async function installApiFixtures(page, state) {
  await page.route("**/api/**", async route => {
    const request = route.request();
    if (request.method() !== "GET") {
      await route.fulfill({ status: 405, contentType: "application/json", body: JSON.stringify({ detail: "visual_fixture_read_only" }) });
      return;
    }
    const body = jsonFor(new URL(request.url()).pathname, state);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

async function assertStableViewport(page, errors) {
  await expect(page.locator("#cat-connection")).not.toHaveText("读取中");
  await expect(page.locator(".cat-desk")).toBeVisible();
  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  expect(fits).toBe(true);
  expect(errors).toEqual([]);
}

for (const scenario of SCENARIOS) {
  test(`Catalyst Desk visual evidence: ${scenario.name}`, async ({ page }) => {
    const errors = [];
    page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
    page.on("pageerror", error => errors.push(error.message));
    await page.setViewportSize({ width: scenario.width, height: scenario.height });
    await page.addInitScript(theme => localStorage.setItem("optix.theme", theme), scenario.theme);
    if (scenario.authenticated) {
      await page.addInitScript(() => sessionStorage.setItem("optix.app.token", "visual-admin-session"));
    }
    await installApiFixtures(page, scenario.state);
    await page.goto("/#catalysts", { waitUntil: "networkidle" });
    await assertStableViewport(page, errors);

    if (["stale", "unavailable"].includes(scenario.state)) {
      const focusAction = page.locator("#cat-focus-run");
      await expect(focusAction).toBeDisabled();
      await expect(focusAction).toHaveText("热点快照暂不可用");
    }
    if (scenario.state === "disabled") {
      await expect(page.locator("#cat-focus-run")).toBeDisabled();
      await expect(page.locator("#cat-focus-run")).toHaveText("分析功能未启用");
    }
    if (scenario.state === "budget_blocked") {
      await expect(page.locator("#cat-focus-run")).toBeDisabled();
      await expect(page.locator("#cat-focus-run")).toHaveText("分析预算未配置");
    }
    if (scenario.state === "prepared") {
      await expect(page.locator("#cat-focus-run")).toBeEnabled();
      await expect(page.locator("#cat-focus-run")).toContainText("重新分析");
    }
    if (!scenario.authenticated) {
      await expect(page.locator("#cat-refresh")).toHaveCount(0);
      await expect(page.locator("#cat-focus-run")).toHaveCount(0);
      await expect(page.locator("[data-private-focus-note]")).toHaveText("管理会话未解锁");
    }
    if (scenario.state === "analysis_unrequested") {
      await page.locator("[data-catalyst-news]").first().click();
      await expect(page.locator("#cat-analysis-body")).toContainText("管理会话未解锁");
      await expect(page.locator("#cat-analysis-body")).toContainText("当前标签页没有管理令牌");
      await expect(page.locator("#cat-analysis-body [data-cat-analyze]")).toHaveCount(0);
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
  });
}
