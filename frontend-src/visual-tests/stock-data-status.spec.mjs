import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const at = '2026-09-04T20:00:00Z';
const ready = { available: true, fresh: true, as_of: at };
const statusRow = (ticker, options = {}) => ({ ticker, status: 'ready', refresh_status: 'ready', as_of: at,
  resources: { overview: ready, daily_chart: ready, signals: ready }, ...options });
const pending = (ticker) => statusRow(ticker, { status: 'pending', refresh_status: 'pending', resources: {
  overview: ready, daily_chart: { available: false, fresh: false, as_of: null }, signals: { available: false, fresh: false, as_of: null },
} });
const coverage = (page) => page.getByTestId('stock-data-coverage');

async function fixture(page, mode = 'status') {
  const state = { requests: [], fail: false, rows: (tickers) => tickers.map(statusRow), dailyReady: false, watchReads: 0, chartReads: [], hold: null };
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    return ['127.0.0.1', 'localhost'].includes(url.hostname) ? route.continue() : route.abort();
  });
  await page.route('**/api/**', async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) return route.continue();
    state.requests.push({ method: request.method(), path: url.pathname, tickers: (url.searchParams.get('tickers') ?? '').split(',').filter(Boolean) });
    if (url.pathname === '/api/stocks/data/status') {
      const tickers = (url.searchParams.get('tickers') ?? '').split(',').filter(Boolean);
      if (state.hold) { const hold = state.hold; state.hold = null; return hold(route, tickers); }
      return route.fulfill({ status: state.fail ? 503 : 200, json: state.fail ? { message: 'status unavailable' } : { items: state.rows(tickers) } });
    }
    if (url.pathname === '/api/stocks/watchlist') {
      state.watchReads += 1;
      const names = mode === 'home' ? Array.from({ length: 205 }, (_, i) => `W${String(i).padStart(3, '0')}`) : ['CUR1', 'CUR2'];
      return route.fulfill({ json: { groups: [{ id: 'default', name: 'Default', stocks: names.map((ticker, i) => ({
        ticker, name: ticker, price: 100 + i, change: 1, change_percent: 205 - i, quote_as_of: at,
        ...(state.dailyReady ? { daily_trend: { interval: '1d', adjustment: 'raw', points: [
          { date: '2026-09-03', close: 95 }, { date: '2026-09-04', close: 101 },
        ] } } : {}),
      })) }] } });
    }
    if (url.pathname.endsWith('/chart')) {
      state.chartReads.push({ range: url.searchParams.get('range'), path: url.pathname });
      return route.fulfill({ json: { bars: state.dailyReady ? [
        { t: '2026-09-03', o: 95, h: 99, l: 94, c: 98, v: 1000 },
        { t: '2026-09-04', o: 98, h: 102, l: 97, c: 101, v: 1200 },
      ] : [] } });
    }
    return route.fulfill({ status: 503, json: { message: `Unexpected fixture request ${url.pathname}` } });
  });
  await page.clock.install();
  state.open = () => page.goto(`/visual-tests/support/stock-data-status.html?mode=${mode}`);
  return state;
}
const statusRequests = (state) => state.requests.filter((request) => request.path === '/api/stocks/data/status');
async function advance(page, ms = 30_000) { await page.clock.fastForward(ms); }
async function capture(page, name) {
  await mkdir('test-results/stock-data-evidence', { recursive: true });
  await page.screenshot({ path: `test-results/stock-data-evidence/${name}.png`, animations: 'disabled', fullPage: false });
}

test('coverage distinguishes incomplete preparation, missing rows, and read failure without losing previous counts', async ({ page }) => {
  const state = await fixture(page);
  state.rows = (tickers) => tickers.map((ticker) => ticker === 'MSFT' ? pending(ticker) : statusRow(ticker));
  await state.open();
  await expect(coverage(page)).toContainText('行情2/2');
  await expect(coverage(page)).toContainText('日线1/2');
  await expect(coverage(page)).toContainText('技术信号1/2');
  await expect(coverage(page)).toContainText('后台准备中 1');
  state.rows = (tickers) => tickers.filter((ticker) => ticker === 'AAPL').map(statusRow);
  await advance(page);
  await expect(coverage(page)).toContainText('状态未知 1');
  await expect(coverage(page)).toContainText('行情1/2');
  state.fail = true;
  await advance(page);
  await expect(coverage(page)).toContainText('状态读取失败');
  await expect(coverage(page)).toContainText('行情1/2');
  await expect(coverage(page)).not.toContainText('数据已就绪');
  expect(state.requests.every((request) => request.method === 'GET')).toBeTruthy();
  await capture(page, 'coverage-read-failure');
});

test('requests deduplicate canonical tickers and use batches of at most 200', async ({ page }) => {
  const state = await fixture(page); await state.open();
  await expect(coverage(page)).toContainText('数据已就绪');
  const requested = Array.from({ length: 403 }, (_, i) => `S${String(i).padStart(3, '0')}`);
  const before = statusRequests(state).length;
  await page.evaluate((tickers) => window.statusHarness.setTickers([...tickers, 's000', ' SPX ', '^GSPC', 'BRK.B', 'BRK-B']), requested);
  await expect(coverage(page)).toContainText('行情406/406');
  const calls = statusRequests(state).slice(before);
  expect(calls.map((call) => call.tickers.length).sort((a, b) => b - a)).toEqual([200, 200, 6]);
  const symbols = calls.flatMap((call) => call.tickers);
  expect(new Set(symbols).size).toBe(406);
  expect(symbols.filter((ticker) => ticker === '^GSPC')).toHaveLength(1);
  expect(symbols).toContain('BRK.B'); expect(symbols).toContain('BRK-B');
  const loaded = statusRequests(state).length;
  await page.evaluate(() => window.statusHarness.setTickers([]));
  await expect(coverage(page)).toHaveCount(0);
  await advance(page);
  expect(statusRequests(state).length).toBe(loaded);
});

