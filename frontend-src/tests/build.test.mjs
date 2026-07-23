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
});
