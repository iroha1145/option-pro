#!/usr/bin/env node
/**
 * In-app SPA navigation timing (not full document reload).
 * Cold pair first paints /catalysts via goto, then Home click, then in-app
 * back to /catalysts. Warm here means client memory + HTTP cache, same tab.
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
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/browser-spa.json';
const REPEATS = Number(process.env.OPTIX_PERF_REPEATS || 20);

function percentile(values, q) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * q)))];
}

async function waitNews(page) {
  await page.waitForFunction(() => {
    const title = document.querySelector('article h3');
    const text = title?.textContent?.trim() || '';
    return text.length > 1;
  }, null, { timeout: 120_000 });
  return page.evaluate(() => {
    const title = document.querySelector('article h3');
    return { title: title?.textContent?.trim() || '', at: performance.now() };
  });
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
  const client = await page.context().newCDPSession(page);
  await client.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await client.send('Network.enable');
  await client.send('Network.emulateNetworkConditions', {
    offline: false,
    downloadThroughput: (10 * 1024 * 1024) / 8,
    uploadThroughput: (2 * 1024 * 1024) / 8,
    latency: 180,
  });
  const coldStart = Date.now();
  await page.goto(`${BASE}/catalysts`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
  const cold = await waitNews(page);
  const homeLink = page.getByRole('link', { name: '首页' }).first();
  if (await homeLink.count()) {
    await homeLink.click({ timeout: 15_000 });
  } else {
    await page.locator('a[href="/"]').first().click({ timeout: 15_000 });
  }
  await page.waitForFunction(() => location.pathname === '/' || document.querySelector('h1')?.textContent?.includes('首页'), null, { timeout: 30_000 });
  const spaStart = await page.evaluate(() => performance.now());
  const newsLink = page.getByRole('link', { name: /新闻催化|^催化$/ }).first();
  if (await newsLink.count()) {
    await newsLink.click({ timeout: 15_000 });
  } else {
    await page.locator('a[href="/catalysts"]').first().click({ timeout: 15_000 });
  }
  const spa = await waitNews(page);
  samples.push({
    cold_ready_ms: Date.now() - coldStart,
    cold_title: cold.title,
    spa_ready_ms: spa.at - spaStart,
    spa_title: spa.title,
  });
  console.log(`#${i + 1}/${REPEATS} cold=${samples.at(-1).cold_ready_ms} spa=${samples.at(-1).spa_ready_ms.toFixed(0)} ${spa.title}`);
  await context.close();
  await browser.close();
}

const spa = samples.map((s) => s.spa_ready_ms);
const report = {
  lab: true,
  measuredAt: new Date().toISOString(),
  n: samples.length,
  summary: {
    spa_p50: percentile(spa, 0.5),
    spa_p75: percentile(spa, 0.75),
  },
  samples,
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report.summary, null, 2));
console.log(`wrote ${OUT}`);
