#!/usr/bin/env node
/**
 * Laboratory fault injection on the news page.
 * Uses Playwright route interception — does not hit paid upstreams.
 * Asserts error UI and recovery, not just HTTP status.
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
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/faults.json';
const FEED = /\/api\/catalysts\/feed/;

async function waitNews(page) {
  await page.waitForFunction(() => {
    const title = document.querySelector('article h3');
    return !!(title && title.textContent && title.textContent.trim().length > 1);
  }, null, { timeout: 120_000 });
  return page.locator('article h3').first().innerText();
}

async function waitError(page) {
  await page.waitForFunction(() => /加载失败|新闻暂不可用/.test(document.body.innerText || ''), null, { timeout: 30_000 });
}

async function clickRefresh(page) {
  const refresh = page.getByRole('button', { name: /^刷新$/ });
  if (await refresh.count()) {
    await refresh.first().click();
    return;
  }
  const retry = page.getByRole('button', { name: /重试/ });
  if (await retry.count()) await retry.first().click();
}

async function withPage(run) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  try {
    return await run(page);
  } finally {
    await context.close();
    await browser.close();
  }
}

const cases = [];

cases.push(await withPage(async (page) => {
  const started = Date.now();
  await page.goto(`${BASE}/catalysts`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  const title = await waitNews(page);
  await page.route(FEED, (route) => route.abort('internetdisconnected'));
  await clickRefresh(page);
  await waitError(page);
  await page.unroute(FEED);
  await clickRefresh(page);
  const recovered = await waitNews(page);
  return {
    name: 'feed_disconnect_then_retry',
    ok: recovered.length > 1,
    title,
    recovered,
    ms: Date.now() - started,
  };
}));

cases.push(await withPage(async (page) => {
  const started = Date.now();
  await page.goto(`${BASE}/catalysts`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  const title = await waitNews(page);
  await page.route(FEED, (route) => route.fulfill({
    status: 429,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'rate limited' }),
    headers: { 'retry-after': '1' },
  }));
  await clickRefresh(page);
  await waitError(page);
  await page.unroute(FEED);
  await clickRefresh(page);
  const recovered = await waitNews(page);
  return {
    name: 'feed_429_then_retry',
    ok: recovered.length > 1,
    title,
    recovered,
    ms: Date.now() - started,
  };
}));

cases.push(await withPage(async (page) => {
  const started = Date.now();
  let delayed = 0;
  await page.route(FEED, async (route) => {
    delayed += 1;
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await route.continue();
  });
  await page.goto(`${BASE}/catalysts`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  const title = await waitNews(page);
  await page.unroute(FEED);
  return {
    name: 'feed_delay_2500ms',
    ok: title.length > 1,
    title,
    delayed,
    ms: Date.now() - started,
  };
}));

cases.push(await withPage(async (page) => {
  const started = Date.now();
  await page.goto(`${BASE}/catalysts`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  const title = await waitNews(page);
  await page.evaluate(async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => caches.delete(key)));
  }).catch(() => {});
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 120_000 });
  const recovered = await waitNews(page);
  return {
    name: 'reload_after_cache_clear',
    ok: recovered.length > 1,
    title,
    recovered,
    ms: Date.now() - started,
  };
}));

const report = {
  lab: true,
  notPaidUpstream: true,
  measuredAt: new Date().toISOString(),
  base: BASE,
  ok: cases.every((row) => row.ok),
  cases,
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
console.log(`wrote ${OUT}`);
if (!report.ok) process.exit(1);
