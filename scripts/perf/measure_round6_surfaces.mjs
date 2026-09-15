#!/usr/bin/env node
/**
 * Round-6 first-open / first-nav / earnings-chart / intent-prefetch lab.
 * Paid upstream hosts are aborted. Earnings calendar is fulfilled locally
 * so chart-lazy tests do not trigger Finnhub/Yahoo/FMP.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { afterSampleGap, attach429Counter } from './lib/rate_limit.mjs';
import { isTerminalReady, pageReadyInstallScript } from './lib/page_ready.mjs';
import { earningsFixture, homeLabFixtures } from './lib/round6_lab_fixtures.mjs';
import { decideIntentPrefetch } from './lib/round6_intent_decision.mjs';
import { readyGateFailures, summarizeReady } from './lib/round6_ready_summary.mjs';

const require = createRequire(fileURLToPath(import.meta.url));
const { chromium } = require(path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../frontend-src/node_modules/playwright',
));

const BASE = process.env.OPTIX_PERF_BASE || 'http://127.0.0.1:2000';
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/round6-surfaces.json';
const REPEATS = Number(process.env.OPTIX_PERF_REPEATS || 8);
const PROFILE = process.env.OPTIX_PERF_PROFILE || 'mobile-ref';
const PAID = /finnhub|yahoo|yfinance|polygon|massive|fmpcloud|twelvedata|openai|macrolens/i;

const PROFILES = {
  'mobile-ref': {
    width: 390, height: 844, dpr: 3, cpu: 4,
    down: (10 * 1024 * 1024) / 8, up: (2 * 1024 * 1024) / 8, rtt: 180, mobile: true,
  },
  // xl+ so the numbered desktop nav is display:flex. Hover is a real
  // pointer gesture here; 390px hides that nav and puts /earnings behind
  // the dock "更多" sheet (a button, not <a href="/earnings">).
  desktop: {
    width: 1440, height: 900, dpr: 1, cpu: 4,
    down: (10 * 1024 * 1024) / 8, up: (2 * 1024 * 1024) / 8, rtt: 180, mobile: false,
  },
};
const profile = PROFILES[PROFILE];
if (!profile) throw new Error(`unknown profile ${PROFILE}`);

function percentile(values, q) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * q)))];
}

function summarize(rows, field = 'ready_ms') {
  return summarizeReady(rows, field);
}

async function applyThrottle(page) {
  const client = await page.context().newCDPSession(page);
  if (profile.cpu > 1) await client.send('Emulation.setCPUThrottlingRate', { rate: profile.cpu });
  if (profile.down > 0) {
    await client.send('Network.enable');
    await client.send('Network.emulateNetworkConditions', {
      offline: false,
      downloadThroughput: profile.down,
      uploadThroughput: profile.up,
      latency: profile.rtt,
    });
  }
}

function jsonOk(body) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  };
}

async function installLabRoutes(page, { mockEarnings = true } = {}) {
  const fixtures = homeLabFixtures();
  await page.route('**/*', async (route) => {
    const url = route.request().url();
    if (PAID.test(url) || url.includes('/api/quotes/stream')) {
      await route.abort();
      return;
    }
    if (mockEarnings && url.includes('/api/earnings/upcoming') && !url.includes('refresh')) {
      await route.fulfill(jsonOk(earningsFixture()));
      return;
    }
    // Home / 财报会打这些端点。浏览器 abort 付费域名挡不住 uvicorn 出站，
    // 必须在到达隔离后端之前 fulfill，避免 Yahoo/Finnhub。
    if (url.includes('/api/market/indices')) {
      await route.fulfill(jsonOk(fixtures.indices));
      return;
    }
    if (url.includes('/api/market/status')) {
      await route.fulfill(jsonOk(fixtures.status));
      return;
    }
    if (url.includes('/api/strength/market')) {
      await route.fulfill(jsonOk(fixtures.strength));
      return;
    }
    if (url.includes('/api/signals/market')) {
      await route.fulfill(jsonOk(fixtures.signals));
      return;
    }
    if (url.includes('/api/breakouts/current')) {
      await route.fulfill(jsonOk(fixtures.breakoutsCurrent));
      return;
    }
    if (url.includes('/api/breakouts/status')) {
      await route.fulfill(jsonOk(fixtures.breakoutsStatus));
      return;
    }
    if (url.includes('/api/stocks/watchlist')) {
      await route.fulfill(jsonOk(fixtures.watchlist));
      return;
    }
    if (url.includes('/api/market/cta')) {
      await route.fulfill(jsonOk(fixtures.cta));
      return;
    }
    if (/\/api\/quotes(?:\?|$)/.test(url)) {
      await route.fulfill(jsonOk(fixtures.quotes));
      return;
    }
    if (url.includes('/api/account/watchlist')) {
      await route.fulfill(jsonOk(fixtures.accountWatchlist));
      return;
    }
    await route.continue();
  });
}

