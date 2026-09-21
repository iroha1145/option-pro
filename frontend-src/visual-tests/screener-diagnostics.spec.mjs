import { expect, test } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

function diagnostic(url) {
  const ticker = decodeURIComponent(url.pathname.split('/').at(-1));
  return {
    ticker, profile: url.searchParams.get('profile'), horizon: url.searchParams.get('timeframe'),
    served_session: '2026-09-18', snapshot_saved_at: '2026-09-21T10:00:00+00:00',
    compute_version: 'limited-all-market-v1.3', feature_version: 'us-eod-research-features-v1.6',
    source_hash: 'abc'.repeat(20), data_status: 'scored', coverage: { status: 'ok' },
    display: { in_observation: false, in_composite: false, display_reason: 'scored_but_rejected' },
    paths: [{
      security_id: ticker, sector_context: 'semiconductors', algorithm_id: 'A_trend_quality',
      track: 'PRICE_ONLY_DIAGNOSTIC', stock_or_etf_track: 'stock', status: 'rejected', score: 80,
      price: 100, rejection_reasons: ['HIGH_ATR'], adv20: 25_000_000,
      atr_pct: 5.5, sector_median_atr_pct: 2.0, atr_threshold_pct: 3.5,
      atr_reference_n: 10000, atr_reference_source: 'legacy_all_tracks_missing_industry', atr_reference_policy: 'legacy',
      extension_atr: 1.2, extension_limit_atr: 2,
      capability_flags: { dollar_liquidity_verified: false, volume_session_verified: false },
      factors: { T: 80, R: 80 }, configured_weights: { T: .75, R: .25 }, track_weights: { T: .75, R: .25 },
      effective_weights: { T: .75, R: .25 }, score_components: { T: 60, R: 20 },
      common_gate_checks: { atr: false, adv20: true }, gate_results_source: 'upstream_full_model',
      score_gate_checks: { coverage_ratio: 1, coverage_min: .9, coverage_passed: true, score_floor: 65, score_floor_passed: true, required_factors_passed: true },
    }],
  };
}

async function open(page, width) {
  await page.setViewportSize({ width, height: 900 });
  await page.addInitScript(() => localStorage.setItem('optix:locale', 'zh-CN'));
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    return ['127.0.0.1', 'localhost'].includes(url.hostname) ? route.continue() : route.abort();
  });
  await page.goto('/screener');
  const panel = page.getByRole('region', { name: '按代码查询选股诊断' });
  await expect(panel).toBeVisible();
  return panel;
}

for (const width of [1440, 390, 320]) {
  test(`a rejected security has real explanations and unverified proxy labels at ${width}px`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const panel = await open(page, width);
    let requests = 0;
    await page.route('**/api/strength/diagnostics/**', (route) => {
      requests += 1;
      return route.fulfill({ json: diagnostic(new URL(route.request().url())) });
    });
    await panel.getByLabel('证券代码').fill('CRWD');
    await panel.getByRole('button', { name: '查询诊断', exact: true }).click();
    await expect(panel.getByText('有技术分数，但未进入结果', { exact: false })).toBeVisible();
    await panel.locator('summary').first().click();
    await expect(panel.getByText('波动幅度超过门槛', { exact: true }).last()).toBeVisible();
    await expect(panel.getByText('实际波动幅度：5.50%')).toBeVisible();
    await expect(panel.getByText('波动门槛：3.50%')).toBeVisible();
    await expect(panel.getByText('代理值达到数值门槛，资格未认证', { exact: false })).toBeVisible();
    await expect(panel.getByText('最终评分门（当前轨道）', { exact: true })).toBeVisible();
    await expect(panel.getByText('分数贡献：60.00')).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    expect(requests).toBe(1);
    expect(errors).toEqual([]);
    await panel.scrollIntoViewIfNeeded();
    await mkdir('test-results/diagnostics-evidence', { recursive: true });
    await page.screenshot({ path: `test-results/diagnostics-evidence/rejected-${width}.png`, animations: 'disabled' });
  });
}

test('changing profile revokes an older lookup and errors remain distinct from empty data', async ({ page }) => {
  const panel = await open(page, 1440);
  let release;
  await page.route('**/api/strength/diagnostics/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/SLOW')) {
      await new Promise((resolve) => { release = resolve; });
      return route.fulfill({ json: diagnostic(url) });
    }
    return route.fulfill({ status: url.pathname.endsWith('/UNKNOWN') ? 404 : 503,
      json: { detail: { code: 'eod_diagnostics_unavailable', message: 'unavailable' } } });
  });
  await panel.getByLabel('证券代码').fill('SLOW');
  await panel.getByRole('button', { name: '查询诊断', exact: true }).click();
  await expect.poll(() => typeof release).toBe('function');
  await page.getByRole('tablist', { name: '偏好', exact: true }).getByRole('tab', { name: '进取', exact: true }).click();
  release();
  await expect(panel.getByText('完整评分路径', { exact: false })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: '查询诊断', exact: true })).toBeEnabled();
  await panel.getByLabel('证券代码').fill('UNKNOWN');
  await panel.getByRole('button', { name: '查询诊断', exact: true }).click();
  await expect(panel.getByRole('alert')).toHaveText('该代码不在本批次证券目录中。');
  await panel.getByLabel('证券代码').fill('UNAVAILABLE');
  await panel.getByRole('button', { name: '查询诊断', exact: true }).click();
  await expect(panel.getByRole('alert')).toHaveText('该批次尚无完整诊断，请等待扫描完成后重试。');
});

test('case-distinct provider symbols are queried separately and never routed to another security', async ({ page }) => {
  const panel = await open(page, 390);
  const requested = [];
  await page.route('**/api/strength/diagnostics/**', (route) => {
    const url = new URL(route.request().url());
    const symbol = url.pathname.split('/').at(-1);
    requested.push(symbol);
    if (symbol === 'bcpc') return route.fulfill({ status: 409, json: { detail: { code: 'eod_diagnostics_symbol_ambiguous' } } });
    const base = { ...diagnostic(url), requested_ticker: symbol, provider_ticker: symbol };
    return route.fulfill({ json: symbol === 'BCpC'
      ? { ...base, data_status: 'out_of_scope', coverage: { status: 'excluded:UNSUPPORTED_SECURITY_TYPE:SP' }, paths: [], display: null }
      : base });
  });
  await panel.getByLabel('证券代码').fill('BCPC');
  await panel.getByRole('button', { name: '查询诊断', exact: true }).click();
  await expect(panel.locator('strong').filter({ hasText: /^BCPC$/ })).toBeVisible();
  await panel.getByLabel('证券代码').fill('BCpC');
  await panel.getByRole('button', { name: '查询诊断', exact: true }).click();
  await expect(panel.locator('strong').filter({ hasText: /^BCpC$/ })).toBeVisible();
  await expect(panel.getByText('本批次范围外', { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: '查看股票详情' })).toHaveCount(0);
  await panel.getByLabel('证券代码').fill('bcpc');
  await panel.getByRole('button', { name: '查询诊断', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('该代码对应多个不同证券');
  expect(requested).toEqual(['BCPC', 'BCpC', 'bcpc']);
});
