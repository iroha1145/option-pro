import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

// Real application components and browser interactions, with local API fixtures only.
async function fixture(page, options = {}) {
  const state = { failCatalysts: false, failAggressive: false, holdBalanced: false, failBalanced: false,
    releaseBalanced: null, batches: 0, catalystRequests: [], scans: [], errors: [], ...options };
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    if (!path.startsWith('/api/')) return route.continue();
    const unavailable = () => route.fulfill({ status: 503, json: { message: '本地测试：读取失败' } });
    if (path === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: state.owner === true, account: null } });
    if (path === '/api/quotes') return route.fulfill({ json: { quotes: [], status: { allowed: false, enabled: false, connected: false } } });
    if (path === '/api/market/status') return route.fulfill({ json: { market: 'open', session: 'regular', is_open: true, next_close: '2026-09-14T20:00:00Z' } });
    if (path === '/api/market/indices') return route.fulfill({ json: { indices: [] } });
    if (path === '/api/strength/market') return route.fulfill({ json: state.marketPayload ?? { avg_score: 80, stocks: [] } });
    if (path === '/api/strength/profiles') return route.fulfill({ json: { profiles: ['balanced', 'aggressive', 'conservative'], sectors: [] } });
    if (path === '/api/strength/scan') {
      state.scans.push(Object.fromEntries(url.searchParams));
      if (state.scanReply) return state.scanReply(route, url, state);
      if (state.failAggressive && url.searchParams.get('profile') === 'aggressive') return unavailable();
      if (url.searchParams.get('profile') === 'balanced') {
        if (state.holdBalanced) await new Promise(resolve => { state.releaseBalanced = resolve; });
        if (state.failBalanced) return unavailable();
      }
      const now = new Date().toISOString();
      const rows = state.rows ?? [
        { ticker: 'AAA', name: url.searchParams.get('profile') === 'aggressive' ? '进取甲公司' : '甲公司', price: 100, final_score: 95, change_pct: 1, avg_dollar_volume_20d: 25_000_000, macro_fit_shadow: 80, score_short: 95, score_mid: 90 },
        { ticker: 'BBB', name: '乙公司', price: 110, final_score: 85, change_pct: 2, avg_dollar_volume_20d: 25_000_000, macro_fit_shadow: 20, score_short: 85, score_mid: 80 },
      ];
      return route.fulfill({ json: { rows, universe_count: rows.length, screened_count: rows.length, source_status: 'active', snapshot_saved_at: now, scan_completed_at: now,
      cache_expires_at: new Date(Date.now() + 3_600_000).toISOString(), score_version: 'audit', _stale: false } });
    }
    if (path.startsWith('/api/worker/actions/')) {
      if (state.workerReply) return state.workerReply(route, url, state);
      return unavailable();
    }
    if (path === '/api/catalysts/tickers/batch') {
      state.batches++;
      const { tickers } = route.request().postDataJSON();
      state.catalystRequests.push([...tickers]);
      if (state.catalystReply) return state.catalystReply(route, tickers, state);
      if (state.failCatalysts) return unavailable();
      return route.fulfill({ json: { results: Object.fromEntries(tickers.map(ticker => [ticker, {
        items: [{ title_zh: `${ticker} 公布新订单`, published_at: ticker === 'BBB' ? '2026-09-11T19:00:00Z' : '2026-09-11T18:00:00Z' }],
        has_more: false, summary: { bullish: 1, bearish: 0, pending: 0 },
      }])) } });
    }
    if (path.startsWith('/api/signals/stock/')) return route.fulfill({ json: { signals: [] } });
    return unavailable();
  });
  await page.goto('/screener');
  await expect(page.getByRole('heading', { name: '选股扫描', exact: true })).toBeVisible();
  return state;
}

const manyRows = (count) => Array.from({ length: count }, (_, index) => ({
  ticker: `T${String(index).padStart(2, '0')}`, name: `公司${index}`, price: 100,
  final_score: 99 - index / 10, change_pct: 1, avg_dollar_volume_20d: 25_000_000,
  macro_fit_shadow: 80, score_short: 90, score_mid: 90,
}));

const catalystResults = (tickers) => ({ results: Object.fromEntries(tickers.map(ticker => [ticker, {
  items: [{ title_zh: `${ticker} 新闻`, published_at: '2026-09-11T18:00:00Z' }],
  has_more: false, summary: { bullish: 1, bearish: 0, pending: 0 },
}])) });

const scanBody = (ticker = 'AAA', overrides = {}) => {
  const now = new Date().toISOString();
  return { rows: [{ ticker, name: `${ticker} 公司`, price: 100, final_score: 95, change_pct: 1,
    avg_dollar_volume_20d: 25_000_000, macro_fit_shadow: 80, score_short: 95, score_mid: 90 }],
  universe_count: 1, screened_count: 1, source_status: 'active', snapshot_saved_at: now,
  scan_completed_at: now, cache_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  score_version: 'audit', _stale: false, ...overrides };
};

