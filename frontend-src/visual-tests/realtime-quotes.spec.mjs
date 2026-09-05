import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
const at = '2026-09-04T15:00:00Z';
const status = { enabled: true, configured: true, allowed: true, public_enabled: true, connected: true, connection_status: 'connected', market_session: 'regular' };
const price = (symbol, value = 100, extra = {}) => ({ symbol, price: value, previous_close: 99, change: value - 99, change_pct: (value / 99 - 1) * 100, trade_at: at, received_at: at, source: 'finnhub', session: 'regular', freshness: 'live', subscription_status: 'live', ...extra });
async function fixture(page, enabled = true) {
  const state = { requests: [], errors: [], completeDetail: false, quoteDelayMs: 0, radar: { event_id: 'live-event', ticker: 'AAPL', name: 'Apple', session: 'regular', setup_type: 'DAILY_BASE_BREAKOUT', lifecycle_state: 'WATCHING', state_version: 0, event_at: at, current_price: 100, event_price: 100, invalidation_price: 90, target_price: 120, session_change_pct: 1, intrinsic_strength_score: 80 }, transitions: [{ state: 'WATCHING', at }] };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(() => {
    window.quoteStreams = [];
    class MockEventSource {
      constructor(url) { this.url = url; this.closed = false; this.listeners = new Map(); window.quoteStreams.push(this); }
      addEventListener(type, callback) { this.listeners.set(type, callback); }
      close() { this.closed = true; }
      emit(type, data) { this.listeners.get(type)?.({ data: JSON.stringify(data) }); }
    }
    window.EventSource = MockEventSource;
    localStorage.setItem('optix-locale', 'zh');
  });
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()); if (!url.pathname.startsWith('/api/')) return route.continue(); state.requests.push(url.pathname + url.search);
    if (url.pathname === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: false, account: null } });
    if (url.pathname === '/api/quotes') {
      if (state.quoteDelayMs) await new Promise(resolve => setTimeout(resolve, state.quoteDelayMs));
      return route.fulfill({ json: { quotes: enabled ? (url.searchParams.get('symbols') ?? '').split(',').filter(Boolean).map(symbol => price(symbol)) : [], status: { ...status, allowed: enabled } } });
    }
    if (url.pathname === '/api/market/status') return route.fulfill({ json: { session: 'regular', label: '盘中', is_open: true } });
    if (url.pathname === '/api/market/indices') return route.fulfill({ json: { indices: [{ code: 'SPX', symbol: '^GSPC', price: 6000, change_percent: 1 }] } });
    if (url.pathname === '/api/stocks/watchlist') return route.fulfill({ json: { groups: [{ id: 'all', name: 'All', stocks: Array.from({ length: 32 }, (_, i) => ({ ticker: i === 0 ? 'AAPL' : `S${String(i).padStart(3, '0')}`, name: `Stock ${i}`, price: 100 + i, change: i, change_percent: i, quote_as_of: at })) }] } });
    if (url.pathname === '/api/stocks/data/status') return route.fulfill({ json: { items: (url.searchParams.get('tickers') ?? '').split(',').map(ticker => ({ ticker, status: 'ready', refresh_status: 'ready', resources: { overview: { available: true, fresh: true, as_of: at }, daily_chart: { available: true, fresh: true, as_of: at }, signals: { available: true, fresh: true, as_of: at } } })) } });
    if (url.pathname === '/api/breakouts/current') return route.fulfill({ json: { events: [state.radar], as_of: at, session: 'regular' } });
    if (url.pathname === '/api/breakouts/events') return route.fulfill({ json: { events: [], next_cursor: null } });
    if (url.pathname === '/api/breakouts/events/live-event') return route.fulfill({ json: { event: state.radar, transitions: state.transitions } });
    if (url.pathname === '/api/breakouts/status') return route.fulfill({ json: { enabled: true, market_session: 'regular' } });
    if (state.completeDetail && url.pathname === '/api/stocks/AAPL') return route.fulfill({ json: { ticker: 'AAPL', name: 'Apple', price: 100, change: 1, change_percent: 1, as_of: at, prev_close: 99 } });
    if (state.completeDetail && url.pathname === '/api/stocks/AAPL/chart') return route.fulfill({ json: { ticker: 'AAPL', interval: '1d', adjustment: 'raw', bars: Array.from({ length: 240 }, (_, i) => ({ t: new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10), o: 98 + i % 3, h: 102 + i % 3, l: 96 + i % 3, c: 100 + i % 3, v: 1000 + i })).filter(bar => ![0, 6].includes(new Date(bar.t).getUTCDay())) } });
    if (url.pathname === '/api/stocks/AAPL') return; // Deliberately keep full technical detail pending.
    return route.fulfill({ status: 503, json: { message: 'No analysis fixture' } });
  });
  return state;
}
const latestSymbols = page => page.evaluate(() => {
  const stream = window.quoteStreams.filter(row => !row.closed).at(-1);
  return stream ? new URL(stream.url, location.origin).searchParams.get('symbols').split(',') : [];
});
async function emitEvent(page, type, data) {
  // The page and its HTTP quote snapshot can render before EventSource exists.
  // Check and deliver in one browser task so a reconnect cannot race the send.
  await expect.poll(() => page.evaluate(({ type, data }) => {
    const stream = window.quoteStreams.filter(row => !row.closed).at(-1);
    if (!stream?.listeners.has(type)) return false;
    stream.emit(type, data);
    return true;
  }, { type, data })).toBe(true);
}
const emit = (page, symbol, value, extra = {}) => emitEvent(page, 'quotes', { quotes: [price(symbol, value, extra)] });

