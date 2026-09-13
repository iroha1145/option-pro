#!/usr/bin/env node
/**
 * Laboratory first-content timings for non-news routes.
 * Ready means a real heading or primary landmark, not a skeleton block alone.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { afterSampleGap, attach429Counter } from './lib/rate_limit.mjs';

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
  { path: '/', ready: () => document.querySelector('h1')?.textContent?.includes('首页') && (document.querySelector('main')?.innerText.length || 0) > 80 },
  { path: '/watchlist', ready: () => (document.querySelector('h1')?.textContent?.includes('自选') ?? false) && (!!document.querySelector('table') || /暂无|空|还没有/.test(document.body.innerText)) },
  { path: '/screener', ready: () => (document.querySelector('h1')?.textContent?.includes('选股') ?? false) && !!document.querySelector('button, form, input') },
  { path: '/market', ready: () => (document.querySelector('h1')?.textContent?.includes('大盘') ?? false) && (document.querySelector('main')?.innerText.length || 0) > 80 },
  { path: '/breakouts', ready: () => /突破|雷达/.test(document.querySelector('h1')?.textContent || '') && (document.querySelector('main')?.innerText.length || 0) > 40 },
  { path: '/earnings', ready: () => (document.querySelector('h1')?.textContent?.includes('财报') ?? false) && (document.querySelector('table') || (document.querySelector('main')?.innerText.length || 0) > 40) },
  { path: '/sectors', ready: () => (document.querySelector('h1')?.textContent?.includes('板块') ?? false) && (document.querySelector('main')?.innerText.length || 0) > 40 },
  { path: '/login', ready: () => !!document.querySelector('form, input[type="password"]') || /已登录|管理员/.test(document.body.innerText) },
  { path: '/cta', ready: () => {
    const heading = document.querySelector('h1')?.textContent || '';
    if (!/CTA|趋势资金/.test(heading)) return false;
    const text = document.body.innerText || '';
    return /CTA 估算尚未生成|CTA 估算读取失败|暂无数据/.test(text) || (document.querySelector('main')?.innerText.length || 0) > 80;
  } },
  { path: '/stock/NVDA', ready: () => {
    if (document.querySelector('[aria-busy="true"]')) return false;
    const text = document.body.innerText || '';
    return /该标的暂无完整数据|代码不存在|行情服务暂不可用|请求较频繁|登录状态已失效|该股票暂无数据/.test(text)
      || (/NVDA/.test(text) && text.length > 80 && !/skeleton/i.test(document.body.className));
  } },
  { path: '/this-page-is-not-a-route', ready: () => /页面不存在/.test(document.body.innerText || '') },
];
const ROUTES = ONLY ? ALL_ROUTES.filter((route) => route.path === ONLY) : ALL_ROUTES;
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
    const rateLimit = attach429Counter(page);
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
      rate_limited: rateLimit.count,
    });
    await context.close();
    await browser.close();
    await afterSampleGap({ rateLimitedCount: rateLimit.count, last: i + 1 >= REPEATS });
  }
  const ready = samples.filter((s) => s.ready_ms != null).map((s) => s.ready_ms);
  results[route.path] = {
    n: samples.length,
    ready_n: ready.length,
    ready_p50: percentile(ready, 0.5),
    ready_p75: percentile(ready, 0.75),
    rate_limited_n: samples.filter((s) => (s.rate_limited || 0) > 0).length,
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
