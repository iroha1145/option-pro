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
  const values = rows.map((row) => row[field]).filter((value) => value != null);
  return {
    n: rows.length,
    ready_n: values.length,
    timeout_n: rows.length - values.length,
    p50: percentile(values, 0.5),
    p75: percentile(values, 0.75),
    spread_iqr: values.length
      ? percentile(values, 0.75) - percentile(values, 0.25)
      : null,
    transfer_p50: percentile(rows.map((row) => row.transferSize || 0), 0.5),
    request_p50: percentile(rows.map((row) => row.request_count || 0), 0.5),
    chart_loaded_n: rows.filter((row) => row.chart_loaded).length,
    runtime_en_n: rows.filter((row) => row.runtime_en).length,
    runtime_ja_n: rows.filter((row) => row.runtime_ja).length,
    rate_limited_n: rows.filter((row) => (row.rate_limited || 0) > 0).length,
    samples: rows,
  };
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

function earningsFixture() {
  const today = new Date();
  const iso = (offset) => {
    const date = new Date(today);
    date.setDate(date.getDate() + offset);
    return date.toISOString().slice(0, 10);
  };
  const row = (ticker, offset, name) => ({
    ticker,
    name,
    date: iso(offset),
    earnings_date: iso(offset),
    timing: 'amc',
    epsEstimate: 1.25 + offset / 10,
    epsActual: offset < 0 ? 1.3 : null,
    eps_estimate: 1.25 + offset / 10,
    eps_actual: offset < 0 ? 1.3 : null,
    marketCap: 2e12,
    sector: 'Technology',
  });
  return {
    earnings: [
      row('AAPL', 0, 'Apple'),
      row('MSFT', 1, 'Microsoft'),
      row('NVDA', 2, 'NVIDIA'),
      row('AMZN', 3, 'Amazon'),
      row('META', 4, 'Meta'),
      row('GOOGL', 8, 'Alphabet'),
    ],
    items: [
      row('AAPL', 0, 'Apple'),
      row('MSFT', 1, 'Microsoft'),
      row('NVDA', 2, 'NVIDIA'),
      row('AMZN', 3, 'Amazon'),
      row('META', 4, 'Meta'),
      row('GOOGL', 8, 'Alphabet'),
    ],
    attempted: 6,
    succeeded: 6,
    failed_symbols: [],
    data_limited: false,
    source_status: 'ok',
    as_of: new Date().toISOString(),
  };
}

async function installLabRoutes(page, { mockEarnings = true } = {}) {
  await page.route('**/*', async (route) => {
    const url = route.request().url();
    if (PAID.test(url)) {
      await route.abort();
      return;
    }
    if (mockEarnings && url.includes('/api/earnings/upcoming') && !url.includes('refresh')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(earningsFixture()),
      });
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
    abortedPaid: 0,
  };
  page.on('request', (request) => {
    state.requests += 1;
    const url = request.url();
    if (PAID.test(url)) state.abortedPaid += 1;
    if (/\/assets\/chart-/.test(url) || url.includes('EpsHatchChart')) state.chart += 1;
    if (url.includes('runtime-en')) state.runtimeEn += 1;
    if (url.includes('runtime-ja')) state.runtimeJa += 1;
    if (/\/assets\/Earnings-/.test(url)) state.earningsChunk += 1;
  });
  return state;
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
        rate_limited: rateLimit.count,
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
    };
  }, intentOpts));
  intent.hover_only.push(await withPage(async (page, network, rateLimit) => {
    await installLabRoutes(page);
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    await waitReady(page, '/');
    const beforePaid = network.abortedPaid;
    await desktopEarningsNav(page).hover();
    await page.waitForTimeout(800);
    return {
      earnings_chunk: network.earningsChunk > 0,
      chart_loaded: network.chart > 0,
      extra_paid: network.abortedPaid > beforePaid,
      rate_limited: rateLimit.count,
    };
  }, intentOpts));
  console.log(
    `intent #${i + 1} immediate=${intent.immediate.at(-1).wall_ms} `
    + `hover=${intent.hover_then_click.at(-1).wall_ms} `
    + `hover_only_chunk=${intent.hover_only.at(-1).earnings_chunk}`,
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
    await page.evaluate(() => window.scrollBy(0, Math.round(window.innerHeight * 0.9)));
    await page.waitForTimeout(1200);
    const afterNear = network.chart;
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(400);
    await page.evaluate(() => window.scrollBy(0, Math.round(window.innerHeight * 0.9)));
    await page.waitForTimeout(400);
    return {
      placeholder_top: slot?.top ?? null,
      placeholder_height: slot?.height ?? null,
      chart_before_scroll: before > 0,
      chart_after_near_scroll: afterNear > 0,
      chart_stayed_mounted: network.chart > 0 && afterNear > 0,
      rate_limited: rateLimit.count,
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
      n: intent.hover_only.length,
      chunk_n: intent.hover_only.filter((row) => row.earnings_chunk).length,
      chart_n: intent.hover_only.filter((row) => row.chart_loaded).length,
      extra_paid_n: intent.hover_only.filter((row) => row.extra_paid).length,
      samples: intent.hover_only,
    },
  },
  earnings_scroll: {
    n: scroll.length,
    chart_before_n: scroll.filter((row) => row.chart_before_scroll).length,
    chart_after_n: scroll.filter((row) => row.chart_after_near_scroll).length,
    stayed_n: scroll.filter((row) => row.chart_stayed_mounted).length,
    samples: scroll,
  },
};
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