test('hidden pages pause the 30-second status polling and resume immediately on visibility', async ({ page }) => {
  const state = await fixture(page); await state.open();
  await expect(coverage(page)).toContainText('数据已就绪');
  const start = statusRequests(state).length;
  await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
  await advance(page, 60_000);
  expect(statusRequests(state).length).toBe(start);
  await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => statusRequests(state).length).toBe(start + 1);
});

test('a late response for an old ticker scope never overwrites the newer scope', async ({ page }) => {
  const state = await fixture(page);
  let release;
  state.hold = (route, tickers) => new Promise((resolve) => { release = async () => { await route.fulfill({ json: { items: tickers.map(statusRow) } }); resolve(); }; });
  await state.open();
  await expect.poll(() => typeof release).toBe('function');
  await page.evaluate(() => window.statusHarness.setTickers(['TSLA']));
  await expect(coverage(page)).toContainText('行情1/1');
  await expect(page.locator('#daily-version')).toContainText('TSLA');
  await release();
  await expect(page.locator('#daily-version')).not.toContainText('AAPL');
  await expect(coverage(page)).toContainText('行情1/1');
});

test('home checks the entire default pool plus displayed radar and earnings, then fills daily charts immediately', async ({ page }) => {
  const state = await fixture(page, 'home');
  state.rows = (tickers) => tickers.map((ticker) => state.dailyReady ? statusRow(ticker) : pending(ticker));
  await state.open();
  await expect(coverage(page)).toContainText('行情220/220');
  await expect(page.getByTestId('watchlist-mover-card')).toHaveCount(6);
  await expect(page.getByTestId('watchlist-daily-trend')).toHaveCount(0);
  const latest = statusRequests(state).flatMap((request) => request.tickers);
  expect(latest).toContain('W204'); expect(latest).toContain('R7'); expect(latest).toContain('E5'); expect(latest).toContain('NVDA');
  expect(latest).not.toContain('R8'); expect(latest).not.toContain('E6');
  state.dailyReady = true;
  const previousReads = state.watchReads;
  await advance(page);
  await expect(page.getByTestId('watchlist-daily-trend')).toHaveCount(6);
  expect(state.watchReads).toBeGreaterThan(previousReads);
  expect(state.requests.every((request) => request.method === 'GET')).toBeTruthy();
  await page.getByTestId('watchlist-mover-card').first().scrollIntoViewIfNeeded();
  await capture(page, 'home-daily-ready');
});

test('home empty charts distinguish background preparation from failure', async ({ page }) => {
  const state = await fixture(page, 'home');
  state.rows = (tickers) => tickers.map((ticker) => pending(ticker));
  await state.open();
  await expect(page.getByTestId('watchlist-mover-card').first()).toContainText('后台正在准备日线');
  state.rows = (tickers) => tickers.map((ticker) => ({ ...pending(ticker), status: 'failed', refresh_status: 'failed' }));
  await advance(page);
  await expect(page.getByTestId('watchlist-mover-card').first()).toContainText('日线准备失败');
  state.rows = (tickers) => tickers.map((ticker) => statusRow(ticker, { refresh_status: 'failed' }));
  await advance(page);
  await expect(page.getByTestId('watchlist-mover-card').first()).toContainText('日线已准备');
  await expect(coverage(page)).toContainText('准备失败 220');
  await expect(page.getByTestId('watchlist-mover-card').first()).not.toContainText('日线准备失败');
  await expect(page.locator('body')).not.toContainText('打开详情后可更新');
});

test('radar includes newly loaded historical tickers and refreshes its real daily chart on readiness', async ({ page }) => {
  const state = await fixture(page, 'breakouts');
  state.rows = (tickers) => tickers.map((ticker) => state.dailyReady ? statusRow(ticker) : pending(ticker));
  await state.open();
  await expect(coverage(page)).toContainText('行情3/3');
  await expect(page.getByText('日线 · 最多 30 个交易日', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '继续读取更早事件' }).click();
  await expect(coverage(page)).toContainText('行情4/4');
  expect(statusRequests(state).at(-1).tickers.sort()).toEqual(['CUR1', 'CUR2', 'HIS1', 'HIS2']);
  const previous = state.chartReads.length;
  state.dailyReady = true;
  await advance(page);
  await expect(page.getByRole('img', { name: 'CUR1 日线迷你 K 线图' })).toBeVisible();
  expect(state.chartReads.length).toBeGreaterThan(previous);
  const firstReadyReads = state.chartReads.length;
  state.rows = (tickers) => tickers.map((ticker) => statusRow(ticker, { resources: {
    overview: ready, signals: ready, daily_chart: { ...ready, as_of: '2026-09-05T20:00:00Z' },
  } }));
  await advance(page);
  await expect.poll(() => state.chartReads.length).toBeGreaterThan(firstReadyReads);
  await expect(page.getByRole('img', { name: 'CUR1 日线迷你 K 线图' })).toBeVisible();
  expect(state.chartReads.every((request) => request.range === '1d')).toBeTruthy();
  expect(state.requests.every((request) => request.method === 'GET')).toBeTruthy();
  await capture(page, 'radar-daily-ready');
});

test('coverage wraps without horizontal overflow on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  const state = await fixture(page);
  state.rows = (tickers) => tickers.map((ticker) => ({ ...pending(ticker), refresh_status: 'failed' }));
  await state.open();
  await expect(coverage(page)).toContainText('准备失败 2');
  const bounds = await coverage(page).boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await capture(page, 'coverage-mobile');
});
