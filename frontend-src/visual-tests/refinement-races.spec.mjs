import { test, expect } from '@playwright/test';

const harness = '/visual-tests/support/refinement-races.html';
const modulePath = '**/api/macro/conditions/modules/risk';
function detail(name) {
  return {
    status: 'active', module_id: 'risk', display_name_zh: '风险', display_name_en: 'RISK',
    as_of: '2026-10-02T22:30:00Z', snapshot_date: '2026-10-02', scoring_version: 'optix-macro-score-v1',
    module: null,
    factors: [{
      factor_id: name === '新快照因子' ? 'new' : 'old', module_id: 'risk', display_name_zh: name,
      description_zh: '浏览器回归合成数据', formula_version: 'optix-macro-factor-v1',
      raw_value: 1.234, formatted_value: '1.234 个百分点', signed_value: null, formatted_signed_value: null,
      unit: { unit: 'percentage_points', symbol_zh: '个百分点', decimals: 3 }, score: 40,
      score_method: 'supportive_low_percentile', direction: 'low', raw_change_7d: 0.012,
      formatted_raw_change_7d: '+0.012 个百分点', score_change_7d: 3.4, confidence: 1,
      valid_observations: 1258, minimum_history: 252, status: 'ok', data_through: '2026-10-02',
      history_basis: 'latest_revised_backfill', missing_inputs: [], stale_inputs: [], source: ['纽约联储'],
    }],
  };
}
async function settleDom(page) {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
for (const oldResult of ['success', 'failure']) {
  test(`late old factor ${oldResult} cannot replace the committed new snapshot`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const requests = [];
    await page.route(modulePath, (route) => { requests.push(route); });
    await page.goto(harness);
    await page.getByRole('button', { name: /风险/ }).click();
    await expect.poll(() => requests.length).toBe(1);
    await page.getByRole('button', { name: '切换新快照' }).click();
    await expect(page.getByRole('status')).toHaveText('快照：new');
    await expect.poll(() => requests.length).toBe(2);
    await requests[1].fulfill({ json: detail('新快照因子') });
    const row = page.getByRole('rowheader', { name: '新快照因子' });
    await expect(row).toBeVisible();
    const oldResponse = page.waitForResponse((response) => response.url().endsWith('/api/macro/conditions/modules/risk'));
    await requests[0].fulfill(oldResult === 'success'
      ? { json: detail('旧快照因子') }
      : { status: 503, json: { code: 503, message: '旧快照请求失败' } });
    await (await oldResponse).finished();
    await settleDom(page);
    await expect(row).toBeVisible();
    await expect(page.getByText('旧快照因子', { exact: true })).toHaveCount(0);
    await expect(page.getByText('旧快照请求失败')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '重试', exact: true })).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}
