import { expect, test } from "@playwright/test";

const NOW = "2026-07-14T18:20:00Z";

const stock = (ticker, price, change) => ({
  ticker,
  name: ticker === "NVDA" ? "英伟达" : ticker === "AMD" ? "超微半导体" : "微软",
  price,
  change_percent: change,
  spark: [price * 0.98, price * 0.99, price, price * 1.01],
});

function responseFor(pathname) {
  if (pathname === "/api/market/indices") {
    return { indices: [{ symbol: "^GSPC", price: 5750.21, change_percent: 0.42 }], as_of: NOW, source_status: "active" };
  }
  if (pathname === "/api/market/status") return { market: "open", phase: "regular", as_of: NOW };
  if (pathname === "/api/stocks/watchlist") {
    return {
      groups: [
        { id: "semis", name: "半导体", stocks: [stock("NVDA", 211.24, 3.79), stock("AMD", 153.95, 2.66)] },
        { id: "software", name: "软件", stocks: [stock("MSFT", 524.11, -0.45)] },
      ],
      as_of: NOW,
      source_status: "active",
      attempted: 3,
      succeeded: 3,
      failed: 0,
      failed_tickers: [],
    };
  }
  if (pathname === "/api/earnings/upcoming") return { earnings: [], as_of: NOW };
  if (pathname === "/api/options/unusual") return { results: [], attempted: 0, as_of: NOW };
  if (/^\/api\/stocks\/[^/]+$/.test(pathname)) {
    const ticker = pathname.split("/").at(-1);
    return { ticker, price: ticker === "AMD" ? 153.95 : 211.24, change: 2.1, volume: 1234567, market_cap: 1234567890 };
  }
  if (/^\/api\/stocks\/[^/]+\/chart$/.test(pathname)) {
    return {
      bars: [
        { t: NOW, o: 150, h: 156, l: 149, c: 154, v: 1000 },
        { t: NOW, o: 154, h: 158, l: 153, c: 157, v: 1100 },
      ],
      as_of: NOW,
    };
  }
  if (/^\/api\/stocks\/[^/]+\/signals$/.test(pathname)) {
    return { score: 72, overall: "bullish", signals: { rsi: { value: 58.4 } } };
  }
  return {};
}

const focusPathFor = (pathname, ticker) => pathname === `/api/stocks/${ticker}`
  || pathname === `/api/stocks/${ticker}/chart`
  || pathname === `/api/stocks/${ticker}/signals`;