test('watchlist subscribes offscreen rows, pushes prices without reordering, and releases on navigation', async ({ page }) => {
  const state = await fixture(page); await page.goto('/watchlist');
  await expect.poll(() => latestSymbols(page)).toContain('S031');
  await expect.poll(() => latestSymbols(page)).toContain('SPY');
  const before = await page.locator('main [data-quote-symbol]').evaluateAll(rows => rows.map(row => row.dataset.quoteSymbol));
  expect(before.length).toBeGreaterThan(0);
  const symbol = before[0]; await emit(page, symbol, 1234.56);
  await expect(page.locator(`main [data-quote-symbol="${symbol}"]`).first().locator('[aria-label="1,234.56"]')).toBeVisible();
  const after = await page.locator('main [data-quote-symbol]').evaluateAll(rows => rows.map(row => row.dataset.quoteSymbol));
  expect(after).toEqual(before);
  await emit(page, symbol, 1234.56, { subscription_status: 'limited', freshness: 'snapshot' });
  await expect(page.locator(`main [data-quote-symbol="${symbol}"]`).first()).toContainText('定时更新');
  await page.getByRole('link', { name: '大盘', exact: true }).first().click();
  await expect.poll(() => latestSymbols(page)).not.toContain('S031');
  expect(await page.evaluate(() => window.quoteStreams.filter(row => !row.closed).length)).toBe(1);
  expect(state.errors).toEqual([]);
});