function attachNetwork(page) {
  const state = {
    requests: 0,
    chart: 0,
    runtimeEn: 0,
    runtimeJa: 0,
    earningsChunk: 0,
    stockChunk: 0,
    homeChunk: 0,
    abortedPaid: 0,
    fulfilled_upcoming: 0,
    fulfilled_indices: 0,
    httpErrors: 0,
    requestFailures: [],
  };
  page.on('request', (request) => {
    state.requests += 1;
    const url = request.url();
    if (PAID.test(url)) state.abortedPaid += 1;
    if (url.includes('/api/earnings/upcoming') && !url.includes('refresh')) state.fulfilled_upcoming += 1;
    if (url.includes('/api/market/indices')) state.fulfilled_indices += 1;
    if (/\/assets\/(?:eps-chart|chart)-/.test(url) || url.includes('EpsHatchChart')) state.chart += 1;
    if (url.includes('runtime-en')) state.runtimeEn += 1;
    if (url.includes('runtime-ja')) state.runtimeJa += 1;
    if (/\/assets\/Earnings-/.test(url) || url.includes('/pages/Earnings')) state.earningsChunk += 1;
    if (/\/assets\/StockDetail-/.test(url) || url.includes('/pages/StockDetail')) state.stockChunk += 1;
    if (/\/assets\/Home-/.test(url) || url.includes('/pages/Home')) state.homeChunk += 1;
  });
  page.on('response', (response) => {
    if (response.status() >= 400) state.httpErrors += 1;
  });
  page.on('requestfailed', (request) => {
    const url = request.url();
    if (PAID.test(url) || url.includes('/api/quotes/stream')) return;
    const errorText = request.failure()?.errorText || 'unknown';
    if (/ERR_ABORTED/i.test(errorText)) return;
    state.requestFailures.push({ url: url.replace(/^https?:\/\/[^/]+/, ''), error: errorText });
  });
  return state;
}

function networkOutcome(network) {
  return {
    http_error_n: network.httpErrors,
    request_failed_n: network.requestFailures.length,
    request_failures: network.requestFailures,
  };
}

async function collectResources(page) {
  return page.evaluate(() => {
    const resources = performance.getEntriesByType('resource');
    return {
      transferSize: resources.reduce((sum, entry) => sum + (entry.transferSize || 0), 0),
      encodedBodySize: resources.reduce((sum, entry) => sum + (entry.encodedBodySize || 0), 0),
      resourceCount: resources.length,
    };
  });
}

async function waitReady(page, route, timeout = 60_000) {
  const ready = await page.waitForFunction((path) => {
    const kind = window.__optixPageReadyClass(path);
    if (kind === 'content' || kind === 'empty' || kind === 'error' || kind === 'idle') {
      return { kind, at: performance.now() };
    }
    return false;
  }, route, { timeout }).then(async (handle) => handle.jsonValue()).catch(() => null);
  const kind = ready?.kind && isTerminalReady(ready.kind) ? ready.kind : 'timeout';
  return { kind, at: ready?.at ?? null };
}