const completedWorker = (parameters, overrides = {}) => ({
  request_id: 'test-task', action_type: 'strength_refresh', status: 'completed',
  details: { parameters, result: { completed_at: new Date(Date.now() - 1_000).toISOString(), score_version: 'audit', published: true } },
  ...overrides,
});

async function scan(page, ticker = 'AAA') {
  await page.locator('button.scan-trigger').click();
  await expect(page.getByRole('table').getByText(ticker, { exact: true }).first()).toBeVisible();
}

test('catalyst failure settles with retry and recovering news changes the real table order', async ({ page }) => {
  const state = await fixture(page, { failCatalysts: true });
  await scan(page);
  await expect.poll(() => state.batches).toBeGreaterThan(0);
  await page.getByRole('tab', { name: '最新催化', exact: true }).click();
  const status = page.getByRole('status').filter({ hasText: '催化摘要读取失败，暂按强度排序' });
  await expect(status).toBeVisible();
  await expect(page.getByText('正在准备排序数据 · 剩余')).toHaveCount(0);
  const before = state.batches;
  state.failCatalysts = false;
  await status.getByRole('button', { name: '重试', exact: true }).click();
  await expect(status).toHaveCount(0);
  await expect.poll(() => state.batches).toBe(before + 1);
  const first = page.locator('table tbody > tr').first();
  await expect(first).toContainText('BBB');
  expect(state.errors).toEqual([]);
});