for (const width of [390, 1440]) {
  test(`detail price arrives while analysis is pending, with decimal rolling at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 }); const state = await fixture(page); await page.goto('/stock/AAPL');
    const header = page.locator('main [data-quote-symbol="AAPL"]').first();
    await expect(header.locator('[aria-label="$100.00"]')).toBeVisible();
    await emit(page, 'AAPL', 999.99); await expect(header.locator('[aria-label="$999.99"]')).toBeVisible();
    await emit(page, 'AAPL', 1000.01); await expect(header.locator('[aria-label="$1,000.01"]')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'AAPL' })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.waitForTimeout(350); // Let the 250 ms digit transition reach its real final frame.
    await mkdir('test-results/quotes', { recursive: true });
    await page.screenshot({ path: `test-results/quotes/detail-${width}.png`, animations: 'disabled' });
    expect(state.errors).toEqual([]);
  });
}

test('visitors without quote access keep delayed indices and never open a stream', async ({ page }) => {
  const state = await fixture(page, false); await page.goto('/watchlist');
  await expect(page.getByText('SPX', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('延迟行情', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('标普500基金', { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.quoteStreams.length)).toBe(0);
  expect(state.requests.filter(url => url.startsWith('/api/quotes'))).toHaveLength(1);
  expect(state.errors).toEqual([]);
});


test('an open radar detail follows new versions and reconciles missed states after reconnect', async ({ page }) => {
  const state = await fixture(page); state.quoteDelayMs = 750; await page.goto('/breakouts');
  await page.getByRole('button', { name: '查看完整证据', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'AAPL 突破事件详情' });
  await expect(dialog).toBeVisible();
  state.radar = { ...state.radar, state_version: 1, lifecycle_state: 'TRIGGERED', trigger_source: 'finnhub', evidence_at: at, triggered_at: at };
  state.transitions.push({ state: 'TRIGGERED', at });
  await emitEvent(page, 'radar', { events: [state.radar] });
  await expect(dialog.getByRole('list', { name: '生命周期轨迹' })).toContainText('已触发');
  state.radar = { ...state.radar, state_version: 2, lifecycle_state: 'CONFIRMED' };
  state.transitions.push({ state: 'CONFIRMED', at });
  // This confirmation is deliberately not pushed. The reconnect must fill the gap.
  await expect.poll(() => page.evaluate(() => {
    const stream = window.quoteStreams.filter(row => !row.closed).at(-1);
    if (typeof stream?.onerror !== 'function') return false;
    stream.onerror();
    return true;
  })).toBe(true);
  await expect(dialog.getByRole('list', { name: '生命周期轨迹' })).toContainText('已确认');
  await expect(dialog.getByText('已确认', { exact: true }).first()).toBeVisible();
  expect(state.errors).toEqual([]);
});


test('live chart reference updates without rewriting candles or resetting the zoom', async ({ page }) => {
  const state = await fixture(page); state.completeDetail = true; await page.goto('/stock/AAPL');
  const chartHost = page.getByRole('img', { name: 'AAPL 1d K 线图', exact: true });
  await expect(chartHost).toBeVisible();
  const before = await page.evaluate(async () => {
    const { echarts } = await import('/src/lib/chart.ts');
    const chart = echarts.getInstanceByDom(document.querySelector('[aria-label="AAPL 1d K 线图"]'));
    window.testQuoteChart = chart;
    chart.dispatchAction({ type: 'dataZoom', startValue: 100, endValue: 171 });
    const option = chart.getOption();
    return { candles: option.series.find(row => row.type === 'candlestick').data, zoom: option.dataZoom[0] };
  });
  await emit(page, 'AAPL', 105.27);
  await expect.poll(() => page.evaluate(() => window.testQuoteChart.getOption().series.find(row => row.id === 'realtime-price-reference').markLine.data[0]?.yAxis)).toBe(105.27);
  const after = await page.evaluate(() => {
    const option = window.testQuoteChart.getOption();
    return { candles: option.series.find(row => row.type === 'candlestick').data, zoom: option.dataZoom[0] };
  });
  expect(after.candles).toEqual(before.candles); expect(after.zoom.startValue).toBe(before.zoom.startValue); expect(after.zoom.endValue).toBe(before.zoom.endValue);
  await page.evaluate(() => {
    const chart = window.testQuoteChart;
    const original = chart.setOption.bind(chart);
    window.referencePatches = 0;
    chart.setOption = (option, ...args) => {
      if (option.series?.some(row => row.id === 'realtime-price-reference')) window.referencePatches++;
      return original(option, ...args);
    };
  });
  await emit(page, 'AAPL', 105.27, { trade_at: '2026-09-04T15:00:01Z', received_at: '2026-09-04T15:00:01Z' });
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => window.referencePatches)).toBe(0);

  expect(state.errors).toEqual([]);
});

for (const width of [320, 390, 768, 1440]) {
  test(`large live prices remain inside the detail and watchlist layouts at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const state = await fixture(page);
    await page.goto('/stock/AAPL');
    await emit(page, 'AAPL', 750000.01, { previous_close: 749999, change: 1.01, change_pct: 0.00013 });
    const value = page.locator('main [aria-label="$750,000.01"]').first();
    await expect(value).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const box = await value.boundingBox(); expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(width);
    await mkdir('test-results/quotes', { recursive: true });
    await page.screenshot({ path: `test-results/quotes/review-detail-${width}.png`, animations: 'disabled' });
    await page.goto('/watchlist');
    await emit(page, 'AAPL', 750000.01, { previous_close: 749999, change: 1.01, change_pct: 0.00013 });
    await expect(page.locator('main [aria-label="750,000.01"]').first()).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/quotes/review-watchlist-${width}.png`, animations: 'disabled' });
    expect(state.errors).toEqual([]);
  });
}

test('detail labels a retained price as reconnecting, never live, after a stream error', async ({ page }) => {
  const state = await fixture(page); await page.goto('/stock/AAPL');
  await emit(page, 'AAPL', 105.27);
  await expect(page.locator('main [aria-label="$105.27"]').first()).toBeVisible();
  await page.evaluate(() => window.quoteStreams.filter(row => !row.closed).at(-1).onerror());
  await expect(page.locator('main header').first()).toContainText('行情重连中');
  await expect(page.locator('main [aria-label="$105.27"]').first()).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('timestamp-only quote bursts keep number DOM stable and reduced motion removes digit columns', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/stock/AAPL');
  const number = page.locator('main [data-quote-symbol="AAPL"]').first().locator('[aria-label="$100.00"]');
  await expect(number).toBeVisible();
  await expect.poll(() => latestSymbols(page)).toContain('AAPL');
  await page.waitForTimeout(500);
  await number.evaluate(element => {
    window.numberMutations = 0;
    window.numberObserver = new MutationObserver(rows => { window.numberMutations += rows.length; });
    window.numberObserver.observe(element, { attributes: true, childList: true, subtree: true, characterData: true });
  });
  await page.evaluate(({ status, quote }) => {
    const stream = window.quoteStreams.filter(row => !row.closed).at(-1);
    for (let i = 1; i <= 200; i++) {
      const stamp = new Date(Date.parse(quote.trade_at) + i).toISOString();
      stream.emit('quotes', { quotes: [{ ...quote, trade_at: stamp, received_at: stamp }], status: { ...status, as_of: stamp } });
    }
  }, { status, quote: price('AAPL') });
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => window.numberMutations)).toBe(0);
  await page.evaluate(() => window.numberObserver.disconnect());
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(() => number.locator('span').count()).toBe(1);
  await expect(number).toHaveText('$100.00');
  expect(state.errors).toEqual([]);
});
