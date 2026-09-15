#!/usr/bin/env node
/**
 * Round-6 interleaved news-page comparison.
 * Cold is only compared to cold; warm only to warm.
 * Records first operable news title, transfer, feed hops, 429s, timeouts.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { afterSampleGap, attach429Counter, sleep, REPEAT_GAP_MS } from './lib/rate_limit.mjs';
import { buildInterleavedSummary, interleavedExitCode, interleavedGateFailures } from './lib/interleaved_summary.mjs';

const require = createRequire(fileURLToPath(import.meta.url));
const { chromium } = require(path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../frontend-src/node_modules/playwright',
));

const OPT = process.env.OPTIX_PERF_OPT_BASE || 'http://127.0.0.1:2000';
const UNOPT = process.env.OPTIX_PERF_UNOPT_BASE || 'http://127.0.0.1:2001';
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/round6-interleaved-mobile-ref.json';
const PAIRS = Number(process.env.OPTIX_PERF_PAIRS || 20);
const PROFILE = process.env.OPTIX_PERF_PROFILE || 'mobile-ref';
const COLD_BUDGET_MS = Number(process.env.OPTIX_PERF_COLD_BUDGET_MS || 2500);
const WARM_BUDGET_MS = Number(process.env.OPTIX_PERF_WARM_BUDGET_MS || 1000);

const PROFILES = {
  desktop: { width: 1440, height: 900, dpr: 1, cpu: 1, down: 0, up: 0, rtt: 0, mobile: false },
  'mobile-ref': {
    width: 390, height: 844, dpr: 3, cpu: 4,
    down: (10 * 1024 * 1024) / 8, up: (2 * 1024 * 1024) / 8, rtt: 180, mobile: true,
  },
};
const profile = PROFILES[PROFILE];
if (!profile) throw new Error(`unknown profile ${PROFILE}`);

function percentile(values, q) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * q)))];
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

async function prepareObservers(page) {
  await page.addInitScript(() => {
    try { localStorage.setItem('optix:locale', 'zh'); } catch { /* ignore */ }
    window.__optixPerf = { lcp: null, cls: 0, longTasks: [] };
    try {
      new PerformanceObserver((list) => {
        const last = list.getEntries().at(-1);
        if (last) window.__optixPerf.lcp = { startTime: last.startTime, size: last.size, url: last.url || null };
      }).observe({ type: 'largest-contentful-paint', buffered: true });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) window.__optixPerf.cls += entry.value;
        }
      }).observe({ type: 'layout-shift', buffered: true });
    } catch { /* optional */ }
  });
}

function attachNetwork(page) {
  const state = {
    requests: 0,
    feedUrls: [],
    scriptUrls: [],
    runtimeEn: 0,
    runtimeJa: 0,
    chart: 0,
    errors: 0,
    requestFailureUrls: [],
  };
  page.on('request', (request) => {
    state.requests += 1;
    const url = request.url();
    if (url.includes('/api/catalysts/feed')) state.feedUrls.push(url.replace(/^https?:\/\/[^/]+/, ''));
    if (url.includes('runtime-en')) state.runtimeEn += 1;
    if (url.includes('runtime-ja')) state.runtimeJa += 1;
    if (/\/assets\/(?:eps-chart|chart)-/.test(url) || url.includes('EpsHatchChart')) state.chart += 1;
    if (request.resourceType() === 'script') state.scriptUrls.push(url.replace(/^https?:\/\/[^/]+/, ''));
  });
  page.on('response', (response) => {
    if (response.status() >= 400) state.errors += 1;
  });
  page.on('requestfailed', (request) => {
    const errorText = request.failure()?.errorText || 'unknown';
    if (/ERR_ABORTED/i.test(errorText)) return;
    state.requestFailureUrls.push({
      url: request.url().replace(/^https?:\/\/[^/]+/, ''),
      error: errorText,
    });
  });
  return state;
}

async function measureNavigation(page, base, pathName, network) {
  const started = Date.now();
  const beforeFeeds = network.feedUrls.length;
  const beforeReq = network.requests;
  const beforeErr = network.errors;
  const beforeFailed = network.requestFailureUrls.length;
  await page.goto(`${base}${pathName}`, { waitUntil: 'domcontentloaded', timeout: 180_000 });
  const readyHandle = await page.waitForFunction(() => {
    const title = document.querySelector('article h3');
    if (!title) return false;
    const text = title.textContent?.trim() || '';
    if (!text || text.length < 2) return false;
    const article = title.closest('article');
    const button = article?.querySelector('button[aria-label]');
    const style = window.getComputedStyle(title);
    const visible = style.visibility !== 'hidden' && style.display !== 'none' && title.getClientRects().length > 0;
    const enabled = !button || !button.disabled;
    if (!visible || !enabled) return false;
    return { title: text, at: performance.now() };
  }, null, { timeout: 180_000 }).catch(() => null);
  const ready = readyHandle ? await readyHandle.jsonValue() : null;
  const metrics = await page.evaluate(() => {
    const observed = window.__optixPerf || {};
    const resources = performance.getEntriesByType('resource');
    return {
      lcp: observed.lcp || null,
      cls: observed.cls ?? 0,
      resourceCount: resources.length,
      transferSize: resources.reduce((sum, entry) => sum + (entry.transferSize || 0), 0),
      encodedBodySize: resources.reduce((sum, entry) => sum + (entry.encodedBodySize || 0), 0),
    };
  });
  const feedUrls = network.feedUrls.slice(beforeFeeds);
  return {
    wall_ms: Date.now() - started,
    news_content_ready_ms: ready?.at ?? null,
    news_title: ready?.title ?? null,
    feed_hops: feedUrls.length,
    feed_urls: feedUrls,
    request_count: network.requests - beforeReq,
    http_error_n: network.errors - beforeErr,
    request_failed_n: network.requestFailureUrls.length - beforeFailed,
    request_failures: network.requestFailureUrls.slice(beforeFailed),
    runtime_en: network.runtimeEn,
    runtime_ja: network.runtimeJa,
    ...metrics,
  };
}

