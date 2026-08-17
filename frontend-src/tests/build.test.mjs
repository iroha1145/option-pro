// 构建一致性冒烟（node --test tests/build.test.mjs）。
// CI 会执行 `VITE_API_MODE=live npm run build` 后 `diff -r frontend-src/dist frontend`
// 做全量一致性；这里只做最小冒烟：dist 存在时校验两边 index.html 完全一致，
// dist 不存在（例如未在本机构建）时跳过该断言并说明原因。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { buildStrengthScanRequest } from '../src/components/screener/scanRequest.ts';
import {
  DEFAULT_FILTERS,
  DOLLAR_VOL_OPTIONS,
  screenerStrengthPresentation,
  TOPN_OPTIONS,
} from '../src/components/screener/types.ts';
import {
  buildFocusCycleRequestBody,
  focusCyclePollPath,
} from '../src/components/catalysts/focusCycleRequest.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(here, '..', 'dist'); // frontend-src/dist（本地构建输出）
const artifactDir = path.resolve(here, '..', '..', 'frontend'); // 仓库提交的构建产物
const screenerSource = path.resolve(here, '..', 'src', 'pages', 'Screener.tsx');
const catalystApiSource = path.resolve(
  here,
  '..',
  'src',
  'components',
  'catalysts',
  'api.ts',
);
const focusCycleCardSource = path.resolve(
  here,
  '..',
  'src',
  'components',
  'catalysts',
  'FocusCycleCard.tsx',
);
const filterWorkbenchSource = path.resolve(here, '..', 'src', 'components', 'screener', 'FilterWorkbench.tsx');
const resultCardsSource = path.resolve(here, '..', 'src', 'components', 'screener', 'ResultCards.tsx');
const resultCellsSource = path.resolve(here, '..', 'src', 'components', 'screener', 'cells.tsx');
const accessApiSource = path.resolve(here, '..', 'src', 'api', 'modules', 'access.ts');
const stockApiSource = path.resolve(here, '..', 'src', 'api', 'modules', 'stocks.ts');
const chartApiSource = path.resolve(here, '..', 'src', 'components', 'detail', 'api.ts');
const adminApiSource = path.resolve(here, '..', 'src', 'api', 'modules', 'admin.ts');
const legacyCatalystApiSource = path.resolve(here, '..', 'src', 'api', 'modules', 'catalysts.ts');
const breakoutApiSource = path.resolve(here, '..', 'src', 'api', 'modules', 'breakouts.ts');

test('committed build artifact matches the local Vite build output', async t => {
  if (!existsSync(path.join(distDir, 'index.html'))) {
    t.skip('frontend-src/dist 不存在（本机尚未构建）；一致性由 CI 的 diff -r 全量把关');
    return;
  }
  const [distIndex, artifactIndex] = await Promise.all([
    readFile(path.join(distDir, 'index.html'), 'utf8'),
    readFile(path.join(artifactDir, 'index.html'), 'utf8'),
  ]);
  assert.equal(
    distIndex,
    artifactIndex,
    'frontend-src/dist/index.html 与仓库 frontend/index.html 不一致：请重新构建并同步产物',
  );
});

test('committed artifact directory is servable (index.html present)', () => {
  assert.ok(existsSync(path.join(artifactDir, 'index.html')), '仓库 frontend/index.html 必须存在');
});

test('live screener does not add the mock-only random delay', async () => {
  const source = await readFile(screenerSource, 'utf8');
  assert.match(
    source,
    /const minMs = isMock \? 800 \+ Math\.random\(\) \* 700 : 0;/,
    '选股扫描的随机延时必须只在 mock 模式启用',
  );
  assert.doesNotMatch(
    source,
    /const minMs = 800 \+ Math\.random\(\) \* 700;/,
    'live 模式不得无条件等待随机演示时长',
  );
});

test('default screener request matches the daily live snapshot parameters', () => {
  const request = buildStrengthScanRequest(DEFAULT_FILTERS);
  assert.deepEqual(request.apiParams, {
    band: 'all',
    sort: 'score',
    order: 'desc',
    universe: 'themes',
    timeframe: 'all',
    profile: 'balanced',
    top: 20,
    sector_id: undefined,
    min_price: 5,
    min_avg_dollar_volume: 10_000_000,
    include_options: true,
  });
  assert.deepEqual(request.refreshParameters, {
    universe: 'themes',
    timeframe: 'all',
    profile: 'balanced',
    top: 20,
    sector_id: null,
    min_price: 5,
    min_avg_dollar_volume: 10_000_000,
    include_options: true,
  });
});