async function fulfillJson(route, body, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

test("cold watchlist paints before all five auxiliary requests finish", async ({ page }) => {
  await page.addInitScript(() => {
    const calls = {};
    let net;
    window.__watchAuxiliaryCalls = calls;
    Object.defineProperty(window, "OPTIX_NET", {
      configurable: true,
      get: () => net,
      set: value => {
        for (const name of ["earnings", "unusual", "stock", "chart", "stockSignals"]) {
          const original = value[name];
          value[name] = (...args) => {
            calls[name] = (calls[name] || 0) + 1;
            return original(...args);
          };
        }
        net = value;
      },
    });
  });
  let releaseAuxiliary;
  const auxiliaryGate = new Promise(resolve => { releaseAuxiliary = resolve; });
  const auxiliaryPaths = new Set([
    "/api/earnings/upcoming",
    "/api/options/unusual",
    "/api/stocks/NVDA",
    "/api/stocks/NVDA/chart",
    "/api/stocks/NVDA/signals",
  ]);
  const auxiliaryRequests = new Map();
  let watchlistRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/stocks/watchlist") watchlistRequests += 1;
    if (auxiliaryPaths.has(pathname)) {
      auxiliaryRequests.set(pathname, (auxiliaryRequests.get(pathname) || 0) + 1);
      await auxiliaryGate;
      if (pathname === "/api/options/unusual") {
        await fulfillJson(route, {
          results: [{
            ticker: "NVDA",
            contract_type: "call",
            strike: 220,
            expiration: "2026-07-17",
            vol_oi_ratio: 12.3,
            premium: 500000,
          }],
          attempted: 1,
          as_of: NOW,
        });
        return;
      }
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: /自选观察/ })).toBeVisible({ timeout: 2_000 });
  await expect(page.locator('[data-card="NVDA"]')).toBeVisible({ timeout: 2_000 });
  await expect(page.locator("#view > .view-loading")).toHaveCount(0);
  await expect(page.locator("#view")).toContainText("K线后台读取中");
  await expect(page.locator("#view")).toContainText("期权异动后台读取中");
  await expect(page.locator("#view")).not.toContainText("读取失败");
  await expect.poll(
    () => page.evaluate(() => Object.values(window.__watchAuxiliaryCalls || {}).reduce((sum, count) => sum + count, 0)),
    { timeout: 2_000 },
  ).toBe(5);
  const callsWhilePending = await page.evaluate(() => window.__watchAuxiliaryCalls);
  expect(callsWhilePending).toEqual({ stock: 1, chart: 1, stockSignals: 1, earnings: 1, unusual: 1 });
  await expect.poll(() => page.evaluate(() => window.OPTIX_NET._queueStats().normal)).toEqual({ running: 3, queued: 2, limit: 3 });
  expect(auxiliaryRequests.size).toBe(3);
  expect(watchlistRequests).toBe(1);

  releaseAuxiliary();
  await expect(page.locator("#focus-chart svg")).toBeVisible();
  await expect(page.locator("#view")).toContainText("72 / 100");
  await expect(page.locator("#view")).toContainText("123 万");
  await expect(page.locator(".flow-item")).toContainText("NVDA");
  await expect(page.locator("#view")).toContainText("未来七日暂无财报");
  await expect(page.locator("#view")).not.toContainText("后台读取中");
  await expect(page.locator("#view")).not.toContainText("后台补齐中");
  for (const pathname of auxiliaryPaths) expect(auxiliaryRequests.get(pathname)).toBe(1);
  expect(watchlistRequests).toBe(1);
});

test("a live quote snapshot redraws while every auxiliary promise remains pending", async ({ page }) => {
  await page.addInitScript(() => {
    const never = new Promise(() => {});
    let net;
    Object.defineProperty(window, "OPTIX_NET", {
      configurable: true,
      get: () => net,
      set: value => {
        for (const name of ["earnings", "unusual", "stock", "chart", "stockSignals"]) {
          value[name] = () => never;
        }
        net = value;
      },
    });
  });
  let watchlistRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/stocks/watchlist") {
      watchlistRequests += 1;
      if (watchlistRequests === 2) {
        const refreshed = responseFor(pathname);
        refreshed.as_of = "2026-07-14T18:30:00Z";
        refreshed.groups[0].stocks[0].price = 222.22;
        await fulfillJson(route, refreshed);
        return;
      }
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: /自选观察/ })).toBeVisible();
  await expect(page.locator('[data-card="NVDA"] .stock-card__quote b')).toHaveText("211.24");
  await expect(page.locator("#view")).toContainText("K线后台读取中");

  await page.evaluate(() => {
    const actualNow = Date.now.bind(Date);
    Date.now = () => actualNow() + 61 * 1000;
    document.dispatchEvent(new Event("visibilitychange"));
  });

  await expect.poll(() => watchlistRequests).toBe(2);
  await expect(page.locator('[data-card="NVDA"] .stock-card__quote b')).toHaveText("222.22");
  await expect(page.locator(".pulse-live")).toContainText("快照 14:30");
  await expect(page.locator("#view > .view-loading")).toHaveCount(0);
});

