#!/usr/bin/env node
/**
 * Round-6 language-boot lab against a mock/dev origin.
 * Records which dictionary chunks download for zh/en/ja, deep links,
 * and language-switch reload. Does not hit isolated :2000/:2001.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { attach429Counter } from './lib/rate_limit.mjs';
import { isTerminalReady, pageReadyInstallScript } from './lib/page_ready.mjs';
import { readyGateFailures, summarizeReady } from './lib/round6_ready_summary.mjs';

const require = createRequire(fileURLToPath(import.meta.url));
const { chromium } = require(path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../frontend-src/node_modules/playwright',
));

const BASE = process.env.OPTIX_PERF_BASE || 'http://127.0.0.1:3021';
const OUT = process.env.OPTIX_PERF_OUT || '/opt/cursor/artifacts/perf/round6-i18n.json';
const ROUTES = ['/', '/earnings', '/catalysts'];
const LOCALES = ['zh', 'en', 'ja'];
const PAID = /finnhub|yahoo|yfinance|polygon|massive|fmpcloud|twelvedata|openai|macrolens/i;

function attachNetwork(page) {
  const state = {
    requests: 0,
    runtimeEn: 0,
    runtimeJa: 0,
    chart: 0,
    urls: [],
    httpErrors: 0,
    requestFailures: [],
  };
  page.on('request', (request) => {
    state.requests += 1;
    const url = request.url();
    state.urls.push(url);
    if (url.includes('runtime-en')) state.runtimeEn += 1;
    if (url.includes('runtime-ja')) state.runtimeJa += 1;
    if (/\/assets\/(?:eps-chart|chart)-/.test(url) || url.includes('EpsHatchChart')) state.chart += 1;
  });
  page.on('response', (response) => {
    if (response.status() >= 400) state.httpErrors += 1;
  });
  page.on('requestfailed', (request) => {
    const url = request.url();
    if (PAID.test(url)) return;
    const errorText = request.failure()?.errorText || 'unknown';
    if (/ERR_ABORTED/i.test(errorText)) return;
    state.requestFailures.push({ url, error: errorText });
  });
  return state;
}

async function waitReady(page, route, timeout = 45_000) {
  const ready = await page.waitForFunction((path) => {
    const kind = window.__optixPageReadyClass?.(path);
    if (kind === 'content' || kind === 'empty' || kind === 'error' || kind === 'idle') {
      return { kind, at: performance.now() };
    }
    return false;
  }, route, { timeout }).then(async (handle) => handle.jsonValue()).catch(() => null);
  const kind = ready?.kind && isTerminalReady(ready.kind) ? ready.kind : 'timeout';
  return { kind, at: ready?.at ?? null };
}

async function withLocale(locale, fn) {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    locale: locale === 'ja' ? 'ja-JP' : locale === 'en' ? 'en-US' : 'zh-CN',
  });
  const page = await context.newPage();
  await page.route('**/*', async (route) => {
    if (PAID.test(route.request().url())) {
      await route.abort();
      return;
    }
    await route.continue();
  });
  await page.addInitScript({
    content: `${pageReadyInstallScript()}; try { if (!localStorage.getItem('optix:locale')) localStorage.setItem('optix:locale', ${JSON.stringify(locale)}); } catch (e) {}`,
  });
  const rateLimit = attach429Counter(page);
  const network = attachNetwork(page);
  try {
    return await fn(page, network, rateLimit);
  } finally {
    await context.close();
    await browser.close();
  }
}

const cold = [];
for (const locale of LOCALES) {
  for (const route of ROUTES) {
    const sample = await withLocale(locale, async (page, network, rateLimit) => {
      const started = Date.now();
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
      const heading = await page.getByRole('heading', { level: 1 }).first().textContent().catch(() => null);
      const htmlLang = await page.locator('html').getAttribute('lang');
      const ready = await waitReady(page, route);
      return {
        locale,
        route,
        wall_ms: Date.now() - started,
        ready_ms: ready.at,
        ready_class: ready.kind,
        heading,
        html_lang: htmlLang,
        runtime_en: network.runtimeEn,
        runtime_ja: network.runtimeJa,
        chart: network.chart,
        rate_limited: rateLimit.count,
        http_error_n: network.httpErrors,
        request_failed_n: network.requestFailures.length,
        request_failures: network.requestFailures,
      };
    });
    cold.push(sample);
    console.log(
      `${locale} ${route} class=${sample.ready_class} en=${sample.runtime_en} ja=${sample.runtime_ja} lang=${sample.html_lang} h=${sample.heading}`,
    );
  }
}

const switched = await withLocale('zh', async (page, network, rateLimit) => {
  await page.goto(`${BASE}/earnings`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.getByRole('heading', { level: 1 }).first().waitFor();
  const before = { href: page.url(), en: network.runtimeEn, ja: network.runtimeJa };
  await page.evaluate(() => localStorage.setItem('optix:locale', 'en'));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { level: 1 }).first().waitFor();
  return {
    stayed_on_earnings: page.url().includes('/earnings'),
    before,
    after: { href: page.url(), en: network.runtimeEn, ja: network.runtimeJa },
    html_lang: await page.locator('html').getAttribute('lang'),
    heading: await page.getByRole('heading', { level: 1 }).first().textContent().catch(() => null),
    rate_limited: rateLimit.count,
    http_error_n: network.httpErrors,
    request_failed_n: network.requestFailures.length,
    request_failures: network.requestFailures,
  };
});
console.log(`switch zh→en stayed=${switched.stayed_on_earnings} en=${switched.after.en} ja=${switched.after.ja} lang=${switched.html_lang} h=${switched.heading}`);

const readySummary = summarizeReady(cold);
const report = {
  lab: true,
  notRUM: true,
  base: BASE,
  product_commit: process.env.OPTIX_PERF_PRODUCT_COMMIT || null,
  measuredAt: new Date().toISOString(),
  cold,
  ready: readySummary,
  language_switch: switched,
  invariants: {
    zh_no_runtime: cold.filter((row) => row.locale === 'zh').every((row) => row.runtime_en === 0 && row.runtime_ja === 0),
    en_only_en: cold.filter((row) => row.locale === 'en').every((row) => row.runtime_en > 0 && row.runtime_ja === 0),
    ja_only_ja: cold.filter((row) => row.locale === 'ja').every((row) => row.runtime_ja > 0 && row.runtime_en === 0),
    switch_keeps_deep_link: switched.stayed_on_earnings,
    switch_loads_en: switched.after.en > 0 && switched.after.ja === 0,
    switch_html_lang_en: /^en/i.test(switched.html_lang || ''),
  },
};
const gate = [
  ...readyGateFailures(readySummary, {
    expectedN: LOCALES.length * ROUTES.length,
    label: 'i18n_cold',
    requireNetworkTelemetry: true,
  }),
  ...Object.entries(report.invariants)
    .filter(([, ok]) => !ok)
    .map(([name]) => `invariant:${name}`),
];
if (switched.rate_limited) gate.push(`language_switch: rate_limited=${switched.rate_limited}`);
if (switched.http_error_n) gate.push(`language_switch: http_error_n=${switched.http_error_n}`);
if (switched.request_failed_n) gate.push(`language_switch: request_failed_n=${switched.request_failed_n}`);
report.gate = { ok: gate.length === 0, failures: gate };
await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ invariants: report.invariants, ready: { n: readySummary.n, ready_n: readySummary.ready_n, error_n: readySummary.error_n, timeout_n: readySummary.timeout_n }, gate: report.gate }, null, 2));
console.log(`wrote ${OUT}`);
if (gate.length) process.exit(1);
