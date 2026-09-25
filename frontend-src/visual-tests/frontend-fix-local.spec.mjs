import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const evidenceDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../output/playwright');

async function fixture(page, options = {}) {
  const state = { indexPrice: 100, indexReads: 0, jobReads: 0, errors: [], ...options };
  const news = { news_id: 'local-news', title_zh: '本地快讯', summary_zh: '浏览器验收用新闻',
    source: 'Local', source_tickers: ['AAPL'], published_at: new Date().toISOString(),
    analysis_status: 'queued', analysis_job_id: 'job-local', url: 'https://example.test/news' };
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh'));
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.route('**/*', (route) => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
    ? route.continue() : route.abort());
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const pathName = url.pathname;
    if (!pathName.startsWith('/api/')) return route.continue();
    if (pathName === '/api/access/status') return route.fulfill({ json: { access_mode: 'password', logged_in: false, account: null } });
    if (pathName === '/api/ai/status') return route.fulfill({ json: { enabled: false } });
    if (pathName === '/api/runtime-settings') return route.fulfill({ json: { settings: { ai: { manual_analysis_enabled: false } } } });
    if (pathName === '/api/market/status') return route.fulfill({ json: {} });
    if (pathName === '/api/market/indices') {
      state.indexReads += 1;
      return route.fulfill({ json: { indices: [{ symbol: '^GSPC', price: state.indexPrice, change_percent: 1 }] } });
    }
    if (pathName === '/api/quotes') return route.fulfill({ json: { quotes: [], status: { enabled: false, allowed: false, connected: false } } });
    if (pathName === '/api/catalysts/feed' && url.searchParams.get('limit') === '12') {
      return route.fulfill({ json: { items: state.showJob ? [news] : [], next_cursor: null,
        summary: { count: state.showJob ? 1 : 0, pending: state.showJob ? 1 : 0 } } });
    }
    if (pathName === '/api/catalysts/news/local-news' && state.showJob) return route.fulfill({ json: news });
    if (pathName === '/api/catalysts/analysis-jobs/job-local' && state.showJob) {
      state.jobReads += 1;
      if (state.jobReads > 1) return route.fulfill({ status: state.jobFailure ?? 503, json: { message: '本地任务状态失败' } });
      // 真实 GET /catalysts/analysis-jobs/{id} 只回 AIJobPublic + submission_source：没有 news_id 与 progress。
      return route.fulfill({ json: { job_id: 'job-local', job_type: 'news_impact', status: 'in_progress',
        model: 'gpt-local', reasoning: 'low', submitted_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        completed_at: null, error_code: null, error_detail: null, retry_after: null, result: null, cached: false,
        cancellable: true, cancel_requested: false, analysis_revision: 1, cycle_revision: null,
        budget_charge_usd: 0, usage: {}, submission_source: 'manual' } });
    }
    return route.fulfill({ status: 503, json: { message: '本地状态读取失败' } });
  });
  return state;
}

async function screenshot(page, name) {
  mkdirSync(evidenceDir, { recursive: true });
  await page.screenshot({ path: path.join(evidenceDir, name), fullPage: false });
}

test('missing market field stays unknown on the market and home pages', async ({ page }) => {
  const state = await fixture(page);
  await page.goto('/market');
  const card = page.getByRole('region', { name: '市场状态', exact: true });
  await expect(card).toContainText('时段未知');
  await expect(card).not.toContainText('休市');
  await screenshot(page, 'market-unknown-desktop.png');
  await page.goto('/');
  await expect(page.getByRole('region', { name: '市场状态', exact: true })).toContainText('时段未知');
  await screenshot(page, 'home-unknown-desktop.png');
  expect(state.errors).toEqual([]);
});

test('index price flash appears for an update and expires', async ({ page }) => {
  const state = await fixture(page);
  await page.clock.install();
  await page.goto('/market');
  const card = page.getByRole('button', { name: '标普500 SPX 详情', exact: true });
  const price = card.locator('.metric-value');
  await expect(price).toContainText('100.00');
  state.indexPrice = 101;
  await page.clock.fastForward(61_000);
  await expect.poll(() => state.indexReads).toBeGreaterThan(1);
  await expect(price).toHaveClass(/tick-flash-up/);
  await screenshot(page, 'market-flash-up-desktop.png');
  await page.clock.fastForward(700);
  await expect(price).not.toHaveClass(/tick-flash-(?:up|down)/);
  expect(state.errors).toEqual([]);
});

test('failed news count reads show uncertainty and a retry on a narrow screen', async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/catalysts');
  await expect(page.getByText('新闻数量暂不可确认', { exact: true })).toBeVisible();
  await expect(page.getByText('暂时无法确认是否有待分析新闻，请稍后重试', { exact: true })).toBeVisible();
  await page.getByText('新闻数量暂不可确认', { exact: true }).scrollIntoViewIfNeeded();
  await screenshot(page, 'news-count-unknown-mobile.png');
  expect(state.errors).toEqual([]);
});

test('repeated job status failures become visible while the original task remains open', async ({ page }) => {
  const state = await fixture(page, { showJob: true });
  await page.goto('/catalysts');
  await page.getByRole('button', { name: '本地快讯', exact: true }).press('Enter');
  const notice = page.getByText('任务状态暂时读不到，正在重试', { exact: true });
  await expect(notice).toBeVisible({ timeout: 18_000 });
  expect(state.jobReads).toBeGreaterThanOrEqual(3);
  await screenshot(page, 'news-job-status-retry-desktop.png');
  expect(state.errors).toEqual([]);
});

test('a missing job record stops polling and names the failure', async ({ page }) => {
  const state = await fixture(page, { showJob: true, jobFailure: 404 });
  await page.goto('/catalysts');
  await page.getByRole('button', { name: '本地快讯', exact: true }).press('Enter');
  await expect(page.getByText('任务记录已不存在', { exact: true })).toBeVisible({ timeout: 10_000 });
  expect(state.jobReads).toBe(2);
  expect(state.errors).toEqual([]);
});
