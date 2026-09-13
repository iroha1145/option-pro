#!/usr/bin/env node
/**
 * Laboratory browser timings against the production SPA + real backend.
 * Does not treat skeleton/spinner as ready. news_content_ready is the first
 * real news title that is visible and whose open control is enabled.
 */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const BASE = process.env.OPTIX_PERF_BASE || 'http://127.0.0.1:2000';
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/browser-baseline.json';
const REPEATS = Number(process.env.OPTIX_PERF_REPEATS || 20);
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

async function collectOnce({ cold }) {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' }).catch(
    () => chromium.launch({ headless: true }),
  );
  const context = await browser.newContext({
    viewport: { width: profile.width, height: profile.height },
    deviceScaleFactor: profile.dpr,
    isMobile: profile.mobile,
    hasTouch: profile.mobile,
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  await applyThrottle(page);
  if (cold) {
    await context.clearCookies();
  }

  const started = Date.now();
  const response = await page.goto(`${BASE}/catalysts`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
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
  const ready = readyHandle ? await readyHandle.jsonValue() : null;

  const metrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const paints = performance.getEntriesByType('paint');
    const lcp = performance.getEntriesByType('largest-contentful-paint').at(-1);
    const shifts = performance.getEntriesByType('layout-shift').filter((e) => !e.hadRecentInput);
    const resources = performance.getEntriesByType('resource');
    const longTasks = performance.getEntriesByType('longtask');
    return {
      ttfb: nav?.responseStart ?? null,
      dcl: nav?.domContentLoadedEventEnd ?? null,
      load: nav?.loadEventEnd ?? null,
      fp: paints.find((e) => e.name === 'first-paint')?.startTime ?? null,
      fcp: paints.find((e) => e.name === 'first-contentful-paint')?.startTime ?? null,
      lcp: lcp ? { startTime: lcp.startTime, size: lcp.size, url: lcp.url || null } : null,
      cls: shifts.reduce((sum, e) => sum + e.value, 0),
      resourceCount: resources.length,
      transferSize: resources.reduce((sum, e) => sum + (e.transferSize || 0), 0),
      encodedBodySize: resources.reduce((sum, e) => sum + (e.encodedBodySize || 0), 0),
      longTaskCount: longTasks.length,
      longTaskTotalMs: longTasks.reduce((sum, e) => sum + e.duration, 0),
      domNodes: document.getElementsByTagName('*').length,
    };
  });

  let clickDelayMs = null;
  if (ready) {
    const handle = page.locator('article h3').first();
    const clickStarted = Date.now();
    await handle.click({ timeout: 15_000 }).catch(() => null);
    clickDelayMs = Date.now() - clickStarted;
    await page.locator('[role="dialog"], aside, [data-news-drawer]').first().waitFor({ state: 'visible', timeout: 15_000 }).catch(() => null);
  }

  const wallMs = Date.now() - started;
  await context.close();
  await browser.close();
  return {
    cold,
    navStatus,
    wall_ms: wallMs,
    news_content_ready_ms: ready?.at ?? null,
    news_title: ready?.title ?? null,
    click_delay_ms: clickDelayMs,
    ...metrics,
  };
}

const samples = [];
for (let i = 0; i < REPEATS; i += 1) {
  const cold = i % 2 === 0;
  const row = await collectOnce({ cold });
  samples.push(row);
  console.log(
    `#${i + 1}/${REPEATS} cold=${cold} ready=${row.news_content_ready_ms} lcp=${row.lcp?.startTime ?? null} cls=${row.cls} status=${row.navStatus}`,
  );
}

const readyCold = samples.filter((s) => s.cold && s.news_content_ready_ms != null).map((s) => s.news_content_ready_ms);
const readyWarm = samples.filter((s) => !s.cold && s.news_content_ready_ms != null).map((s) => s.news_content_ready_ms);
const lcp = samples.filter((s) => s.lcp?.startTime != null).map((s) => s.lcp.startTime);
const cls = samples.map((s) => s.cls);

const report = {
  profile: PROFILE,
  profileSpec: profile,
  base: BASE,
  measuredAt: new Date().toISOString(),
  lab: true,
  notRUM: true,
  n: samples.length,
  summary: {
    news_content_ready_cold_p50: percentile(readyCold, 0.5),
    news_content_ready_cold_p75: percentile(readyCold, 0.75),
    news_content_ready_warm_p50: percentile(readyWarm, 0.5),
    news_content_ready_warm_p75: percentile(readyWarm, 0.75),
    lcp_p75: percentile(lcp, 0.75),
    cls_p75: percentile(cls, 0.75),
    ready_samples_cold: readyCold.length,
    ready_samples_warm: readyWarm.length,
  },
  samples,
};

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report.summary, null, 2));
console.log(`wrote ${OUT}`);