async function withPage(fn, { viewport = 'mobile-ref' } = {}) {
  const vp = PROFILES[viewport];
  if (!vp) throw new Error(`unknown viewport ${viewport}`);
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: vp.dpr,
    isMobile: vp.mobile,
    hasTouch: vp.mobile,
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  await page.addInitScript({
    content: `${pageReadyInstallScript()}; try { localStorage.setItem('optix:locale', 'zh'); } catch (e) {}`,
  });
  const rateLimit = attach429Counter(page);
  const network = attachNetwork(page);
  await applyThrottle(page);
  try {
    return await fn(page, network, rateLimit);
  } finally {
    await context.close();
    await browser.close();
  }
}

function homeEarningsLink(page) {
  return page.locator('section[aria-label="财报临近"] a[href="/earnings"]');
}

function desktopEarningsNav(page) {
  return page.locator('nav[aria-label="主导航"] a[href="/earnings"]');
}

const pages = { '/': [], '/earnings': [] };
for (const route of Object.keys(pages)) {
  for (let i = 0; i < REPEATS; i += 1) {
    const sample = await withPage(async (page, network, rateLimit) => {
      await installLabRoutes(page);
      const started = Date.now();
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
      const ready = await waitReady(page, route);
      const resources = await collectResources(page);
      return {
        wall_ms: Date.now() - started,
        ready_ms: ready.at,
        ready_class: ready.kind,
        chart_loaded: network.chart > 0,
        runtime_en: network.runtimeEn > 0,
        runtime_ja: network.runtimeJa > 0,
        request_count: network.requests,
        fulfilled_upcoming: network.fulfilled_upcoming,
        fulfilled_indices: network.fulfilled_indices,
        rate_limited: rateLimit.count,
        ...networkOutcome(network),
        ...resources,
      };
    });
    pages[route].push(sample);
    console.log(`${route} #${i + 1} class=${sample.ready_class} ready=${sample.ready_ms} chart=${sample.chart_loaded} xfer=${sample.transferSize}`);
    await afterSampleGap({ rateLimitedCount: sample.rate_limited, last: i + 1 >= REPEATS && route === '/earnings' });
  }
}

async function firstNavSample(page, network, rateLimit, open) {
  await installLabRoutes(page);
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  await waitReady(page, '/');
  const before = { req: network.requests, earn: network.earningsChunk, chart: network.chart };
  const started = Date.now();
  await open(page);
  const ready = await waitReady(page, '/earnings');
  return {
    ready_ms: ready.at,
    wall_ms: Date.now() - started,
    ready_class: ready.kind,
    chart_loaded: network.chart > before.chart,
    earnings_chunk: network.earningsChunk > before.earn,
    request_count: network.requests - before.req,
    rate_limited: rateLimit.count,
    ...networkOutcome(network),
  };
}

const nav = { home_card: [], desktop_nav: [] };
for (let i = 0; i < REPEATS; i += 1) {
  nav.home_card.push(await withPage(async (page, network, rateLimit) => (
    firstNavSample(page, network, rateLimit, async (target) => {
      await homeEarningsLink(target).click();
    })
  )));
  nav.desktop_nav.push(await withPage(async (page, network, rateLimit) => (
    firstNavSample(page, network, rateLimit, async (target) => {
      await desktopEarningsNav(target).click();
    })
  ), { viewport: 'desktop' }));
  console.log(
    `nav #${i + 1} home=${nav.home_card.at(-1).wall_ms}/${nav.home_card.at(-1).ready_class} `
    + `desk=${nav.desktop_nav.at(-1).wall_ms}/${nav.desktop_nav.at(-1).ready_class}`,
  );
  await afterSampleGap({
    rateLimitedCount: (nav.home_card.at(-1).rate_limited || 0) + (nav.desktop_nav.at(-1).rate_limited || 0),
    last: i + 1 >= REPEATS,
  });
}

