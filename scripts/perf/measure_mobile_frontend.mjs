/** Controlled production-bundle experiment, not real-device FPS or field INP.
 * Serve the two builds with serve_frontend_fixture.py; no production API is used. */
import { createRequire } from 'node:module';
import { readFile, readdir, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { parseStaticJsImports } from './lib/round6_bundle_graph.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(path.join(root, 'frontend-src/package.json'));
const { chromium } = require('playwright');
const sides = {
  baseline: { url: process.env.BASELINE || 'http://127.0.0.1:3055', dir: process.env.BASELINE_DIR || '/tmp/option-pro-mobile-baseline' },
  current: { url: process.env.CURRENT || 'http://127.0.0.1:3057', dir: path.join(root, 'frontend') },
};
for (const side of Object.values(sides)) {
  if (!['localhost', '127.0.0.1'].includes(new URL(side.url).hostname)) throw new Error('Local fixture servers only');
}
const fixtures = JSON.parse(await readFile(path.join(root, 'docs/performance/artifacts/r7-controlled-fixtures.json'), 'utf8'));
const stocks = Array.from({ length: 32 }, (_, i) => ({ ticker: `S${String(i).padStart(3, '0')}`, name: `Stock ${i}`,
  price: 100 + i, change_percent: i, quote_as_of: '2026-09-30T13:00:00Z' }));
Object.assign(fixtures, {
  '/api/access/status': { access_mode: 'password', logged_in: false, account: { logged_in: true, username: 'mobile-perf-fixture' } },
  '/api/account/watchlist': { tickers: stocks.map(row => row.ticker), max_tickers: 50 },
  '/api/stocks/watchlist': { groups: [{ id: 'all', name: 'All', stocks }] },
});
async function graph(dir) {
  const names = await readdir(path.join(dir, 'assets'));
  const html = await readFile(path.join(dir, 'index.html'), 'utf8');
  const entry = html.match(/<script[^>]*type="module"[^>]*src="([^"]+)"/)?.[1];
  if (!entry) throw new Error('Missing module entry');
  const files = new Map();
  async function visit(name) {
    if (files.has(name)) return;
    const bytes = await readFile(path.join(dir, 'assets', name));
    files.set(name, { name, bytes: bytes.length, gzip9: gzipSync(bytes, { level: 9 }).length,
      sha256: createHash('sha256').update(bytes).digest('hex') });
    for (const dep of parseStaticJsImports(bytes.toString())) await visit(path.basename(dep));
  }
  for (const name of [path.basename(entry), names.find(name => name.startsWith('app-shell-')), names.find(name => name.startsWith('Catalysts-'))]) await visit(name);
  const watchlist = names.find(name => name.startsWith('Watchlist-'));
  const watchlistSha256 = createHash('sha256').update(await readFile(path.join(dir, 'assets', watchlist))).digest('hex');
  return { entry, watchlist, watchlistSha256, files: [...files.values()], bytes: [...files.values()].reduce((n, file) => n + file.bytes, 0),
    gzip9: [...files.values()].reduce((n, file) => n + file.gzip9, 0) };
}
const browser = await chromium.launch({ executablePath: process.env.OPTIX_PLAYWRIGHT_EXECUTABLE_PATH || '/usr/bin/chromium' });
const output = { lab: true, measuredAt: new Date().toISOString(), browser: browser.version(), profile: { width: 390, height: 844, dpr: 3, cpu: 4 },
  fixture: { stocks: 32, news: 'r7-controlled-fixtures.json' }, news_static_graph: {}, pairs: [] };
async function sample(side) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3,
    isMobile: true, hasTouch: true, locale: 'zh-CN', reducedMotion: 'no-preference' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { localStorage.setItem('optix:locale', 'zh'); });
  await page.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', route => {
    const body = fixtures[new URL(route.request().url()).pathname];
    return body ? route.fulfill({ json: body }) : route.fulfill({ status: 503, json: { message: 'Optional resource absent from fixture' } });
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  await cdp.send('Performance.enable');
  const metrics = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(x => [x.name, x.value]));
  try {
    await page.goto(side.url + '/watchlist');
    await page.locator('main [data-quote-symbol="S000"]').waitFor();
    await page.waitForTimeout(900); // Finish entry animations before sampling idle clocks.
    const dom = await page.evaluate(() => ({ elements: document.querySelectorAll('main *').length,
      quoteElements: document.querySelectorAll('main [data-quote-symbol] *').length,
      renderedQuotes: document.querySelectorAll('main [data-quote-symbol]').length }));
    const before = await metrics();
    await page.waitForTimeout(2200);
    const after = await metrics();
    const idle = Object.fromEntries(['TaskDuration', 'ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration']
      .map(key => [key + 'Ms', (after[key] - before[key]) * 1000]));
    await page.getByRole('tab', { name: '表格', exact: true }).click();
    const tableMode = await page.evaluate(() => ({ tables: document.querySelectorAll('main table').length,
      elements: document.querySelectorAll('main *').length,
      firstSymbolCopies: document.querySelectorAll('main [data-quote-symbol="S000"]').length }));
    if (errors.length) throw new Error(errors.join('\n'));
    return { dom, idleWindowMs: 2200, idle, tableMode };
  } finally { await context.close(); }
}
try {
  for (const [name, side] of Object.entries(sides)) output.news_static_graph[name] = await graph(side.dir);
  for (let i = 0; i < 4; i++) {
    const row = { order: i % 2 ? ['current', 'baseline'] : ['baseline', 'current'] };
    for (const side of row.order) row[side] = await sample(sides[side]);
    output.pairs.push(row);
    console.log(JSON.stringify({ pair: i + 1, baseline: row.baseline.dom, current: row.current.dom }));
  }
  const file = process.env.OUT || path.join(root, 'docs/performance/artifacts/mobile-20260930.json');
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(output, null, 2) + '\n');
} finally { await browser.close(); }
