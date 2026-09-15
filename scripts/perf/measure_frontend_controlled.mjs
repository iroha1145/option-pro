import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(ROOT, 'frontend-src/package.json'));
const { chromium } = require('playwright');
const pairs = Number(process.env.PAIRS || 20);
const current = process.env.CURRENT || 'http://127.0.0.1:3057';
const baseline = process.env.BASELINE || 'http://127.0.0.1:3055';
const out = process.env.OUT || path.join(ROOT, 'output/controlled-frontend.json');
if (!process.env.PRODUCT_COMMIT) throw new Error('PRODUCT_COMMIT is required for measured-source identity');
if (![current, baseline].every(base => ['127.0.0.1', 'localhost'].includes(new URL(base).hostname))) {
  throw new Error('This experiment requires local fixed-response servers');
}
await mkdir(path.dirname(out), { recursive: true });
const fixtureHash = createHash('sha256').update(await readFile(path.join(ROOT, 'docs/performance/artifacts/r7-controlled-fixtures.json'))).digest('hex');
const html = await readFile(path.join(ROOT, 'frontend/index.html'), 'utf8');
const entry = html.match(/<script[^>]*type="module"[^>]*src="([^"]+)"/)?.[1];
if (!entry) throw new Error('Production module entry missing');
const entryHash = createHash('sha256').update(await readFile(path.join(ROOT, 'frontend', entry.replace(/^\//, '')))).digest('hex');
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const samples = [];
const report = () => ({ lab: true, fixed_responses: true, measuredAt: new Date().toISOString(),
  environment: { browser: browser.version(), node: process.version, platform: os.platform(), release: os.release(), arch: os.arch() },
  product_commit: process.env.PRODUCT_COMMIT,
  baseline_commit: process.env.BASELINE_COMMIT || 'df1bd5d35e8128805d75291126341be06e38b1e6',
  entry, entry_sha256: entryHash, fixture_sha256: fixtureHash, fixture_as_of: '2026-09-15T07:00:00Z',
  current, baseline, profile: { width: 390, height: 844, cpu: 4, down: 1310720, up: 262144, rtt: 180 },
  pairs: samples.length, samples });
async function sample(base) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, locale: 'zh-CN' });
  const page = await context.newPage();
  // Keep native performance/navigation clocks and resource timing intact.
  // Playwright clock instrumentation persists across reloads and replaces
  // resource timing, so it cannot be used for this navigation benchmark.
  const cdp = await context.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, downloadThroughput: 1310720, uploadThroughput: 262144, latency: 180 });
  await page.addInitScript(() => {
    localStorage.setItem('optix:locale', 'zh');
    const perf = window.__controlledPerf = { ready: null, long: [], lcp: null };
    new PerformanceObserver(list => perf.long.push(...list.getEntries().map(e => ({ at: e.startTime, duration: e.duration })))).observe({ type: 'longtask', buffered: true });
    new PerformanceObserver(list => { const e = list.getEntries().at(-1); perf.lcp = e && { at: e.startTime, size: e.size }; }).observe({ type: 'largest-contentful-paint', buffered: true });
    const check = () => {
      if (location.pathname !== '/catalysts') { perf.ready = null; return; }
      if (perf.ready !== null) return;
      const h = document.querySelector('article h3');
      if (h?.textContent?.trim() !== '芯片企业发布最新进展') return;
      const style = getComputedStyle(h), article = h.closest('article');
      const button = article?.querySelector('button[aria-label]');
      if (h.getClientRects().length && style.display !== 'none' && style.visibility !== 'hidden' && !button?.disabled) perf.ready = performance.now();
    };
    new MutationObserver(check).observe(document, { subtree: true, childList: true, attributes: true });
  });
  const errors = [], failures = [], jsErrors = [];
  let phase = 'cold';
  page.on('response', r => { if (r.status() >= 400) errors.push({ url: r.url(), status: r.status() }); });
  page.on('requestfailed', r => { if (r.failure()?.errorText !== 'net::ERR_ABORTED') failures.push({ url: r.url(), error: r.failure()?.errorText, phase }); });
  page.on('pageerror', e => jsErrors.push(e.message));
  const ready = () => page.waitForFunction(() => window.__controlledPerf.ready !== null, null, { timeout: 45000 });
  const settle = async () => { await page.waitForLoadState('networkidle', { timeout: 45000 }); await page.waitForTimeout(500); };
  const metrics = () => page.evaluate(() => {
    const p = window.__controlledPerf;
    return { ready_ms: p.ready, navigation: performance.getEntriesByType('navigation')[0]?.toJSON(), long_tasks: p.long, lcp: p.lcp, resources: performance.getEntriesByType('resource').map(e => ({ name: new URL(e.name).pathname, initiator: e.initiatorType, start: e.startTime, duration: e.duration, transfer: e.transferSize, encoded: e.encodedBodySize })) };
  });
  try {
    await page.goto(base + '/catalysts', { waitUntil: 'domcontentloaded' }); await ready(); await settle();
    const cold = await metrics();
    phase = 'warm_direct';
    await page.reload({ waitUntil: 'domcontentloaded' }); await ready(); await settle();
    const warm_direct = await metrics();
    for (const measured of [cold, warm_direct]) {
      if (!measured.navigation || measured.navigation.startTime !== 0 || !measured.resources.length) {
        throw new Error('Native navigation/resource timing unavailable; do not report this sample');
      }
    }
    phase = 'home';
    await page.getByRole('navigation', { name: '移动端导航' }).getByRole('link', { name: '首页', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-optix-region="home-indices"]')?.getAttribute('data-optix-state') === 'content', null, { timeout: 45000 });
    await settle();
    await page.getByRole('button', { name: '更多', exact: true }).click();
    phase = 'warm_return';
    const start = await page.evaluate(() => performance.now());
    await page.getByRole('dialog', { name: '更多功能', exact: true }).getByRole('button', { name: /新闻催化/ }).click();
    await ready();
    const warm_return = await metrics(); warm_return.ready_ms -= start;
    // Preserve the measured ready time, then let requests finish before closing
    // the context. Teardown socket errors must not mutate a completed sample.
    await settle();
    return { cold, warm_direct, warm_return, errors: [...errors], failures: [...failures], jsErrors: [...jsErrors] };
  } catch (error) {
    const diagnostic = await page.evaluate(() => ({ url: location.href,
      body: document.body?.innerText, ready: window.__controlledPerf?.ready,
      resources: performance.getEntriesByType('resource').map(e => e.toJSON()) })).catch(() => null);
    await writeFile(out + '.failure.json', JSON.stringify({ base, phase, error: String(error), errors, failures, jsErrors, diagnostic }, null, 2));
    throw error;
  } finally { phase = 'teardown'; await context.close(); }
}
try {
  for (let i = 0; i < pairs; i++) {
    const order = i % 2 === 0 ? ['baseline', 'current'] : ['current', 'baseline'];
    const row = { i: i + 1, order };
    for (const side of order) row[side] = await sample(side === 'baseline' ? baseline : current);
    samples.push(row);
    await writeFile(out, JSON.stringify(report(), null, 2));
    console.log(JSON.stringify({ pair: i + 1, baseline: Object.fromEntries(['cold', 'warm_direct', 'warm_return'].map(k => [k, row.baseline[k].ready_ms])), current: Object.fromEntries(['cold', 'warm_direct', 'warm_return'].map(k => [k, row.current[k].ready_ms])), errors: [row.baseline.errors, row.current.errors], failures: [row.baseline.failures, row.current.failures], jsErrors: [row.baseline.jsErrors, row.current.jsErrors] }));
  }
  const summary = {};
  const failures = [];
  const percentile = (values, q) => [...values].sort((a, b) => a - b)[Math.round((values.length - 1) * q)];
  if (samples.length < 20) failures.push('fewer than 20 pairs');
  for (const side of ['baseline', 'current']) {
    summary[side] = {};
    for (const stage of ['cold', 'warm_direct', 'warm_return']) {
      const values = samples.map(row => row[side][stage].ready_ms);
      if (values.some(value => !Number.isFinite(value) || value <= 0)) failures.push(`${side}/${stage}: invalid ready time`);
      summary[side][stage] = { n: values.length, p50: percentile(values, .5), p75: percentile(values, .75) };
    }
    if (samples.some(row => row[side].errors.length || row[side].failures.length || row[side].jsErrors.length)) failures.push(`${side}: request or page errors`);
  }
  for (const [stage, budget] of [['cold', 2500], ['warm_direct', 1000], ['warm_return', 1000]]) {
    if (summary.current[stage].p75 > budget) failures.push(`${stage}: p75 exceeds ${budget}ms`);
  }
  const gate = { ok: failures.length === 0, failures };
  await writeFile(out, JSON.stringify({ ...report(), summary, gate }, null, 2) + '\n');
  console.log(JSON.stringify({ summary, gate }));
  if (!gate.ok) process.exitCode = 1;
} finally { await browser.close(); }