const intent = { immediate: [], hover_then_click: [], hover_only: [] };
for (let i = 0; i < REPEATS; i += 1) {
  const intentOpts = { viewport: 'desktop' };
  intent.immediate.push(await withPage(async (page, network, rateLimit) => {
    await installLabRoutes(page);
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    await waitReady(page, '/');
    const before = network.earningsChunk;
    const started = Date.now();
    await desktopEarningsNav(page).click();
    const ready = await waitReady(page, '/earnings');
    return {
      ready_ms: ready.at,
      wall_ms: Date.now() - started,
      ready_class: ready.kind,
      prefetched_before_click: before > 0,
      chart_loaded: network.chart > 0,
      rate_limited: rateLimit.count,
      ...networkOutcome(network),
    };
  }, intentOpts));
  intent.hover_then_click.push(await withPage(async (page, network, rateLimit) => {
    await installLabRoutes(page);
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    await waitReady(page, '/');
    const link = desktopEarningsNav(page);
    await link.hover();
    await page.waitForTimeout(400);
    const prefetched = network.earningsChunk > 0;
    const started = Date.now();
    await link.click();
    const ready = await waitReady(page, '/earnings');
    return {
      ready_ms: ready.at,
      wall_ms: Date.now() - started,
      ready_class: ready.kind,
      prefetched_before_click: prefetched,
      chart_loaded: network.chart > 0,
      rate_limited: rateLimit.count,
      ...networkOutcome(network),
    };
  }, intentOpts));
  intent.hover_only.push(await withPage(async (page, network, rateLimit) => {
    await installLabRoutes(page);
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    const ready = await waitReady(page, '/');
    const beforePaid = network.abortedPaid;
    await desktopEarningsNav(page).hover();
    await page.waitForTimeout(800);
    return {
      ready_class: ready.kind,
      ready_ms: ready.at,
      earnings_chunk: network.earningsChunk > 0,
      chart_loaded: network.chart > 0,
      extra_paid: network.abortedPaid > beforePaid,
      rate_limited: rateLimit.count,
      ...networkOutcome(network),
    };
  }, intentOpts));
  console.log(
    `intent #${i + 1} immediate=${intent.immediate.at(-1).wall_ms} `
    + `hover=${intent.hover_then_click.at(-1).wall_ms} `
    + `hover_only_chunk=${intent.hover_only.at(-1).earnings_chunk}`,
  );
  await afterSampleGap({ last: i + 1 >= REPEATS });
}

