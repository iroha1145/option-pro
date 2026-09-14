#!/usr/bin/env node
/**
 * Laboratory first-content timings for non-news routes.
 * Samples keep their class: shell / empty / error / content. Content and
 * error timings are never mixed into one "ready" distribution.
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
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/browser-pages.json';
const REPEATS = Number(process.env.OPTIX_PERF_REPEATS || 8);
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
};
const profile = PROFILES[PROFILE];
if (!profile) throw new Error(`unknown profile ${PROFILE}`);

const ONLY = process.env.OPTIX_PERF_ROUTE;
const ALL_ROUTES = [
  '/',
  '/watchlist',
  '/screener',
  '/market',
  '/breakouts',
  '/earnings',
  '/sectors',
  '/login',
  '/cta',
  '/stock/NVDA',
  '/this-page-is-not-a-route',
];
const ROUTES = ONLY ? ALL_ROUTES.filter((route) => route === ONLY) : ALL_ROUTES;
if (!ROUTES.length) throw new Error(`unknown OPTIX_PERF_ROUTE=${ONLY}`);

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

function summarize(samples) {
  const byClass = (kind) => samples.filter((row) => row.ready_class === kind).map((row) => row.ready_ms).filter((ms) => ms != null);
  const content = byClass('content');
  const empty = byClass('empty');
  const idle = byClass('idle');
  const error = byClass('error');
  const timeout = samples.filter((row) => row.ready_class === 'timeout');
  return {
    n: samples.length,
    content_n: content.length,
    empty_n: empty.length,
    idle_n: idle.length,
    error_n: error.length,
    timeout_n: timeout.length,
    content_rate: samples.length ? content.length / samples.length : 0,
    empty_rate: samples.length ? empty.length / samples.length : 0,
    idle_rate: samples.length ? idle.length / samples.length : 0,
    error_rate: samples.length ? error.length / samples.length : 0,
    data_rate: samples.length ? (content.length + empty.length) / samples.length : 0,
    success_rate: samples.length ? (content.length + empty.length + idle.length) / samples.length : 0,
    content_p50: percentile(content, 0.5),
    content_p75: percentile(content, 0.75),
    empty_p50: percentile(empty, 0.5),
    empty_p75: percentile(empty, 0.75),
    idle_p50: percentile(idle, 0.5),
    idle_p75: percentile(idle, 0.75),
    error_p50: percentile(error, 0.5),
    error_p75: percentile(error, 0.75),
    rate_limited_n: samples.filter((row) => (row.rate_limited || 0) > 0).length,
    samples,
  };
}

const results = {};
for (const route of ROUTES) {
  const samples = [];
  for (let i = 0; i < REPEATS; i += 1) {
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: profile.width, height: profile.height },
      deviceScaleFactor: profile.dpr,
      isMobile: profile.mobile,
      hasTouch: profile.mobile,
      locale: 'zh-CN',
    });
    const page = await context.newPage();
    await page.addInitScript({ content: `${pageReadyInstallScript()}; try { localStorage.setItem('optix:locale', 'zh'); } catch (e) {}` });
    const rateLimit = attach429Counter(page);
    await applyThrottle(page);
    const started = Date.now();
    await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    const ready = await page.waitForFunction((path) => {
      const kind = window.__optixPageReadyClass(path);
      if (kind === 'content' || kind === 'empty' || kind === 'error' || kind === 'idle') {
        return { kind, at: performance.now() };
      }
      return false;
    }, route, { timeout: 60_000 }).then(async (handle) => handle.jsonValue()).catch(() => null);
    const kind = ready?.kind && isTerminalReady(ready.kind) ? ready.kind : 'timeout';
    samples.push({
      wall_ms: Date.now() - started,
      ready_ms: ready?.at ?? null,
      ready_class: kind,
      ready_ok: kind === 'content' || kind === 'empty' || kind === 'idle',
      rate_limited: rateLimit.count,
    });
    await context.close();
    await browser.close();
    await afterSampleGap({ rateLimitedCount: rateLimit.count, last: i + 1 >= REPEATS });
  }
  results[route] = summarize(samples);
  const row = results[route];
  console.log(`${route} content_p75=${row.content_p75} content=${row.content_n}/${row.n} empty=${row.empty_n} idle=${row.idle_n} error=${row.error_n} timeout=${row.timeout_n}`);
}

const report = {
  lab: true,
  profile: PROFILE,
  measuredAt: new Date().toISOString(),
  ready_semantics: 'content/empty/idle/error/timeout; content_p75 excludes idle, error and timeout; idle is unscanned, not a loaded empty result',
  routes: results,
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(`wrote ${OUT}`);
