#!/usr/bin/env node
/**
 * Interleaved optimized vs unoptimized news-page pairs.
 * Same profile, same machine. Alternates which base goes first each pair.
 * Never compare a cold sample to a warm sample across versions.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { afterSampleGap, attach429Counter, sleep, REPEAT_GAP_MS } from './lib/rate_limit.mjs';

const require = createRequire(fileURLToPath(import.meta.url));
const { chromium } = require(path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../frontend-src/node_modules/playwright',
));

const OPT = process.env.OPTIX_PERF_OPT_BASE || 'http://127.0.0.1:2000';
const UNOPT = process.env.OPTIX_PERF_UNOPT_BASE || 'http://127.0.0.1:2001';
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/browser-interleaved-mobile-ref.json';
const PAIRS = Number(process.env.OPTIX_PERF_PAIRS || 20);
const PROFILE = process.env.OPTIX_PERF_PROFILE || 'mobile-ref';

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

async function measureNavigation(page, base, pathName) {
  const started = Date.now();
  await page.goto(`${base}${pathName}`, { waitUntil: 'domcontentloaded', timeout: 120_000 });
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
    const observed = window.__optixPerf || {};
    return {
      lcp: observed.lcp || null,
      cls: observed.cls ?? 0,
    };
  });
  return {
    wall_ms: Date.now() - started,
    news_content_ready_ms: ready?.at ?? null,
    news_title: ready?.title ?? null,
    ...metrics,
  };
}

async function collectPair(base) {
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
  await prepareObservers(page);
  const cold = await measureNavigation(page, base, '/catalysts');
  const coldLimited = rateLimit.count;
  await page.goto(`${base}/`, { waitUntil: 'domcontentloaded', timeout: 60_000 }).catch(() => null);
  const warm = await measureNavigation(page, base, '/catalysts');
  await context.close();
  await browser.close();
  return {
    cold: { cache: 'cold', rate_limited: coldLimited, ...cold },
    warm: { cache: 'warm', rate_limited: Math.max(0, rateLimit.count - coldLimited), ...warm },
    rate_limited: rateLimit.count,
  };
}

function summarize(rows) {
  const ready = rows.filter((s) => s.news_content_ready_ms != null).map((s) => s.news_content_ready_ms);
  const lcp = rows.filter((s) => s.lcp?.startTime != null).map((s) => s.lcp.startTime);
  return {
    n: rows.length,
    ready_n: ready.length,
    news_content_ready_p50: percentile(ready, 0.5),
    news_content_ready_p75: percentile(ready, 0.75),
    lcp_p75: percentile(lcp, 0.75),
    titles: [...new Set(rows.map((s) => s.news_title).filter(Boolean))],
    rate_limited_n: rows.filter((s) => (s.rate_limited || 0) > 0).length,
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
    + `rate_limited=${row.rate_limited}`,
  );
  await afterSampleGap({ rateLimitedCount: row.rate_limited, last: i + 1 >= PAIRS });
}

const optCold = samples.map((s) => s.opt.cold);
const unoptCold = samples.map((s) => s.unopt.cold);
const optWarm = samples.map((s) => s.opt.warm);
const unoptWarm = samples.map((s) => s.unopt.warm);
const report = {
  lab: true,
  interleaved: true,
  notRUM: true,
  profile: PROFILE,
  optBase: OPT,
  unoptBase: UNOPT,
  measuredAt: new Date().toISOString(),
  pairs: samples.length,
  summary: {
    opt: { cold: summarize(optCold), warm: summarize(optWarm) },
    unopt: { cold: summarize(unoptCold), warm: summarize(unoptWarm) },
    delta_cold_p75: (summarize(optCold).news_content_ready_p75 ?? 0) - (summarize(unoptCold).news_content_ready_p75 ?? 0),
    delta_warm_p75: (summarize(optWarm).news_content_ready_p75 ?? 0) - (summarize(unoptWarm).news_content_ready_p75 ?? 0),
  },
  samples,
};
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report.summary, null, 2));
console.log(`wrote ${OUT}`);