test('Owner custom scan keeps the broad and unrestricted controls', () => {
  const request = buildStrengthScanRequest({
    ...DEFAULT_FILTERS,
    topN: 0,
    priceMin: null,
    minDollarVol: 0,
  });
  assert.equal(request.apiParams.top, 120);
  assert.equal(request.apiParams.min_price, 0);
  assert.equal(request.apiParams.min_avg_dollar_volume, 0);
  assert.equal(request.refreshParameters.top, 120);
  assert.equal(request.refreshParameters.min_price, 0);
  assert.equal(request.refreshParameters.min_avg_dollar_volume, 0);
  assert.ok(TOPN_OPTIONS.some((option) => option.value === 0 && option.label === '最多 120'));
  assert.ok(DOLLAR_VOL_OPTIONS.some((option) => option.value === 0 && option.label === '不限成交额'));
});

test('market focus polling uses the cycle endpoint and only forces a consumed nonempty revision', () => {
  assert.equal(
    focusCyclePollPath('mfc_0123456789abcdef0123456789abcdef'),
    '/catalysts/market-focus-cycles/mfc_0123456789abcdef0123456789abcdef',
  );
  assert.deepEqual(buildFocusCycleRequestBody(12, true, 8), {
    trigger: 'manual',
    expected_prepared_revision: 12,
  });
  assert.deepEqual(buildFocusCycleRequestBody(12, false, 8), {
    trigger: 'manual',
    expected_prepared_revision: 12,
    force: true,
  });
  assert.deepEqual(buildFocusCycleRequestBody(0, false, 0), {
    trigger: 'manual',
    expected_prepared_revision: 0,
  });
  assert.deepEqual(buildFocusCycleRequestBody(12, false, 0), {
    trigger: 'manual',
    expected_prepared_revision: 12,
  });
  assert.deepEqual(
    buildFocusCycleRequestBody(
      null,
      null,
      null,
      'mfc_0123456789abcdef0123456789abcdef',
    ),
    {
      trigger: 'manual',
      retry_cycle_id: 'mfc_0123456789abcdef0123456789abcdef',
    },
  );
});

test('previous focus-cycle comparison reads the live history field', async () => {
  const source = await readFile(catalystApiSource, 'utf8');
  assert.match(source, /previous_successful_cycle/);
  assert.doesNotMatch(
    source,
    /上一焦点周期快照暂不可用（契约未提供该端点）/,
  );
});

test('failed focus attempt keeps the latest successful result visible and retryable', async () => {
  const [apiSource, cardSource] = await Promise.all([
    readFile(catalystApiSource, 'utf8'),
    readFile(focusCycleCardSource, 'utf8'),
  ]);
  assert.match(apiSource, /useSuccessfulFallback/);
  assert.match(apiSource, /latest_successful_cycle/);
  assert.match(apiSource, /normalized\.latestAttempt/);
  assert.match(cardSource, /最近一次更新失败，当前展示上次成功结果/);
  assert.match(cardSource, /triggerFocusCycle\(failedCycleId\)/);
  assert.match(cardSource, /重试焦点周期/);
});

test('default catalyst feed requests translated analysis and preserves neutral articles', async () => {
  const source = await readFile(catalystApiSource, 'utf8');
  assert.match(source, /q\.includeUnanalyzed \?\? \(status \? status !== 'completed' : true\)/);
  assert.match(source, /q\.includeNeutral \?\? true/);
  assert.match(source, /include_unanalyzed: includeUnanalyzed/);
  assert.match(source, /include_neutral: includeNeutral/);
  assert.match(source, /\.filter\(\(item\) => item\.titleZh && item\.summaryZh\)/);
  assert.match(source, /includeUnanalyzed: true,\s*includeNeutral: true,/);
});

