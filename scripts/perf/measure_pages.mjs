#!/usr/bin/env node
/**
 * Laboratory first-content timings for non-news routes.
 * Ready means a real heading or primary landmark, not a skeleton block alone.
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
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/browser-pages.json';
const REPEATS = Number(process.env.OPTIX_PERF_REPEATS || 8);
const PROFILE = process.env.OPTIX_PERF_PROFILE || 'mobile-ref';

const PROFILES = {
  desktop: { width: 1440, height: 900, dpr: 1, cpu: 1, down: 0, up: 0, rtt: 0, mobile: false },
  'mobile-ref': {
    width: 390, height: 844, dpr: 3, cpu: 4,
    down: (10 * 1024 * 1024) / 8, up: (2 * 1024 * 1024) / 8, rtt: 180, mobile: true,
  },
};
const profile = PROFILES[PROFILE];

const ROUTES = [
  { path: '/', ready: () => !!document.querySelector('h1, h2, [data-page]') },
  { path: '/watchlist', ready: () => !!document.querySelector('h1, table, [class*="empty"]') },
  { path: '/screener', ready: () => !!document.querySelector('h1, button, form') },
  { path: '/market', ready: () => !!document.querySelector('h1, h2') },
  { path: '/breakouts', ready: () => !!document.querySelector('h1, h2') },
  { path: '/earnings', ready: () => !!document.querySelector('h1, h2, table') },
  { path: '/sectors', ready: () => !!document.querySelector('h1, h2') },
  { path: '/login', ready: () => !!document.querySelector('form, input[type="password"]') },
];

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
    await applyThrottle(page);
    const started = Date.now();
    await page.goto(`${BASE}${route.path}`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    const ready = await page.waitForFunction(route.ready, null, { timeout: 60_000 }).then(async (handle) => {
      const ok = await handle.jsonValue();
      return { ok, at: await page.evaluate(() => performance.now()) };
    }).catch(() => ({ ok: false, at: null }));
    samples.push({
      wall_ms: Date.now() - started,
      ready_ms: ready.at,
      ready_ok: !!ready.ok,
    });
    await context.close();
    await browser.close();
  }
  const ready = samples.filter((s) => s.ready_ms != null).map((s) => s.ready_ms);
  results[route.path] = {
    n: samples.length,
    ready_n: ready.length,
    ready_p50: percentile(ready, 0.5),
    ready_p75: percentile(ready, 0.75),
    samples,
  };
  console.log(`${route.path} p50=${results[route.path].ready_p50} p75=${results[route.path].ready_p75} ready_n=${ready.length}/${samples.length}`);
}

const report = {
  lab: true,
  profile: PROFILE,
  measuredAt: new Date().toISOString(),
  routes: results,
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(`wrote ${OUT}`);