test("a pending chart range does not block quote refresh or duplicate the chart request", async ({ page }) => {
  let releaseChart;
  const chartGate = new Promise(resolve => { releaseChart = resolve; });
  let rangeChartRequests = 0;
  let watchlistRequests = 0;
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname === "/api/stocks/watchlist") {
      watchlistRequests += 1;
      if (watchlistRequests === 2) {
        const refreshed = responseFor(pathname);
        refreshed.as_of = "2026-07-14T18:30:00Z";
        refreshed.groups[0].stocks[0].price = 222.22;
        await fulfillJson(route, refreshed);
        return;
      }
    }
    if (pathname === "/api/stocks/NVDA/chart" && url.searchParams.get("range") === "15m") {
      rangeChartRequests += 1;
      await chartGate;
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "15分" }).click();
  await expect.poll(() => rangeChartRequests).toBe(1);

  await page.evaluate(() => {
    const actualNow = Date.now.bind(Date);
    Date.now = () => actualNow() + 61 * 1000;
    document.dispatchEvent(new Event("visibilitychange"));
  });

  await expect.poll(() => watchlistRequests).toBe(2);
  await expect(page.locator('[data-card="NVDA"] .stock-card__quote b')).toHaveText("222.22");
  await expect(page.locator(".pulse-live")).toContainText("快照 14:30");
  await expect(page.locator("#view")).toContainText("K线后台读取中");
  expect(rangeChartRequests).toBe(1);

  releaseChart();
  await expect(page.locator("#focus-chart svg")).toBeVisible();
  expect(rangeChartRequests).toBe(1);
});

test("focus failure cooldown does not create a silent redraw loop", async ({ page }) => {
  let nvdaQuoteRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/stocks/NVDA") {
      nvdaQuoteRequests += 1;
      await fulfillJson(route, { detail: "temporary quote failure" }, 404);
      return;
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await expect(page.locator("#focus-chart svg")).toBeVisible();
  await expect(page.locator("#view")).toContainText("72 / 100");
  expect(nvdaQuoteRequests).toBe(1);

  await page.evaluate(() => {
    window.__watchRootMutations = 0;
    window.__watchRootObserver = new MutationObserver(records => {
      window.__watchRootMutations += records.length;
    });
    window.__watchRootObserver.observe(document.querySelector("#view"), { childList: true });
  });
  await page.locator('[data-wf="up"]').click();
  await expect(page.locator('[data-wf="up"]')).toHaveClass(/active/);
  await page.waitForTimeout(150);
  const mutationsAfterFilter = await page.evaluate(() => window.__watchRootMutations);
  expect(mutationsAfterFilter).toBeGreaterThan(0);
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => window.__watchRootMutations)).toBe(mutationsAfterFilter);
  expect(nvdaQuoteRequests).toBe(1);

  await page.locator('[data-wf="all"]').click();
  await expect(page.locator('[data-wf="all"]')).toHaveClass(/active/);
});

test("watchlist returns instantly and local controls do not refetch the full snapshot", async ({ page }) => {
  const requests = [];
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    requests.push(`${route.request().method()} ${url.pathname}`);
    await fulfillJson(route, responseFor(url.pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: /自选观察/ })).toBeVisible();
  expect(requests.filter(item => item === "GET /api/stocks/watchlist")).toHaveLength(1);

  await page.getByRole("link", { name: "财报" }).click();
  await expect(page.getByRole("heading", { name: /财报日历/ })).toBeVisible();
  await page.getByRole("link", { name: "自选" }).click();
  await expect(page.getByRole("heading", { name: /自选观察/ })).toBeVisible();
  await expect(page.locator(".view-loading")).toHaveCount(0);
  expect(requests.filter(item => item === "GET /api/stocks/watchlist")).toHaveLength(1);

  const beforeLocalFilters = requests.length;
  await page.locator('[data-wf="up"]').click();
  await expect(page.locator('[data-card="NVDA"]')).toBeVisible();
  await page.locator('[data-wf="all"]').click();
  await page.locator('[data-wg="software"]').click();
  await expect(page.locator('[data-card="MSFT"]')).toBeVisible();
  expect(requests).toHaveLength(beforeLocalFilters);

  await page.locator('[data-wg="semis"]').click();
  const beforeFocus = requests.length;
  await page.locator('[data-card="AMD"]').click();
  await expect(page.getByRole("heading", { name: /超微半导体 AMD/ })).toBeVisible();
  await expect.poll(() => requests.slice(beforeFocus).filter(item => item.startsWith("GET /api/stocks/AMD")).length).toBe(3);
  const focusRequests = requests.slice(beforeFocus);
  expect(focusRequests).toEqual(expect.arrayContaining([
    "GET /api/stocks/AMD",
    "GET /api/stocks/AMD/chart",
    "GET /api/stocks/AMD/signals",
  ]));
  expect(focusRequests.some(item => item === "GET /api/stocks/watchlist")).toBe(false);
});

