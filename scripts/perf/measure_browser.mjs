#!/usr/bin/env node
/**
 * Laboratory browser timings against the production SPA + real backend.
 * Skeleton/spinner is not treated as ready. news_content_ready is the first
 * real news title that is visible and whose open control is enabled.
 *
 * Cold = new browser context (empty HTTP cache).
 * Warm = same context, navigate away then back (HTTP + JS cache).
 * Network/CPU throttling is applied once via CDP, not stacked with tc.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(fileURLToPath(import.meta.url));
const { chromium } = require(path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../frontend-src/node_modules/playwright',
));

const BASE = process.env.OPTIX_PERF_BASE || 'http://127.0.0.1:2000';
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/browser-baseline.json';
const PAIRS = Number(process.env.OPTIX_PERF_PAIRS || process.env.OPTIX_PERF_REPEATS || 20);
const PROFILE = process.env.OPTIX_PERF_PROFILE || 'mobile-ref';

const PROFILES = {
  desktop: { width: 1440, height: 900, dpr: 1, cpu: 1, down: 0, up: 0, rtt: 0, mobile: false },
  'mobile-ref': {
    width: 390, height: 844, dpr: 3, cpu: 4,
    down: (10 * 1024 * 1024) / 8, up: (2 * 1024 * 1024) / 8, rtt: 180, mobile: true,
  },
  'mobile-360': {
    width: 360, height: 800, dpr: 3, cpu: 4,
    down: (10 * 1024 * 1024) / 8, up: (2 * 1024 * 1024) / 8, rtt: 180, mobile: true,
  },
  'mobile-430': {
    width: 430, height: 932, dpr: 3, cpu: 4,
    down: (10 * 1024 * 1024) / 8, up: (2 * 1024 * 1024) / 8, rtt: 180, mobile: true,
  },
  'mobile-weak': {
    width: 390, height: 844, dpr: 3, cpu: 6,
    down: (1.6 * 1024 * 1024) / 8, up: (0.75 * 1024 * 1024) / 8, rtt: 300, mobile: true,
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
  if (profile.cpu > 1) {
    await client.send('Emulation.setCPUThrottlingRate', { rate: profile.cpu });
  }
  if (profile.down > 0) {
    await client.send('Network.enable');
    await client.send('Network.emulateNetworkConditions', {
      offline: false,
      downloadThroughput: profile.down,
      uploadThroughput: profile.up,
      latency: profile.rtt,
    });
  }
  return client;
}

async function prepareObservers(page) {
  await page.addInitScript(() => {
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
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__optixPerf.longTasks.push({ start: entry.startTime, duration: entry.duration });
        }
      }).observe({ type: 'longtask', buffered: true });
    } catch {
      /* observers optional in older engines */
    }
  });
}

async function measureNavigation(page, pathName) {
  const requests = [];
  const onRequest = (request) => {
    if (request.resourceType() === 'document' || request.resourceType() === 'script'
      || request.resourceType() === 'stylesheet' || request.url().includes('/api/')) {
      requests.push({ url: request.url(), type: request.resourceType(), started: Date.now() });
    }
  };
  page.on('request', onRequest);
  const started = Date.now();
  const response = await page.goto(`${BASE}${pathName}`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  const navStatus = response?.status() ?? null;
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
  }, null, { timeout: 120_000 }).catch(() => null);
  page.off('request', onRequest);
  const ready = readyHandle ? await readyHandle.jsonValue() : null;
  const metrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const paints = performance.getEntriesByType('paint');
    const resources = performance.getEntriesByType('resource');
    const observed = window.__optixPerf || {};
    return {
      ttfb: nav?.responseStart ?? null,
      dcl: nav?.domContentLoadedEventEnd ?? null,
      load: nav?.loadEventEnd ?? null,
      fp: paints.find((e) => e.name === 'first-paint')?.startTime ?? null,
      fcp: paints.find((e) => e.name === 'first-contentful-paint')?.startTime ?? null,
      lcp: observed.lcp || null,
      cls: observed.cls ?? 0,
      resourceCount: resources.length,
      transferSize: resources.reduce((sum, e) => sum + (e.transferSize || 0), 0),
      encodedBodySize: resources.reduce((sum, e) => sum + (e.encodedBodySize || 0), 0),
      longTaskCount: (observed.longTasks || []).length,
      longTaskTotalMs: (observed.longTasks || []).reduce((sum, e) => sum + e.duration, 0),
      domNodes: document.getElementsByTagName('*').length,
      apiPaths: resources
        .filter((e) => String(e.name).includes('/api/'))
        .map((e) => ({ name: e.name.replace(/^https?:\/\/[^/]+/, ''), duration: e.duration, transferSize: e.transferSize })),
    };
  });
  return {
    navStatus,
    wall_ms: Date.now() - started,
    news_content_ready_ms: ready?.at ?? null,
    news_title: ready?.title ?? null,
    request_count_tracked: requests.length,
    ...metrics,
  };
}

async function collectPair() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: profile.width, height: profile.height },
    deviceScaleFactor: profile.dpr,
    isMobile: profile.mobile,
    hasTouch: profile.mobile,
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  await applyThrottle(page);
  await prepareObservers(page);
  const cold = await measureNavigation(page, '/catalysts');
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 60_000 }).catch(() => null);
  const warm = await measureNavigation(page, '/catalysts');
  await context.close();
  await browser.close();
  return { cold: { cache: 'cold', ...cold }, warm: { cache: 'warm', ...warm } };
}

const samples = [];
for (let i = 0; i < PAIRS; i += 1) {
  const pair = await collectPair();
  samples.push(pair);
  console.log(
    `#${i + 1}/${PAIRS} cold_ready=${pair.cold.news_content_ready_ms} warm_ready=${pair.warm.news_content_ready_ms} `
    + `cold_lcp=${pair.cold.lcp?.startTime ?? null} warm_lcp=${pair.warm.lcp?.startTime ?? null} `
    + `cold_cls=${pair.cold.cls} warm_cls=${pair.warm.cls}`,
  );
}

const flat = (side) => samples.map((row) => row[side]);
function summarize(rows) {
  const ready = rows.filter((s) => s.news_content_ready_ms != null).map((s) => s.news_content_ready_ms);
  const lcp = rows.filter((s) => s.lcp?.startTime != null).map((s) => s.lcp.startTime);
  const cls = rows.map((s) => s.cls ?? 0);
  return {
    n: rows.length,
    ready_n: ready.length,
    news_content_ready_p50: percentile(ready, 0.5),
    news_content_ready_p75: percentile(ready, 0.75),
    news_content_ready_min: ready.length ? Math.min(...ready) : null,
    news_content_ready_max: ready.length ? Math.max(...ready) : null,
    lcp_p75: percentile(lcp, 0.75),
    cls_p75: percentile(cls, 0.75),
    transfer_p50: percentile(rows.map((s) => s.transferSize || 0), 0.5),
    longtask_total_p75: percentile(rows.map((s) => s.longTaskTotalMs || 0), 0.75),
  };
}

const report = {
  profile: PROFILE,
  profileSpec: profile,
  base: BASE,
  measuredAt: new Date().toISOString(),
  lab: true,
  notRUM: true,
  pairs: samples.length,
  summary: { cold: summarize(flat('cold')), warm: summarize(flat('warm')) },
  samples,
};

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report.summary, null, 2));
console.log(`wrote ${OUT}`);