async function collectPair(base) {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    viewport: { width: profile.width, height: profile.height },
    deviceScaleFactor: profile.dpr,
    isMobile: profile.mobile,
    hasTouch: profile.mobile,
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  const rateLimit = attach429Counter(page);
  const network = attachNetwork(page);
  await applyThrottle(page);
  await prepareObservers(page);
  const cold = await measureNavigation(page, base, '/catalysts', network);
  const coldLimited = rateLimit.count;
  await page.goto(`${base}/`, { waitUntil: 'domcontentloaded', timeout: 60_000 }).catch(() => null);
  const warm = await measureNavigation(page, base, '/catalysts', network);
  await context.close();
  await browser.close();
  return {
    cold: { cache: 'cold', rate_limited: coldLimited, ...cold },
    warm: { cache: 'warm', rate_limited: Math.max(0, rateLimit.count - coldLimited), ...warm },
    rate_limited: rateLimit.count,
  };
}

const samples = [];
for (let i = 0; i < PAIRS; i += 1) {
  const optFirst = i % 2 === 0;
  const firstBase = optFirst ? OPT : UNOPT;
  const secondBase = optFirst ? UNOPT : OPT;
  const firstLabel = optFirst ? 'opt' : 'unopt';
  const secondLabel = optFirst ? 'unopt' : 'opt';
  const first = await collectPair(firstBase);
  if (REPEAT_GAP_MS > 0) await sleep(REPEAT_GAP_MS);
  const second = await collectPair(secondBase);
  const row = {
    i: i + 1,
    first: firstLabel,
    [firstLabel]: { cold: first.cold, warm: first.warm },
    [secondLabel]: { cold: second.cold, warm: second.warm },
    rate_limited: first.rate_limited + second.rate_limited,
  };
  samples.push(row);
  console.log(
    `#${i + 1}/${PAIRS} first=${firstLabel} `
    + `opt_cold=${row.opt.cold.news_content_ready_ms} unopt_cold=${row.unopt.cold.news_content_ready_ms} `
    + `opt_warm=${row.opt.warm.news_content_ready_ms} unopt_warm=${row.unopt.warm.news_content_ready_ms} `
    + `opt_feeds=${row.opt.cold.feed_hops}/${row.opt.warm.feed_hops} `
    + `unopt_feeds=${row.unopt.cold.feed_hops}/${row.unopt.warm.feed_hops} `
    + `rate_limited=${row.rate_limited}`,
  );
  await afterSampleGap({ rateLimitedCount: row.rate_limited, last: i + 1 >= PAIRS });
}

const optCold = samples.map((s) => s.opt.cold);
const unoptCold = samples.map((s) => s.unopt.cold);
const optWarm = samples.map((s) => s.opt.warm);
const unoptWarm = samples.map((s) => s.unopt.warm);
const summary = buildInterleavedSummary({ optCold, optWarm, unoptCold, unoptWarm });
const gateFailures = interleavedGateFailures(summary, {
  coldBudgetMs: COLD_BUDGET_MS,
  warmBudgetMs: WARM_BUDGET_MS,
  requireNetworkTelemetry: true,
});
summary.opt.cold.transfer_p50 = percentile(optCold.map((s) => s.transferSize || 0), 0.5);
summary.opt.warm.transfer_p50 = percentile(optWarm.map((s) => s.transferSize || 0), 0.5);
summary.unopt.cold.transfer_p50 = percentile(unoptCold.map((s) => s.transferSize || 0), 0.5);
summary.unopt.warm.transfer_p50 = percentile(unoptWarm.map((s) => s.transferSize || 0), 0.5);
summary.opt.cold.feed_hops_p50 = percentile(optCold.map((s) => s.feed_hops || 0), 0.5);
summary.unopt.cold.feed_hops_p50 = percentile(unoptCold.map((s) => s.feed_hops || 0), 0.5);
summary.opt.cold.request_p50 = percentile(optCold.map((s) => s.request_count || 0), 0.5);
summary.unopt.cold.request_p50 = percentile(unoptCold.map((s) => s.request_count || 0), 0.5);

const report = {
  ok: gateFailures.length === 0,
  lab: true,
  interleaved: true,
  notRUM: true,
  identity: 'loopback-owner',
  profile: PROFILE,
  network_profile: {
    name: PROFILE,
    width: profile.width,
    height: profile.height,
    cpu: profile.cpu,
    down_bps: profile.down,
    up_bps: profile.up,
    rtt_ms: profile.rtt,
  },
  optBase: OPT,
  unoptBase: UNOPT,
  product_commit: process.env.OPTIX_PERF_PRODUCT_COMMIT || null,
  entry: process.env.OPTIX_PERF_ENTRY || null,
  measuredAt: new Date().toISOString(),
  pairs: samples.length,
  summary,
  gate: {
    ok: gateFailures.length === 0,
    cold_budget_ms: COLD_BUDGET_MS,
    warm_budget_ms: WARM_BUDGET_MS,
    failures: gateFailures,
  },
  samples,
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report.summary, null, 2));
console.log(`wrote ${OUT}`);
if (interleavedExitCode(summary, {
  coldBudgetMs: COLD_BUDGET_MS,
  warmBudgetMs: WARM_BUDGET_MS,
  requireNetworkTelemetry: true,
}) !== 0) {
  console.error(`interleaved gate failed: ${gateFailures.join('; ')}`);
  process.exitCode = 1;
}