test("a late watchlist focus response cannot overwrite the Catalyst route", async ({ page }) => {
  let releaseFocus;
  const focusGate = new Promise(resolve => { releaseFocus = resolve; });
  let heldFocusRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (focusPathFor(pathname, "AMD")) {
      heldFocusRequests += 1;
      await focusGate;
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await page.locator('[data-card="AMD"]').click();
  await expect.poll(() => heldFocusRequests).toBe(3);

  await page.getByRole("link", { name: "催化剂" }).click();
  await expect(page.locator(".cat-desk")).toBeVisible();
  releaseFocus();
  await page.waitForTimeout(150);

  await expect(page.locator(".cat-desk")).toBeVisible();
  await expect(page.getByRole("heading", { name: /自选观察/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "催化剂" }).first()).toHaveAttribute("aria-current", "page");
});

test("a completed off-route focus request redraws the restored watchlist", async ({ page }) => {
  let releaseFocus;
  const focusGate = new Promise(resolve => { releaseFocus = resolve; });
  let heldFocusRequests = 0;
  let completedFocusRequests = 0;
  let watchlistRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/stocks/watchlist") watchlistRequests += 1;
    if (focusPathFor(pathname, "AMD")) {
      heldFocusRequests += 1;
      await focusGate;
      await fulfillJson(route, responseFor(pathname));
      completedFocusRequests += 1;
      return;
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await page.locator('[data-card="AMD"]').click();
  await expect.poll(() => heldFocusRequests).toBe(3);

  await page.getByRole("link", { name: "催化剂" }).click();
  await expect(page.locator(".cat-desk")).toBeVisible();
  releaseFocus();
  await expect.poll(() => completedFocusRequests).toBe(3);

  await page.getByRole("link", { name: "自选" }).click();
  await expect(page.getByRole("heading", { name: /超微半导体 AMD/ })).toBeVisible();
  await expect(page.locator('[data-card="AMD"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#focus-chart svg")).toBeVisible();
  expect(watchlistRequests).toBe(1);
});

test("rapid local filtering does not strand a pending focus request", async ({ page }) => {
  let releaseFocus;
  const focusGate = new Promise(resolve => { releaseFocus = resolve; });
  let heldFocusRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (focusPathFor(pathname, "AMD")) {
      heldFocusRequests += 1;
      await focusGate;
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await page.locator('[data-card="AMD"]').click();
  await expect.poll(() => heldFocusRequests).toBe(3);
  await page.locator('[data-wf="up"]').click();
  releaseFocus();

  await expect(page.getByRole("heading", { name: /超微半导体 AMD/ })).toBeVisible();
  await expect(page.locator("#focus-chart svg")).toBeVisible();
  await expect(page.locator("#view")).toContainText("72 / 100");
  await expect(page.locator("#view")).not.toContainText("正在后台更新");
});

test("a background snapshot redraws while focus details remain pending", async ({ page }) => {
  let releaseWatchlist;
  const watchlistGate = new Promise(resolve => { releaseWatchlist = resolve; });
  let releaseFocus;
  const focusGate = new Promise(resolve => { releaseFocus = resolve; });
  let watchlistRequests = 0;
  let heldFocusRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/stocks/watchlist") {
      watchlistRequests += 1;
      if (watchlistRequests === 2) {
        await watchlistGate;
        const refreshed = responseFor(pathname);
        refreshed.as_of = "2026-07-14T18:30:00Z";
        refreshed.groups[0].stocks[1].price = 160;
        await fulfillJson(route, refreshed);
        return;
      }
    }
    if (focusPathFor(pathname, "AMD")) {
      heldFocusRequests += 1;
      await focusGate;
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await page.evaluate(() => {
    const actualNow = Date.now.bind(Date);
    Date.now = () => actualNow() + 61 * 1000;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => watchlistRequests).toBe(2);

  await page.locator('[data-card="AMD"]').click();
  await expect.poll(() => heldFocusRequests).toBe(2);
  releaseWatchlist();
  await expect.poll(() => heldFocusRequests).toBe(3);
  await expect(page.locator('[data-card="AMD"] .stock-card__quote b')).toHaveText("160.00");
  await expect(page.locator(".pulse-live")).toContainText("快照 14:30");
  releaseFocus();

  await expect(page.getByRole("heading", { name: /超微半导体 AMD/ })).toBeVisible();
  await expect(page.locator('[data-card="AMD"] .stock-card__quote b')).toHaveText("160.00");
  await expect(page.locator(".pulse-live")).toContainText("快照 14:30");
  await expect(page.locator("#view")).not.toContainText("正在后台更新");
});

test("a failed focus quote is retried instead of being kept in the browser cache", async ({ page }) => {
  let amdQuoteRequests = 0;
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/stocks/AMD") {
      amdQuoteRequests += 1;
      if (amdQuoteRequests === 1) {
        await fulfillJson(route, { detail: "temporary quote failure" }, 404);
        return;
      }
    }
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  await page.locator('[data-card="AMD"]').click();
  await expect(page.getByRole("heading", { name: /超微半导体 AMD/ })).toBeVisible();
  await expect.poll(() => amdQuoteRequests).toBe(1);
  await expect(page.locator("#focus-chart svg")).toBeVisible();
  await expect(page.locator("#view")).toContainText("72 / 100");

  await page.locator('[data-card="NVDA"]').click();
  await expect(page.getByRole("heading", { name: /英伟达 NVDA/ })).toBeVisible();
  await page.locator('[data-card="AMD"]').click();

  await expect.poll(() => amdQuoteRequests).toBe(2);
  await expect(page.getByRole("heading", { name: /超微半导体 AMD/ })).toBeVisible();
  await expect(page.locator("#view")).toContainText("123 万");
});

test("bounded background refresh replaces stale focus data and preserves keyboard focus", async ({ page }) => {
  const counts = new Map();
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    counts.set(pathname, (counts.get(pathname) || 0) + 1);
    await fulfillJson(route, responseFor(pathname));
  });

  await page.goto("/#watchlist", { waitUntil: "networkidle" });
  const filter = page.locator('[data-wf="up"]');
  await filter.focus();
  await expect(filter).toBeFocused();

  await page.evaluate(() => {
    const actualNow = Date.now.bind(Date);
    Date.now = () => actualNow() + 16 * 60 * 1000;
    document.dispatchEvent(new Event("visibilitychange"));
  });

  await expect.poll(() => counts.get("/api/stocks/watchlist") || 0).toBe(2);
  await expect.poll(() => counts.get("/api/stocks/NVDA") || 0).toBe(2);
  await expect.poll(() => counts.get("/api/stocks/NVDA/chart") || 0).toBe(2);
  await expect.poll(() => counts.get("/api/stocks/NVDA/signals") || 0).toBe(2);
  await expect(page.locator('[data-wf="up"]')).toBeFocused();
  await expect(page.locator("#focus-chart svg")).toBeVisible();
});