const extras = { no_intent: [], palette_closed: [] };
for (let i = 0; i < REPEATS; i += 1) {
  extras.no_intent.push(await withPage(async (page, network) => {
    await installLabRoutes(page);
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    const ready = await waitReady(page, '/');
    await page.waitForTimeout(800);
    return {
      ready_class: ready.kind,
      ready_ms: ready.at,
      earnings_chunk: network.earningsChunk > 0,
      stock_chunk: network.stockChunk > 0,
      chart_loaded: network.chart > 0,
      ...networkOutcome(network),
    };
  }, { viewport: 'desktop' }));
  extras.palette_closed.push(await withPage(async (page, network) => {
    await installLabRoutes(page);
    await page.addInitScript({ content: "try { localStorage.setItem('optix:recent-tickers', JSON.stringify(['AAPL'])); } catch (e) {}" });
    await page.goto(`${BASE}/earnings`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    const ready = await waitReady(page, '/earnings');
    await page.waitForTimeout(600);
    return {
      ready_class: ready.kind,
      ready_ms: ready.at,
      stock_chunk: network.stockChunk > 0,
      home_chunk: network.homeChunk > 0,
      chart_loaded: network.chart > 0,
      ...networkOutcome(network),
    };
  }, { viewport: 'desktop' }));
  console.log(
    `extras #${i + 1} no_intent_chunk=${extras.no_intent.at(-1).earnings_chunk} `
    + `palette_stock=${extras.palette_closed.at(-1).stock_chunk}`,
  );
  await afterSampleGap({ last: i + 1 >= REPEATS });
}

const scroll = [];
for (let i = 0; i < REPEATS; i += 1) {
  const sample = await withPage(async (page, network, rateLimit) => {
    await installLabRoutes(page);
    await page.goto(`${BASE}/earnings`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    await waitReady(page, '/earnings');
    const before = network.chart;
    const slot = await page.evaluate(() => {
      const node = document.querySelector('[data-eps-chart-slot]');
      if (!node) return null;
      const rect = node.getBoundingClientRect();
      return { top: rect.top, height: rect.height };
    });
    // 近滚：把图槽送到首屏以下、rootMargin 100% 以内。
    // 固定滚 0.9 视口在图槽更靠下时到不了观察区（本轮槽顶约 4300px）。
    await page.evaluate(() => {
      const node = document.querySelector('[data-eps-chart-slot]');
      if (!node) return;
      const top = node.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, Math.max(0, top - Math.round(window.innerHeight * 0.7)));
    });
    await page.waitForFunction(
      () => Boolean(document.querySelector('[data-eps-chart], [data-eps-chart-error], canvas')),
      null,
      { timeout: 8_000 },
    ).catch(() => null);
    await page.waitForTimeout(400);
    const afterNear = network.chart
      + (await page.evaluate(() => document.querySelectorAll('[data-eps-chart], canvas').length));
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(400);
    await page.evaluate(() => {
      const node = document.querySelector('[data-eps-chart-slot]');
      if (!node) return;
      const top = node.getBoundingClientRect().top + window.scrollY;
      window.scrollTo(0, Math.max(0, top - Math.round(window.innerHeight * 0.7)));
    });
    await page.waitForTimeout(400);
    const mounted = await page.evaluate(() => {
      const slot = document.querySelector('[data-eps-chart-slot]');
      if (!slot) return { slot: false, canvas: 0, chart: 0 };
      return {
        slot: true,
        canvas: slot.querySelectorAll('canvas').length,
        chart: slot.querySelectorAll('[data-eps-chart]').length,
      };
    });
    return {
      placeholder_top: slot?.top ?? null,
      placeholder_height: slot?.height ?? null,
      chart_before_scroll: before > 0,
      chart_after_near_scroll: afterNear > 0,
      chart_node_n: mounted.chart,
      chart_canvas_n: mounted.canvas,
      chart_stayed_mounted: mounted.slot && (mounted.canvas > 0 || mounted.chart > 0),
      rate_limited: rateLimit.count,
      ...networkOutcome(network),
    };
  });
  scroll.push(sample);
  console.log(`scroll #${i + 1} before=${sample.chart_before_scroll} after=${sample.chart_after_near_scroll} h=${sample.placeholder_height}`);
  await afterSampleGap({ last: i + 1 >= REPEATS });
}

const report = {
  lab: true,
  notRUM: true,
  profile: PROFILE,
  base: BASE,
  product_commit: process.env.OPTIX_PERF_PRODUCT_COMMIT || null,
  entry: process.env.OPTIX_PERF_ENTRY || null,
  measuredAt: new Date().toISOString(),
  pages: {
    '/': summarize(pages['/']),
    '/earnings': summarize(pages['/earnings']),
  },
  first_nav: {
    home_card: summarize(nav.home_card, 'wall_ms'),
    desktop_nav: summarize(nav.desktop_nav, 'wall_ms'),
  },
  intent: {
    immediate: summarize(intent.immediate, 'wall_ms'),
    hover_then_click: summarize(intent.hover_then_click, 'wall_ms'),
    hover_only: {
      ...summarize(intent.hover_only),
      chunk_n: intent.hover_only.filter((row) => row.earnings_chunk).length,
      chart_n: intent.hover_only.filter((row) => row.chart_loaded).length,
      extra_paid_n: intent.hover_only.filter((row) => row.extra_paid).length,
    },
  },
  extras: {
    no_intent: {
      ...summarize(extras.no_intent),
      chunk_n: extras.no_intent.filter((row) => row.earnings_chunk).length,
      stock_n: extras.no_intent.filter((row) => row.stock_chunk).length,
      chart_n: extras.no_intent.filter((row) => row.chart_loaded).length,
    },
    palette_closed: {
      ...summarize(extras.palette_closed),
      stock_n: extras.palette_closed.filter((row) => row.stock_chunk).length,
      home_n: extras.palette_closed.filter((row) => row.home_chunk).length,
      chart_n: extras.palette_closed.filter((row) => row.chart_loaded).length,
    },
    no_intent_chunk_n: extras.no_intent.filter((row) => row.earnings_chunk).length,
    palette_closed_stock_n: extras.palette_closed.filter((row) => row.stock_chunk).length,
    samples: extras,
  },
  earnings_scroll: {
    n: scroll.length,
    chart_before_n: scroll.filter((row) => row.chart_before_scroll).length,
    chart_after_n: scroll.filter((row) => row.chart_after_near_scroll).length,
    stayed_n: scroll.filter((row) => row.chart_stayed_mounted).length,
    samples: scroll,
  },
};
const intentDecision = decideIntentPrefetch(report.intent, { expectedN: REPEATS });
report.intent.decision = intentDecision;
const gate = [
  ...readyGateFailures(report.pages['/'], { expectedN: REPEATS, label: 'home', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.pages['/earnings'], { expectedN: REPEATS, label: 'earnings', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.first_nav.home_card, { expectedN: REPEATS, label: 'nav_home_card', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.first_nav.desktop_nav, { expectedN: REPEATS, label: 'nav_desktop', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.intent.immediate, { expectedN: REPEATS, label: 'intent_immediate', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.intent.hover_then_click, { expectedN: REPEATS, label: 'intent_hover_then_click', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.intent.hover_only, { expectedN: REPEATS, label: 'intent_hover_only', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.extras.no_intent, { expectedN: REPEATS, label: 'extras_no_intent', requireNetworkTelemetry: true }),
  ...readyGateFailures(report.extras.palette_closed, { expectedN: REPEATS, label: 'extras_palette_closed', requireNetworkTelemetry: true }),
];
if (report.earnings_scroll.stayed_n !== REPEATS) {
  gate.push(`earnings_scroll: stayed_n=${report.earnings_scroll.stayed_n} expected=${REPEATS}`);
}
if (report.earnings_scroll.chart_before_n !== 0) {
  gate.push(`earnings_scroll: chart_before_n=${report.earnings_scroll.chart_before_n} expected=0`);
}
if (report.earnings_scroll.chart_after_n !== REPEATS) {
  gate.push(`earnings_scroll: chart_after_n=${report.earnings_scroll.chart_after_n} expected=${REPEATS}`);
}
const scrollHttpErrors = scroll.reduce((sum, row) => sum + Number(row.http_error_n || 0), 0);
const scrollRequestFailures = scroll.reduce((sum, row) => sum + Number(row.request_failed_n || 0), 0);
const scrollRateLimited = scroll.filter((row) => (row.rate_limited || 0) > 0).length;
if (scrollRateLimited) gate.push(`earnings_scroll: rate_limited_n=${scrollRateLimited}`);
if (scrollHttpErrors) gate.push(`earnings_scroll: http_error_n=${scrollHttpErrors}`);
if (scrollRequestFailures) gate.push(`earnings_scroll: request_failed_n=${scrollRequestFailures}`);
if (report.extras.no_intent_chunk_n > 0) {
  gate.push(`no_intent: earnings_chunk_n=${report.extras.no_intent_chunk_n}`);
}
if (report.extras.palette_closed_stock_n > 0) {
  gate.push(`palette_closed: stock_chunk_n=${report.extras.palette_closed_stock_n}`);
}
if (intentDecision.decision !== 'keep') {
  gate.push(`intent: ${intentDecision.decision}/${intentDecision.reason}`);
}
report.gate = { ok: gate.length === 0, failures: gate };
if (gate.length) {
  console.error(JSON.stringify(report.gate, null, 2));
}
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({
  pages: { '/': report.pages['/'].p75, '/earnings': report.pages['/earnings'].p75 },
  first_nav: {
    home_card: report.first_nav.home_card.p75,
    desktop_nav: report.first_nav.desktop_nav.p75,
  },
  intent: {
    immediate: report.intent.immediate.p75,
    hover_then_click: report.intent.hover_then_click.p75,
    hover_only_chunk: report.intent.hover_only.chunk_n,
  },
  earnings_scroll: {
    before: report.earnings_scroll.chart_before_n,
    after: report.earnings_scroll.chart_after_n,
  },
}, null, 2));
console.log(`wrote ${OUT}`);
if (gate.length) process.exit(1);
