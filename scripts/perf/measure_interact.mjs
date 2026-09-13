#!/usr/bin/env node
/**
 * Laboratory interaction timings on a loaded news page.
 * Records click-to-drawer, window filter, and scroll long-tasks.
 * Not real-user INP; do not label as INP.
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
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/browser-interact.json';
const REPEATS = Number(process.env.OPTIX_PERF_REPEATS || 20);

function percentile(values, q) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * q)))];
}

async function applyThrottle(page) {
  const client = await page.context().newCDPSession(page);
  await client.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await client.send('Network.enable');
  await client.send('Network.emulateNetworkConditions', {
    offline: false,
    downloadThroughput: (10 * 1024 * 1024) / 8,
    uploadThroughput: (2 * 1024 * 1024) / 8,
    latency: 180,
  });
}

async function waitNews(page) {
  await page.waitForFunction(() => {
    const title = document.querySelector('article h3');
    return !!(title && title.textContent && title.textContent.trim().length > 1);
  }, null, { timeout: 120_000 });
}

const samples = [];
for (let i = 0; i < REPEATS; i += 1) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  await applyThrottle(page);
  await page.goto(`${BASE}/catalysts`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  await waitNews(page);

  const drawerStarted = await page.evaluate(() => performance.now());
  await page.locator('article').first().locator('h3').click({ force: true });
  await page.waitForFunction(() => {
    const dialog = document.querySelector('[role="dialog"][aria-modal="true"]');
    const heading = dialog?.querySelector('h2');
    const text = heading?.textContent?.trim() || '';
    return text.length > 2;
  }, null, { timeout: 30_000 });
  const drawerReady = await page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"][aria-modal="true"]');
    const heading = dialog?.querySelector('h2');
    const text = heading?.textContent?.trim() || '';
    return { at: performance.now(), title: text };
  });
  const drawerMs = drawerReady.at - drawerStarted;

  await page.keyboard.press('Escape').catch(() => {});
  await page.waitForTimeout(200);

  await page.getByRole('button', { name: '筛选' }).click();
  const filterStarted = await page.evaluate(() => performance.now());
  const feedWait = page.waitForResponse(
    (response) => response.url().includes('window_hours=24') && response.ok(),
    { timeout: 60_000 },
  );
  await page.getByRole('tab', { name: '24 时' }).click();
  await feedWait;
  await page.waitForFunction(() => {
    const title = document.querySelector('article h3');
    return !!(title && title.textContent && title.textContent.trim().length > 1);
  }, null, { timeout: 60_000 });
  const filterReady = await page.evaluate(() => performance.now());
  const filterMs = filterReady - filterStarted;
  const filterTitle = await page.locator('article h3').first().textContent();

  const longBefore = await page.evaluate(() => {
    window.__scrollLong = [];
    try {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__scrollLong.push(entry.duration);
        }
      }).observe({ type: 'longtask', buffered: false });
    } catch { /* optional */ }
    return performance.now();
  });
  for (let step = 0; step < 12; step += 1) {
    await page.mouse.wheel(0, 400);
    await page.waitForTimeout(50);
  }
  const scroll = await page.evaluate((started) => ({
    elapsed: performance.now() - started,
    longTasks: window.__scrollLong || [],
    overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
  }), longBefore);

  samples.push({
    drawer_ms: drawerMs,
    drawer_title: drawerReady.title,
    filter_ms: filterMs,
    filter_title: filterTitle?.trim() || null,
    scroll_ms: scroll.elapsed,
    scroll_longtask_count: scroll.longTasks.length,
    scroll_longtask_total_ms: scroll.longTasks.reduce((sum, ms) => sum + ms, 0),
    horizontal_overflow: scroll.overflowX,
  });
  console.log(
    `#${i + 1}/${REPEATS} drawer=${drawerMs.toFixed(0)} filter=${filterMs.toFixed(0)} `
    + `scroll_long=${scroll.longTasks.reduce((sum, ms) => sum + ms, 0).toFixed(0)}`,
  );
  await context.close();
  await browser.close();
}

const drawer = samples.map((s) => s.drawer_ms);
const filter = samples.map((s) => s.filter_ms);
const report = {
  lab: true,
  notINP: true,
  measuredAt: new Date().toISOString(),
  n: samples.length,
  summary: {
    drawer_p50: percentile(drawer, 0.5),
    drawer_p75: percentile(drawer, 0.75),
    filter_p50: percentile(filter, 0.5),
    filter_p75: percentile(filter, 0.75),
    scroll_longtask_total_p75: percentile(samples.map((s) => s.scroll_longtask_total_ms), 0.75),
    horizontal_overflow_any: samples.some((s) => s.horizontal_overflow),
  },
  samples,
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report.summary, null, 2));
console.log(`wrote ${OUT}`);