test('screener waiting state does not invent a percentage', async () => {
  const [page, workbench] = await Promise.all([
    readFile(screenerSource, 'utf8'),
    readFile(filterWorkbenchSource, 'utf8'),
  ]);
  assert.doesNotMatch(page, /setProgress|Math\.exp\(-el/);
  assert.doesNotMatch(workbench, /扫描中[^\n]*\{pct\}|style=\{\{ width: `\$\{pct\}%`/);
  assert.match(workbench, /扫描中 · 等待后台结果/);
  assert.match(workbench, /aria-busy=\{scanning\}/);
});

test('mobile screener gives the sector field a full row and keeps chips on one line', async () => {
  const source = await readFile(filterWorkbenchSource, 'utf8');
  assert.match(source, /data-screener-field="sectors"/);
  assert.match(source, /w-full min-w-0 flex-none sm:w-auto sm:flex-1/);
  assert.match(source, /h-7 shrink-0 items-center whitespace-nowrap/);
});

test('screener result strength colors use stable score bands on mobile and desktop', async () => {
  const expected = [
    [59.9, 'D', 'd', 'bg-ink-300'],
    [60, 'C', 'c', 'bg-warn-600'],
    [69.9, 'C', 'c', 'bg-warn-600'],
    [70, 'B', 'b', 'bg-brand-400'],
    [79.9, 'B', 'b', 'bg-brand-400'],
    [80, 'A', 'a', 'bg-brand-600'],
    [84.9, 'A', 'a', 'bg-brand-600'],
    [85, 'A', 'a-high', 'bg-up-600'],
    [89.9, 'A', 'a-high', 'bg-up-600'],
    [90, 'S', 's', 'bg-up-700'],
  ];
  for (const [score, band, tone, barClass] of expected) {
    const presentation = screenerStrengthPresentation(score);
    assert.equal(presentation.band, band);
    assert.equal(presentation.tone, tone);
    assert.equal(presentation.barClass, barClass);
  }

  const [cards, cells] = await Promise.all([
    readFile(resultCardsSource, 'utf8'),
    readFile(resultCellsSource, 'utf8'),
  ]);
  assert.match(cards, /screenerStrengthPresentation\(r\.strengthScore\)/);
  assert.match(cells, /screenerStrengthPresentation\(score\)/);
  assert.match(cards, /data-strength-band=\{strength\.band\}/);
  assert.match(cards, /data-strength-tone=\{strength\.tone\}/);
});

test('login identity does not masquerade as AI availability', async () => {
  const source = await readFile(accessApiSource, 'utf8');
  assert.match(source, /get\('\/ai\/status'\)/);
  assert.match(source, /get\('\/runtime-settings'\)/);
  assert.match(source, /manual_analysis_enabled/);
  assert.match(source, /capabilityEnabled/);
  assert.doesNotMatch(source, /get\('\/catalysts\/status'\)/);
  assert.doesNotMatch(source, /aiEnabled:\s*s\.access_mode\s*===\s*'private_network'\s*\|\|\s*s\.logged_in/);
});

test('chart controls map one-to-one to the backend candle intervals', async () => {
  const [stocks, detail] = await Promise.all([
    readFile(stockApiSource, 'utf8'),
    readFile(chartApiSource, 'utf8'),
  ]);
  assert.doesNotMatch(stocks, /CHART_RANGE_MAP/);
  assert.match(detail, /value: '5m', label: __t\('5分'\)/);
  assert.match(detail, /value: '15m', label: __t\('15分'\)/);
  assert.match(detail, /value: '1h', label: __t\('1小时'\)/);
  assert.match(detail, /value: '1d', label: __t\('日线'\)/);
  assert.match(detail, /value: '1w', label: __t\('周线'\)/);
});

test('missing worker health fields fail closed', async () => {
  const source = await readFile(adminApiSource, 'utf8');
  assert.match(source, /const enabled = pickB\(r, 'enabled'\) \?\? false/);
  assert.match(
    source,
    /pickB\(r, 'healthy'\)[\s\S]*?\?\? \(enabled && !\[[\s\S]*?'disabled'[\s\S]*?\]\.includes\(status\)\)/,
  );
  assert.doesNotMatch(source, /enabled: pickB\(r, 'enabled'\) \?\? true/);
});

test('stock news uses Chinese projection and excludes non-directional items', async () => {
  const source = await readFile(legacyCatalystApiSource, 'utf8');
  assert.match(source, /pickS\(r, 'title_zh', 'titleZh', 'title'\)/);
  assert.match(source, /include_unanalyzed=false&include_neutral=false/);
});

test('breakout detail retains the live range-persistence metrics', async () => {
  const source = await readFile(breakoutApiSource, 'utf8');
  for (const field of [
    'range_persistence_slope_5d',
    'range_persistence_ratio_10d',
    'range_persistence_global_percentile',
    'range_persistence_sector_percentile',
  ]) {
    assert.match(source, new RegExp(field));
  }
});