test('catalyst batches finish their full round despite a same-symbol discovery, then wait for an explicit retry', async ({ page }) => {
  const rows = manyRows(60);
  let releaseThird;
  const state = await fixture(page, { rows, catalystReply: (route, tickers, current) => {
    if (current.batches === 3) return new Promise(resolve => { releaseThird = () => { route.fulfill({ json: catalystResults(tickers) }).then(resolve); }; });
    return route.fulfill({ status: 503, json: { message: 'batch unavailable' } });
  } });
  await page.getByRole('combobox', { name: '最多显示数量', exact: true }).click();
  await page.getByRole('option', { name: '最多 120', exact: true }).click();
  await page.getByRole('tab', { name: '最新催化', exact: true }).click();
  await scan(page, 'T00');
  await expect.poll(() => state.batches).toBe(3);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(state.batches).toBe(3);
  releaseThird();
  const status = page.getByRole('status').filter({ hasText: '催化摘要读取失败，暂按强度排序' });
  await expect(status).toBeVisible();
  expect(state.catalystRequests.map(tickers => tickers.length)).toEqual([20, 20, 20]);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(state.batches).toBe(3);
  currentReplyToSuccess(state);
  await status.getByRole('button', { name: '重试', exact: true }).click();
  await expect.poll(() => state.batches).toBe(5);
  expect(state.catalystRequests.slice(3).flat().sort()).toEqual(rows.slice(0, 40).map(row => row.ticker).sort());
  await expect(status).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

function currentReplyToSuccess(state) {
  state.catalystReply = (route, tickers) => route.fulfill({ json: catalystResults(tickers) });
}

test('a late catalyst response from an older scan cannot enter the new result set', async ({ page }) => {
  let releaseOld;
  const state = await fixture(page, { rows: manyRows(1), catalystReply: (route, tickers) => {
    if (tickers.includes('T00')) return new Promise(resolve => { releaseOld = () => { route.fulfill({ json: catalystResults(tickers) }).then(resolve); }; });
    return route.fulfill({ json: catalystResults(tickers) });
  } });
  await scan(page, 'T00');
  await expect.poll(() => typeof releaseOld).toBe('function');
  state.rows = [{ ...manyRows(1)[0], ticker: 'NEW', name: '新公司' }];
  await page.getByRole('tablist', { name: '偏好', exact: true }).getByRole('tab', { name: '进取', exact: true }).click();
  await page.locator('button.scan-trigger').click();
  await expect(page.getByRole('table').getByText('NEW', { exact: true })).toBeVisible();
  releaseOld();
  await expect.poll(() => state.catalystRequests.some(tickers => tickers.includes('NEW'))).toBe(true);
  await expect(page.getByRole('table').getByText('T00', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('a completed catalyst summary expires and reloads for the same visible ticker', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  const state = await fixture(page);
  await scan(page);
  await expect.poll(() => state.batches).toBe(1);
  await page.clock.fastForward(5 * 60_000 + 1_000);
  await expect.poll(() => state.batches).toBe(2);
  expect(state.catalystRequests).toEqual([['AAA', 'BBB'], ['AAA', 'BBB']]);
  expect(state.errors).toEqual([]);
});

test('visitor waits for a preparing snapshot and never submits an owner task', async ({ page }) => {
  let reads = 0;
  let posts = 0;
  const state = await fixture(page, { scanReply: (route, url) => {
    if (!url.searchParams.has('top')) return route.fulfill({ json: scanBody() });
    reads++;
    return reads < 3
      ? route.fulfill({ status: 503, json: { code: 'strength_snapshot_preparing', message: 'preparing' } })
      : route.fulfill({ json: scanBody() });
  }, workerReply: (route) => { posts++; return route.fulfill({ status: 500, json: { message: 'unexpected task' } }); } });
  await scan(page);
  expect(reads).toBe(3);
  expect(posts).toBe(0);
  expect(state.errors).toEqual([]);
});

test('visitor treats an unavailable snapshot as failure without starting a refresh', async ({ page }) => {
  let posts = 0;
  const state = await fixture(page, { scanReply: (route, url) => url.searchParams.has('top')
    ? route.fulfill({ status: 503, json: { code: 'strength_snapshot_unavailable', message: 'gone' } })
    : route.fulfill({ json: scanBody() }),
  workerReply: (route) => { posts++; return route.fulfill({ status: 500, json: { message: 'unexpected task' } }); } });
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  expect(posts).toBe(0);
  expect(state.errors).toEqual([]);
});

test('an expired successful scan cannot reuse cached metadata after the next GET fails', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let reads = 0;
  const state = await fixture(page, { scanReply: (route, url) => {
    if (!url.searchParams.has('top')) return route.fulfill({ json: scanBody() });
    reads++;
    return reads === 1
      ? route.fulfill({ json: scanBody('INITIAL', { cache_expires_at: new Date(Date.now() + 5_000).toISOString() }) })
      : route.fulfill({ status: 503, json: { message: 'offline' } });
  } });
  await scan(page, 'INITIAL');
  await page.clock.fastForward(31_000);
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('INITIAL', { exact: true })).toBeVisible();
  expect(reads).toBe(2);
  expect(state.errors).toEqual([]);
});

test('successful scan commits requested results while leaving a newer draft untouched', async ({ page }) => {
  let releaseAggressive;
  const state = await fixture(page, { scanReply: (route, url) => {
    if (url.searchParams.get('profile') === 'aggressive') {
      return new Promise(resolve => { releaseAggressive = () => route.fulfill({ json: scanBody('AGGRESSIVE') }).then(resolve); });
    }
    return route.fulfill({ json: scanBody('BALANCED') });
  } });
  await scan(page, 'BALANCED');
  const profile = page.getByRole('tablist', { name: '偏好', exact: true });
  await profile.getByRole('tab', { name: '进取', exact: true }).click();
  await page.locator('button.scan-trigger').click();
  await expect.poll(() => typeof releaseAggressive).toBe('function');
  await profile.getByRole('tab', { name: '稳健', exact: true }).click();
  releaseAggressive();
  await expect(page.getByRole('table').getByText('AGGRESSIVE', { exact: true })).toBeVisible();
  await expect(profile.getByRole('tab', { name: '稳健', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: /评分方法/ })).toContainText('进取');
  expect(state.errors).toEqual([]);
});

test('scanning does not persist the removed algorithm preference', async ({ page }) => {
  const state = await fixture(page);
  const writes = [];
  page.on('request', request => {
    if (request.method() === 'PUT' && new URL(request.url()).pathname === '/api/view-preferences') {
      writes.push(request.postDataJSON());
    }
  });
  await scan(page);
  expect(writes).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('a failed discovery retains rows, marks them unverified and keeps the earlier scan clock', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let failDiscovery = false;
  const state = await fixture(page, { scanReply: (route, url) => failDiscovery && url.searchParams.has('top')
    ? route.fulfill({ status: 503, json: { message: 'offline' } })
    : route.fulfill({ json: scanBody() }) });
  await scan(page);
  const scanClock = page.getByText('上次扫描', { exact: true }).locator('..');
  const before = await scanClock.innerText();
  failDiscovery = true;
  await page.clock.fastForward(31_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.getByText('数据未刷新').first()).toBeVisible();
  await expect(page.getByRole('table').getByText('AAA', { exact: true })).toBeVisible();
  expect(await scanClock.innerText()).toBe(before);
  expect(state.errors).toEqual([]);
});

test('visible discovery marks an expired snapshot stale before its delayed GET returns', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let releaseRead;
  let reads = 0;
  const oldSnapshot = scanBody('INITIAL', { cache_expires_at: new Date(Date.now() + 5_000).toISOString() });
  const state = await fixture(page, { scanReply: (route, url) => {
    if (!url.searchParams.has('top')) return route.fulfill({ json: oldSnapshot });
    reads++;
    if (reads === 1) return route.fulfill({ json: oldSnapshot });
    return new Promise(resolve => { releaseRead = () => route.fulfill({ json: oldSnapshot }).then(resolve); });
  } });
  await scan(page, 'INITIAL');
  await page.clock.fastForward(31_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => typeof releaseRead).toBe('function');
  await expect(page.getByText('数据未刷新').first()).toBeVisible();
  releaseRead();
  await expect(page.getByText('数据未刷新').first()).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('late discovery cannot replace newer profile rows after another scan', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let releaseOld;
  let hold = false;
  const state = await fixture(page, { scanReply: (route, url) => {
    if (hold && url.searchParams.get('profile') === 'balanced') {
      hold = false;
      return new Promise(resolve => { releaseOld = () => route.fulfill({ json: scanBody('OLD') }).then(resolve); });
    }
    return route.fulfill({ json: scanBody(url.searchParams.get('profile') === 'aggressive' ? 'NEW' : 'AAA') });
  } });
  await scan(page);
  hold = true;
  await page.clock.fastForward(31_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => typeof releaseOld).toBe('function');
  await page.getByRole('tablist', { name: '偏好', exact: true }).getByRole('tab', { name: '进取', exact: true }).click();
  await scan(page, 'NEW');
  releaseOld();
  await expect(page.getByRole('table').getByText('NEW', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('OLD', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('a late discovery failure cannot mark newer profile rows stale', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let releaseOld;
  let hold = false;
  const state = await fixture(page, { scanReply: (route, url) => {
    if (hold && url.searchParams.get('profile') === 'balanced') {
      hold = false;
      return new Promise(resolve => { releaseOld = () => route.fulfill({ status: 503, json: { message: 'old offline read' } }).then(resolve); });
    }
    return route.fulfill({ json: scanBody(url.searchParams.get('profile') === 'aggressive' ? 'NEW' : 'AAA') });
  } });
  await scan(page);
  hold = true;
  await page.clock.fastForward(31_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => typeof releaseOld).toBe('function');
  await page.getByRole('tablist', { name: '偏好', exact: true }).getByRole('tab', { name: '进取', exact: true }).click();
  await scan(page, 'NEW');
  releaseOld();
  await expect(page.getByRole('table').getByText('NEW', { exact: true })).toBeVisible();
  await expect(page.getByText('数据未刷新')).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('an unmounted screener ignores a delayed published snapshot', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let releaseOld;
  let hold = false;
  const state = await fixture(page, { scanReply: (route) => {
    if (hold) {
      hold = false;
      return new Promise(resolve => { releaseOld = () => route.fulfill({ json: scanBody('OLD') }).then(resolve); });
    }
    return route.fulfill({ json: scanBody() });
  } });
  await scan(page);
  hold = true;
  await page.clock.fastForward(31_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => typeof releaseOld).toBe('function');
  await page.getByRole('link', { name: '首页', exact: true }).click();
  releaseOld();
  await expect(page.getByRole('heading', { name: '选股扫描', exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('an unmounted screener ignores a delayed discovery failure', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let releaseFailure;
  let hold = false;
  const state = await fixture(page, { scanReply: route => {
    if (hold) {
      hold = false;
      return new Promise(resolve => { releaseFailure = () => route.fulfill({ status: 503, json: { message: 'old offline read' } }).then(resolve); });
    }
    return route.fulfill({ json: scanBody() });
  } });
  await scan(page);
  hold = true;
  await page.clock.fastForward(31_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => typeof releaseFailure).toBe('function');
  await page.getByRole('link', { name: '首页', exact: true }).click();
  releaseFailure();
  await expect(page.getByRole('heading', { name: '选股扫描', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: '选股', exact: true }).click();
  await expect(page.getByRole('heading', { name: '选股扫描', exact: true })).toBeVisible();
  await expect(page.getByText('数据未刷新')).toHaveCount(0);
  await expect(page.getByText('扫描数据不可用', { exact: true })).toHaveCount(0);
  await scan(page);
  await expect(page.getByText('数据未刷新')).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('identity loss cancels an old owner read before it can submit a worker task', async ({ page }) => {
  let releaseOld;
  let hold = false;
  let posts = 0;
  const state = await fixture(page, { owner: true,
    scanReply: (route) => {
      if (hold) return new Promise(resolve => { releaseOld = () => route.fulfill({ json: scanBody('OLD', { _stale: true }) }).then(resolve); });
      return route.fulfill({ json: scanBody() });
    },
    workerReply: route => { posts++; return route.fulfill({ status: 500, json: { message: 'obsolete refresh' } }); },
  });
  hold = true;
  await page.locator('button.scan-trigger').click();
  await expect.poll(() => typeof releaseOld).toBe('function');
  state.owner = false;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('button', { name: '刷新强度分', exact: true })).toHaveCount(0);
  releaseOld();
  expect(posts).toBe(0);
  expect(state.errors).toEqual([]);
});

test('a superseded stale scan GET cannot start an obsolete owner task or change the newer result', async ({ page }) => {
  let releaseOld;
  let hold = true;
  let posts = 0;
  const state = await fixture(page, { owner: true,
    scanReply: (route, url) => {
      if (url.searchParams.has('top') && url.searchParams.get('profile') === 'balanced' && hold) {
        hold = false;
        return new Promise(resolve => { releaseOld = () => route.fulfill({ json: scanBody('OLD', { _stale: true }) }).then(resolve); });
      }
      return route.fulfill({ json: scanBody('NEW') });
    },
    workerReply: route => { posts++; return route.fulfill({ status: 500, json: { message: 'obsolete task' } }); },
  });
  await page.locator('button.scan-trigger').click();
  await expect.poll(() => typeof releaseOld).toBe('function');
  await page.getByRole('tablist', { name: '偏好', exact: true }).getByRole('tab', { name: '进取', exact: true }).click();
  await scan(page, 'NEW');
  releaseOld();
  await expect(page.getByRole('table').getByText('NEW', { exact: true })).toBeVisible();
  await expect(page.getByText('使用已有评分', { exact: true })).toBeVisible();
  expect(posts).toBe(0);
  expect(state.errors).toEqual([]);
});

test('a superseded worker wait stops polling after a newer scan wins', async ({ page }) => {
  let releaseOldStatus;
  let statusReads = 0;
  let posts = 0;
  let requested;
  const state = await fixture(page, { owner: true,
    scanReply: (route, url) => route.fulfill({ json: scanBody(url.searchParams.get('profile') === 'aggressive' ? 'NEW' : 'INITIAL') }),
    workerReply: route => {
      if (route.request().method() === 'POST') {
        posts++;
        requested = route.request().postDataJSON().parameters;
        return route.fulfill({ json: completedWorker(requested, { status: 'queued' }) });
      }
      statusReads++;
      if (statusReads === 1) return new Promise(resolve => {
        releaseOldStatus = () => route.fulfill({ json: completedWorker(requested, { status: 'running' }) }).then(resolve);
      });
      return route.fulfill({ json: completedWorker(requested, { status: 'running' }) });
    },
  });
  await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
  await expect.poll(() => typeof releaseOldStatus).toBe('function');
  await page.getByRole('tablist', { name: '偏好', exact: true }).getByRole('tab', { name: '进取', exact: true }).click();
  await scan(page, 'NEW');
  releaseOldStatus();
  await page.waitForTimeout(1_700);
  expect(statusReads).toBe(1);
  expect(posts).toBe(1);
  await expect(page.getByRole('table').getByText('NEW', { exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('owner refresh retries publication visibility without posting a second task', async ({ page }) => {
  let posts = 0;
  let readsAfterPost = 0;
  const state = await fixture(page, { owner: true,
    workerReply: (route) => {
      posts++;
      const parameters = route.request().postDataJSON().parameters;
      return route.fulfill({ json: completedWorker(parameters) });
    },
    scanReply: (route) => {
      if (posts === 0) return route.fulfill({ json: scanBody('INITIAL') });
      readsAfterPost++;
      return route.fulfill({ json: readsAfterPost === 1
        ? scanBody('OLD', { snapshot_saved_at: '2026-09-01T00:00:00Z' })
        : scanBody('PUBLISHED') });
    },
  });
  await scan(page, 'INITIAL');
  await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
  await expect(page.getByRole('table').getByText('PUBLISHED', { exact: true })).toBeVisible();
  expect(posts).toBe(1);
  expect(readsAfterPost).toBe(2);
  expect(state.errors).toEqual([]);
});

test('owner refresh stops after three unpublished reads, retaining the previous rows', async ({ page }) => {
  let posts = 0;
  let readsAfterPost = 0;
  const state = await fixture(page, { owner: true,
    workerReply: (route) => {
      posts++;
      return route.fulfill({ json: completedWorker(route.request().postDataJSON().parameters) });
    },
    scanReply: (route) => {
      if (posts === 0) return route.fulfill({ json: scanBody('INITIAL') });
      readsAfterPost++;
      return route.fulfill({ json: scanBody('OLD', { snapshot_saved_at: '2026-09-01T00:00:00Z' }) });
    },
  });
  await scan(page, 'INITIAL');
  await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
  await expect(page.getByText('扫描失败', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('INITIAL', { exact: true })).toBeVisible();
  expect(posts).toBe(1);
  expect(readsAfterPost).toBe(3);
  expect(state.errors).toEqual([]);
});

test('missing publication after a completed task cannot cause another worker post', async ({ page }) => {
  let posts = 0;
  let readsAfterPost = 0;
  const state = await fixture(page, { owner: true,
    workerReply: route => {
      posts++;
      return route.fulfill({ json: completedWorker(route.request().postDataJSON().parameters) });
    },
    scanReply: route => {
      if (posts === 0) return route.fulfill({ json: scanBody('INITIAL') });
      readsAfterPost++;
      return route.fulfill({ status: 503, json: { code: 'strength_snapshot_unavailable', message: 'missing' } });
    },
  });
  await scan(page, 'INITIAL');
  await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('INITIAL', { exact: true })).toBeVisible();
  expect(posts).toBe(1);
  expect(readsAfterPost).toBe(3);
  expect(state.errors).toEqual([]);
});

test('retry after a failed owner refresh still posts a fresh worker task', async ({ page }) => {
  let posts = 0;
  const state = await fixture(page, { owner: true,
    workerReply: route => {
      posts++;
      return posts === 1
        ? route.fulfill({ status: 503, json: { message: 'worker offline' } })
        : route.fulfill({ json: completedWorker(route.request().postDataJSON().parameters) });
    },
    scanReply: route => route.fulfill({ json: scanBody(posts > 1 ? 'REFRESHED' : 'INITIAL') }),
  });
  await scan(page, 'INITIAL');
  await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('INITIAL', { exact: true })).toBeVisible();
  await page.getByRole('region', { name: '扫描结果', exact: true }).getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('table').getByText('REFRESHED', { exact: true })).toBeVisible();
  expect(posts).toBe(2);
  expect(state.errors).toEqual([]);
});

test('worker queue and running phases reach the real scan interface before publication', async ({ page }) => {
  let releaseRunning;
  let polls = 0;
  let requested;
  const state = await fixture(page, { owner: true,
    scanReply: route => route.fulfill({ json: scanBody('PUBLISHED') }),
    workerReply: route => {
      if (route.request().method() === 'POST') {
        requested = route.request().postDataJSON().parameters;
        return route.fulfill({ json: completedWorker(requested, { status: 'queued' }) });
      }
      polls++;
      if (polls === 1) return new Promise(resolve => {
        releaseRunning = () => route.fulfill({ json: completedWorker(requested, { status: 'running' }) }).then(resolve);
      });
      return route.fulfill({ json: completedWorker(requested) });
    },
  });
  await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
  await expect.poll(() => typeof releaseRunning).toBe('function');
  await expect(page.getByText('正在扫描… · 排队中')).toBeVisible();
  releaseRunning();
  await expect(page.getByText('正在扫描… · 后台计算中')).toBeVisible();
  await expect(page.getByRole('table').getByText('PUBLISHED', { exact: true })).toBeVisible();
  expect(polls).toBe(2);
  expect(state.errors).toEqual([]);
});

test('timed-out owner task stays recoverable with its original request ID', async ({ page }) => {
  await page.clock.install({ time: new Date() });
  let posts = 0;
  let polls = 0;
  const state = await fixture(page, { owner: true,
    scanReply: route => route.fulfill({ json: scanBody('INITIAL') }),
    workerReply: (route) => {
      const method = route.request().method();
      if (method === 'POST') posts++;
      else polls++;
      const parameters = method === 'POST' ? route.request().postDataJSON().parameters : state.requested;
      if (method === 'POST') state.requested = parameters;
      return route.fulfill({ json: completedWorker(parameters, { status: 'queued' }) });
    },
  });
  await expect(page.getByRole('button', { name: '刷新强度分', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
  await expect.poll(() => polls).toBeGreaterThan(0);
  await page.clock.fastForward(7_319_000);
  await expect(page.getByText('扫描失败', { exact: true })).toHaveCount(0);
  await page.clock.fastForward(1_001);
  await expect(page.getByText('扫描失败', { exact: true })).toBeVisible();
  const pending = await page.evaluate(() => JSON.parse(sessionStorage.getItem('optix:screener-pending-strength')));
  expect(pending.requestId).toBe('test-task');
  expect(posts).toBe(1);
  expect(state.errors).toEqual([]);
});

for (const recoveredStatus of ['failed', 'missing']) {
  test(`a ${recoveredStatus} recovered worker task is replaced on the next owner refresh`, async ({ page }) => {
    const parameters = { universe: 'all_market', timeframe: 'mid', profile: 'balanced', top: 20,
      sector_id: null, min_price: 5, min_avg_dollar_volume: 10_000_000, include_options: true,
      ranking_algorithm: 'eod_limited_v1' };
    await page.addInitScript((params) => sessionStorage.setItem('optix:screener-pending-strength', JSON.stringify({
      requestId: 'abandoned-task', parameters: params, storedAt: Date.now(), principal: 'owner:',
    })), parameters);
    let posts = 0;
    let statusReads = 0;
    const state = await fixture(page, { owner: true,
      workerReply: (route) => {
        if (route.request().method() === 'GET') {
          statusReads++;
          return recoveredStatus === 'missing'
            ? route.fulfill({ status: 404, json: { message: 'missing' } })
            : route.fulfill({ json: completedWorker(parameters, { request_id: 'abandoned-task', status: 'failed' }) });
        }
        posts++;
        return route.fulfill({ json: completedWorker(route.request().postDataJSON().parameters) });
      },
      scanReply: route => route.fulfill({ json: scanBody('RECOVERED') }),
    });
    await page.getByRole('button', { name: '刷新强度分', exact: true }).click();
    await expect(page.getByRole('table').getByText('RECOVERED', { exact: true })).toBeVisible();
    expect(statusReads).toBe(1);
    expect(posts).toBe(1);
    expect(state.errors).toEqual([]);
  });
}

test('Retry repeats the failed profile and preserves a draft edited after the failure', async ({ page }) => {
  const state = await fixture(page);
  await scan(page);
  state.failAggressive = true;
  const profile = page.getByRole('tablist', { name: '偏好', exact: true });
  await profile.getByRole('tab', { name: '进取', exact: true }).click();
  await page.getByTestId('screener-advanced-filters').locator('summary').click();
  await page.getByRole('textbox', { name: '最低价格', exact: true }).fill('40');
  await page.locator('button.scan-trigger').click();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  await profile.getByRole('tab', { name: '稳健', exact: true }).click();
  await page.getByRole('textbox', { name: '最低价格', exact: true }).fill('80');
  state.failAggressive = false;
  const before = state.scans.filter(item => item.profile === 'aggressive').length;
  await page.getByRole('region', { name: '扫描结果', exact: true }).getByRole('button', { name: '重试', exact: true }).click();
  await expect.poll(() => state.scans.filter(item => item.profile === 'aggressive').length).toBe(before + 1);
  expect(state.scans.at(-1).min_price).toBe('40');
  await expect(page.getByText('扫描数据不可用', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('table').getByText('进取甲公司', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /评分方法/ })).toContainText('进取');
  await expect(profile.getByRole('tab', { name: '稳健', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('textbox', { name: '最低价格', exact: true })).toHaveValue('80');
  expect(state.errors).toEqual([]);
});

test('native table row expands through a real keyboard button without activating from tooltips', async ({ page }) => {
  const state = await fixture(page);
  await scan(page);
  const row = page.getByRole('row').filter({ has: page.getByText('AAA', { exact: true }) }).first();
  await expect(row).not.toHaveAttribute('role', 'button');
  await expect(row).not.toHaveAttribute('tabindex');
  const toggle = row.getByRole('button', { name: '展开或收起 AAA 详情', exact: true });
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const id = await toggle.getAttribute('aria-controls');
  await expect(page.locator(`[id=${JSON.stringify(id)}]`)).toBeVisible();
  const tooltip = row.getByRole('button', { name: '分项强度', exact: true });
  await tooltip.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await toggle.focus();
  await page.keyboard.press('Space');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(state.errors).toEqual([]);
});

test('reset all clears the macro filter that caused an empty result', async ({ page }) => {
  const state = await fixture(page);
  await scan(page);
  await page.getByRole('button', { name: '宏观适配', exact: true }).click();
  await page.getByRole('tab', { name: '中性', exact: true }).click();
  await expect(page.getByText('当前条件无命中', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重置全部条件', exact: true }).click();
  await expect(page.getByText('当前条件无命中', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('table').getByText('AAA', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('BBB', { exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: '中性', exact: true })).toHaveAttribute('aria-selected', 'false');
  expect(state.errors).toEqual([]);
});

test('macro filtering keeps other tier counts available for comparison', async ({ page }) => {
  const rows = [
    { ticker: 'S', final_score: 95, macro_fit_shadow: 80 },
    { ticker: 'A', final_score: 85, macro_fit_shadow: 80 },
    { ticker: 'B', final_score: 75, macro_fit_shadow: 20 },
    { ticker: 'UNKNOWN', final_score: 96, macro_fit_shadow: null },
  ].map(row => ({ name: row.ticker, price: 100, change_pct: 1, avg_dollar_volume_20d: 25_000_000,
    score_short: 90, score_mid: 90, ...row }));
  const state = await fixture(page, { rows });
  await scan(page, 'S');
  await page.getByRole('button', { name: '宏观适配', exact: true }).click();
  await page.getByRole('tab', { name: '顺风', exact: true }).click();
  const tierHit = (tier) => page.locator(`button[title="只看 ${tier} 档"] .metric-value`);
  await expect(tierHit('S')).toHaveText('1');
  await expect(tierHit('A')).toHaveText('1');
  await expect(tierHit('B')).toHaveText('0');
  await page.locator('button[title="只看 S 档"]').click();
  await expect(page.getByRole('table').getByText('S', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('table').getByText('A', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test('reset from aggressive actually scans balanced and preserves old profile through failure and retry', async ({ page }) => {
  const state = await fixture(page);
  const profile = page.getByRole('tablist', { name: '偏好', exact: true });
  await profile.getByRole('tab', { name: '进取', exact: true }).click();
  await page.getByRole('tablist', { name: '周期', exact: true }).getByRole('tab', { name: '短期', exact: true }).click();
  await scan(page);
  const method = page.getByRole('button', { name: /评分方法/ });
  await expect(method).toContainText('进取');
  await page.getByRole('button', { name: '宏观适配', exact: true }).click();
  await page.getByRole('tab', { name: '中性', exact: true }).click();
  await expect(page.getByText('当前条件无命中', { exact: true })).toBeVisible();
  state.holdBalanced = true;
  await page.getByRole('button', { name: '重置全部条件', exact: true }).click();
  await expect.poll(() => typeof state.releaseBalanced).toBe('function');
  expect(state.scans.at(-1).profile).toBe('balanced');
  expect(state.scans.at(-1).timeframe).toBe('mid');
  await expect(profile.getByRole('tab', { name: '均衡', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(method).toContainText('进取');
  await expect(page.getByRole('table').getByText('进取甲公司', { exact: true })).toBeVisible();
  state.failBalanced = true;
  state.releaseBalanced();
  await expect(page.getByText('扫描数据不可用', { exact: true })).toBeVisible();
  await expect(method).toContainText('进取');
  state.holdBalanced = false;
  state.failBalanced = false;
  const before = state.scans.filter(item => item.profile === 'balanced').length;
  await page.getByRole('region', { name: '扫描结果', exact: true }).getByRole('button', { name: '重试', exact: true }).click();
  await expect.poll(() => state.scans.filter(item => item.profile === 'balanced').length).toBe(before + 1);
  await expect(method).toContainText('均衡');
  await expect(page.getByRole('table').getByText('甲公司', { exact: true })).toBeVisible();
  await expect(page.getByRole('table').getByText('进取甲公司', { exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

for (const width of [320, 390]) {
  test(`mobile screener pager changes rows with 44px controls and no overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    const rows = Array.from({ length: 25 }, (_, index) => ({
      ticker: `T${String(index).padStart(3, '0')}`,
      name: `分页公司 ${index}`,
      price: 100 + index,
      final_score: 95 - index * 0.5,
      change_pct: index / 10,
      avg_dollar_volume_20d: 25_000_000,
      macro_fit_shadow: 80,
      score_short: 95 - index * 0.5,
      score_mid: 90 - index * 0.5,
    }));
    const state = await fixture(page, { rows, marketPayload: { market_regime: {
      score: 80, label: '偏强', index_trend_score: 80, market_momentum_score: 75,
      market_breadth_score: 70, market_volume_score: 65, risk_appetite_score: 75,
      risk_on_spread_score: 60, warnings: [],
    } } });
    await page.getByRole('combobox', { name: '最多显示数量', exact: true }).click();
    await page.getByRole('option', { name: 'Top 40', exact: true }).click();
    await page.locator('button.scan-trigger').click();
    await expect(page.getByText('T000', { exact: true }).filter({ visible: true })).toBeVisible();

    const previous = page.getByRole('button', { name: '上一页', exact: true });
    const next = page.getByRole('button', { name: '下一页', exact: true });
    await expect(previous).toBeVisible();
    await expect(next).toBeVisible();
    for (const button of [previous, next]) {
      const box = await button.boundingBox();
      expect(box).not.toBeNull();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    await expect(page.getByText('T000', { exact: true }).filter({ visible: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    await next.click();
    await expect(page.getByText('T020', { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(page.getByText('T000', { exact: true }).filter({ visible: true })).toHaveCount(0);
    await expect(page.getByText('2 / 2', { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

    await previous.click();
    await expect(page.getByText('T000', { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(page.getByText('T020', { exact: true }).filter({ visible: true })).toHaveCount(0);
    await expect(page.getByText('1 / 2', { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width === 320) {
      await mkdir('test-results/audit-fixes', { recursive: true });
      await page.screenshot({ path: 'test-results/audit-fixes/screener-pager-320.png', animations: 'disabled' });
    }
    expect(state.errors).toEqual([]);
  });
}

test.describe('chart exchange time', () => {
  test.use({ timezoneId: 'Asia/Tokyo' });
  test('real chart axis, tooltip and indicator headers agree in a non-US timezone', async ({ page }) => {
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.goto('/visual-tests/support/indicator-harness.html?scenario=empty');
    const readout = page.locator('[data-indicator-header="volume"]');
    await expect(readout).toContainText('2026-06-09');
    await expect(readout).not.toContainText('2026-06-10');
    const initial = await page.evaluate(() => {
      const chart = window.indicatorTest.getChart(), option = chart.getOption();
      return { labels: option.xAxis[0].data, count: window.indicatorTest.bars.length };
    });
    expect(initial.labels.at(-1)).toBe('06-09');
    expect(initial.labels).toHaveLength(initial.count);
    await page.getByRole('tab', { name: '5分', exact: true }).click();
    await expect(readout).toContainText('2026-06-09 17:00 ET');
    // React readouts commit before the ECharts option update settles.
    await expect.poll(() => page.evaluate(() => window.indicatorTest.getChart()?.getOption().xAxis[0].data.at(-1)))
      .toBe('06-09 17:00');
    const detail = await page.evaluate(() => {
      const chart = window.indicatorTest.getChart(), option = chart.getOption();
      const index = window.indicatorTest.bars.length - 1;
      const title = option.tooltip[0].formatter([{ seriesType: 'candlestick', dataIndex: index }]);
      return { label: option.xAxis[0].data.at(-1), title, count: option.xAxis[0].data.length };
    });
    expect(detail.label).toBe('06-09 17:00');
    expect(detail.title).toContain('2026-06-09 17:00 ET');
    expect(detail.count).toBe(initial.count);
  });
});
