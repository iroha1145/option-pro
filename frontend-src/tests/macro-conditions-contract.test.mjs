// Optix 宏观环境 前端契约（node --experimental-strip-types --test）
//
// 覆盖：live 归一器（null 不变 0、未知枚举安全回退、模块顺序）、六种状态在 UI 层
// 的判定、驱动列表排序、mock fixture 的 mock 标记与形状、组件只用设计令牌。
// 全部离线：不发任何网络请求。
//
// 归一器带 `@/` 别名导入，node 的解析器读不了，因此沿用
// catalysts-source-ui-contract.test.mjs 已有做法：用 typescript 转译后放进 vm，
// 由测试提供依赖桩。这样断言的是真实执行结果，不是源码文本。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(here, '..', 'src');

function liveHelpers() {
  const asRec = (value) =>
    value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const unwrap = (body, ...keys) => {
    if (Array.isArray(body)) return body;
    const row = asRec(body);
    for (const key of keys) {
      if (Array.isArray(row[key])) return row[key];
    }
    return [];
  };
  return {
    asRec,
    unwrap,
    pickB: (row, ...keys) => {
      for (const key of keys) if (typeof row[key] === 'boolean') return row[key];
      return null;
    },
    pickN: (row, ...keys) => {
      for (const key of keys) {
        const raw = row[key];
        const value = typeof raw === 'string' ? Number(raw) : raw;
        if (typeof value === 'number' && Number.isFinite(value)) return value;
      }
      return null;
    },
    pickS: (row, ...keys) => {
      for (const key of keys) if (typeof row[key] === 'string' && row[key]) return row[key];
      return null;
    },
  };
}

/**
 * 把一个使用 `@/` 别名的 TS 模块转译后在**当前 realm** 内求值。
 *
 * 刻意不用 vm.createContext：新 context 有自己的 intrinsics，模块里创建的对象
 * 原型来自另一个 realm，deepStrictEqual 会因原型不同而误报不相等。
 */
function loadModule(relative, resolve) {
  const source = fs.readFileSync(path.join(srcDir, relative), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  const evaluate = new Function('module', 'exports', 'require', compiled);
  evaluate(module, module.exports, resolve);
  return module.exports;
}

const LEAF = loadModule('lib/macroModules.ts', (id) => {
  throw new Error(`unexpected import: ${id}`);
});

function macroMockModule() {
  return loadModule('mocks/macro.ts', (id) => {
    if (id === './rng') return loadModule('mocks/rng.ts', () => ({}));
    if (id === '@/lib/macroModules') return LEAF;
    if (id === '@/api/modules/macro') return {};
    throw new Error(`unexpected import: ${id}`);
  });
}

const MOCKS = macroMockModule();
const { MOCK_REASON, getMacroConditions, getMacroHistory, getMacroModuleDetail } = MOCKS;

const MACRO_API = loadModule('api/modules/macro.ts', (id) => {
  if (id === '../client') {
    return {
      get: async () => ({}),
      post: async () => ({}),
      mockOr: (_mock, live) => live(),
      toQuery: (params) =>
        new URLSearchParams(
          Object.entries(params).filter(([, value]) => value !== undefined),
        ).toString(),
    };
  }
  if (id === '../live') return liveHelpers();
  if (id === '@/lib/macroModules') return LEAF;
  if (id === '@/mocks/macro') return MOCKS;
  throw new Error(`unexpected import: ${id}`);
});

const {
  MACRO_MODULE_ORDER,
  mapConditions,
  mapHistory,
  mapModuleDetail,
  mapRefresh,
} = MACRO_API;

const HINTS = loadModule('lib/scoreHints.ts', (id) => {
  throw new Error(`unexpected import: ${id}`);
});
const { MACRO_FACTOR_HINTS, MACRO_MODULE_HINTS, SCORE_HINTS_MACRO } = HINTS;

const LEAF_ORDER = LEAF.MACRO_MODULE_ORDER;

/* ---------------------------------- 归一器 ---------------------------------- */

test('module order is one shared constant and matches the backend registry order', () => {
  assert.deepEqual([...MACRO_MODULE_ORDER], [...LEAF_ORDER]);
  assert.deepEqual(
    [...MACRO_MODULE_ORDER],
    ['liquidity', 'funding', 'treasury', 'rates', 'credit', 'risk', 'external'],
  );
});

test('mapper keeps backend null as null and never substitutes 0', () => {
  const mapped = mapConditions({
    status: 'degraded',
    as_of: '2026-07-24T22:30:00Z',
    data_through: '2026-07-23',
    scoring_version: 'optix-macro-score-v1',
    history_basis: 'mixed',
    composite: {
      score: 41.2,
      score_change_7d: null,
      confidence: null,
      regime: null,
      valid_module_count: 6,
      total_module_count: 7,
      snapshot_date: '2026-07-24',
      formatted_score: '41.2 分',
    },
    modules: [
      {
        module_id: 'risk',
        display_name_zh: '风险',
        display_name_en: 'RISK',
        score: null,
        score_change_7d: null,
        confidence: null,
        valid_factor_count: 1,
        total_factor_count: 4,
        minimum_valid_factors: 3,
        data_through: null,
        status: 'insufficient_factors',
      },
    ],
    drivers: { improving: [], deteriorating: [] },
    warnings: ['fred_unavailable'],
    sources: ['Cboe Global Markets'],
  });
  assert.equal(mapped.composite.score, 41.2);
  assert.equal(mapped.composite.scoreChange7d, null);
  assert.equal(mapped.composite.confidence, null);
  assert.equal(mapped.composite.regime, null);
  assert.equal(mapped.modules[0].score, null);
  assert.equal(mapped.modules[0].dataThrough, null);
  assert.equal(mapped.historyBasis, 'mixed');
  assert.deepEqual(mapped.warnings, ['fred_unavailable']);
  // 绝不出现 0 冒充缺失
  assert.notEqual(mapped.composite.scoreChange7d, 0);
});

test('an unknown status falls back to unavailable rather than pretending to be active', () => {
  assert.equal(mapConditions({ status: 'brand_new_state' }).status, 'unavailable');
  assert.equal(mapConditions({}).status, 'unavailable');
  assert.equal(mapConditions(null).status, 'unavailable');
  for (const known of [
    'active',
    'degraded',
    'stale',
    'unavailable',
    'disabled',
    'insufficient_history',
  ]) {
    assert.equal(mapConditions({ status: known }).status, known);
  }
});

test('an unknown history basis and factor status fall back safely', () => {
  assert.equal(mapConditions({ history_basis: 'guesswork' }).historyBasis, null);
  const detail = mapModuleDetail({
    status: 'active',
    module_id: 'risk',
    factors: [{ factor_id: 'vix', status: 'who_knows', history_basis: 'nope' }],
  });
  assert.equal(detail.factors[0].status, 'missing');
  assert.equal(detail.factors[0].historyBasis, null);
});

test('modules are returned in registry order regardless of response order', () => {
  const mapped = mapConditions({
    modules: [
      { module_id: 'external' },
      { module_id: 'liquidity' },
      { module_id: 'risk' },
      { module_id: 'funding' },
    ],
  });
  assert.deepEqual(
    mapped.modules.map((item) => item.moduleId),
    ['liquidity', 'funding', 'risk', 'external'],
  );
});

test('an unregistered module id is dropped instead of rendered', () => {
  const mapped = mapConditions({ modules: [{ module_id: 'crypto' }, { module_id: 'credit' }] });
  assert.deepEqual(mapped.modules.map((item) => item.moduleId), ['credit']);
});

test('history points keep null scores and only registry module keys', () => {
  const mapped = mapHistory({
    status: 'active',
    days: 90,
    points: [
      {
        date: '2026-07-24',
        score: null,
        confidence: null,
        regime: null,
        history_basis: 'latest_revised_backfill',
        module_scores: { liquidity: 41.3, unknown_module: 99 },
      },
      { score: 50 },
    ],
  });
  assert.equal(mapped.points.length, 1, 'a point without a date is dropped');
  assert.equal(mapped.points[0].score, null);
  assert.deepEqual(Object.keys(mapped.points[0].moduleScores), ['liquidity']);
});

test('factor payloads carry units, formulas and honest missing-input lists', () => {
  const detail = mapModuleDetail({
    status: 'active',
    module_id: 'funding',
    factors: [
      {
        factor_id: 'collateral_repo_friction',
        module_id: 'funding',
        display_name_zh: '抵押品回购摩擦',
        description_zh: 'SOFR 减 OBFR',
        formula_version: 'optix-macro-factor-v1',
        raw_value: -0.06,
        formatted_value: '-0.060 个百分点',
        signed_value: -0.06,
        formatted_signed_value: '-0.060 个百分点',
        unit: { unit: 'percentage_points', symbol_zh: '个百分点', decimals: 3 },
        score: 62.5,
        score_method: 'supportive_low_percentile',
        direction: 'low',
        raw_change_7d: null,
        formatted_raw_change_7d: null,
        score_change_7d: null,
        status: 'stale',
        data_through: '2026-07-10',
        missing_inputs: ['OBFR'],
        stale_inputs: ['SOFR'],
        source: ['纽约联储'],
      },
    ],
  });
  const factor = detail.factors[0];
  assert.equal(factor.unit.unit, 'percentage_points');
  assert.equal(factor.unit.decimals, 3);
  assert.equal(factor.formulaVersion, 'optix-macro-factor-v1');
  assert.equal(factor.rawChange7d, null);
  assert.equal(factor.formattedRawChange7d, null);
  assert.equal(factor.status, 'stale');
  assert.deepEqual(factor.missingInputs, ['OBFR']);
  assert.deepEqual(factor.staleInputs, ['SOFR']);
  assert.deepEqual(factor.sources, ['纽约联储']);
});

test('refresh mapper surfaces reuse reasons without inventing an error code', () => {
  assert.deepEqual(
    mapRefresh({
      request_id: 'act_1',
      status: 'queued',
      reason: 'queued',
      reused: false,
      cooldown_until: null,
      cooldown_seconds: 300,
      error_code: null,
    }),
    {
      requestId: 'act_1',
      status: 'queued',
      reason: 'queued',
      reused: false,
      cooldownUntil: null,
      cooldownSeconds: 300,
      errorCode: null,
    },
  );
  const cooling = mapRefresh({ reason: 'cooldown', reused: true, error_code: 'macro_refresh_cooldown' });
  assert.equal(cooling.reason, 'cooldown');
  assert.equal(cooling.reused, true);
  assert.equal(cooling.errorCode, 'macro_refresh_cooldown');
});

/* ------------------------------- 面板状态判定 ------------------------------- */

test('every documented status is representable end to end', () => {
  const statuses = [
    'active',
    'degraded',
    'stale',
    'unavailable',
    'disabled',
    'insufficient_history',
  ];
  for (const status of statuses) {
    const mapped = mapConditions({ status, composite: null, modules: [] });
    assert.equal(mapped.status, status);
    assert.equal(mapped.composite, null);
    assert.deepEqual(mapped.modules, []);
  }
});

test('disabled carries a machine reason the panel can branch on', () => {
  const mapped = mapConditions({
    status: 'disabled',
    reason: 'fred_api_key_missing',
    warnings: ['fred_api_key_missing'],
  });
  assert.equal(mapped.status, 'disabled');
  assert.equal(mapped.reason, 'fred_api_key_missing');
});

test('drivers keep at most three entries per side and preserve backend order', () => {
  const mapped = mapConditions({
    drivers: {
      improving: [
        { factor_id: 'vix', display_name_zh: 'VIX 波动率', score: 70, score_change_7d: 12.5 },
        { factor_id: 'nfci', display_name_zh: 'NFCI', score: 60, score_change_7d: 8.1 },
      ],
      deteriorating: [
        { factor_id: 'wti_oil', display_name_zh: 'WTI', score: 20, score_change_7d: -14.2 },
      ],
    },
  });
  assert.deepEqual(
    mapped.drivers.improving.map((item) => item.factorId),
    ['vix', 'nfci'],
  );
  assert.equal(mapped.drivers.improving[0].scoreChange7d, 12.5);
  assert.equal(mapped.drivers.deteriorating[0].scoreChange7d, -14.2);
  assert.ok(mapped.drivers.improving.length <= 3);
});

/* --------------------------------- mock 层 --------------------------------- */

test('mock fixtures are clearly labelled as synthetic', () => {
  const conditions = getMacroConditions();
  assert.equal(MOCK_REASON, 'mock_fixture');
  assert.equal(conditions.reason, MOCK_REASON);
  assert.ok(conditions.warnings.includes(MOCK_REASON));
  assert.match(conditions.sources.join(' '), /mock|合成/);
  assert.equal(conditions.modules.length, 7);
  assert.equal(conditions.composite.validModuleCount, 7);
  assert.equal(conditions.scoringVersion, 'optix-macro-score-v1');
});

test('mock module detail returns the real per-module factor counts', () => {
  const expected = {
    liquidity: 5,
    funding: 6,
    treasury: 3,
    rates: 3,
    credit: 4,
    risk: 4,
    external: 5,
  };
  for (const [moduleId, count] of Object.entries(expected)) {
    const detail = getMacroModuleDetail(moduleId);
    assert.equal(detail.factors.length, count, moduleId);
    assert.equal(detail.moduleId, moduleId);
  }
});

test('mock history marks the older span as revision-based backfill', () => {
  const history = getMacroHistory(1825);
  assert.ok(history.points.length > 10);
  const bases = new Set(history.points.map((point) => point.historyBasis));
  assert.ok(bases.has('latest_revised_backfill'));
  for (const point of history.points) {
    assert.match(point.date, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(point.score >= 0 && point.score <= 100);
  }
});

test('no third-party product name appears in the macro frontend sources', async () => {
  const files = [
    'api/modules/macro.ts',
    'mocks/macro.ts',
    'lib/macroModules.ts',
    'components/market/macro/MacroConditionsPanel.tsx',
    'components/market/macro/CompositeCard.tsx',
    'components/market/macro/MacroHistoryChart.tsx',
    'components/market/macro/ModuleCard.tsx',
    'components/market/macro/ModuleGrid.tsx',
    'components/market/macro/DriverList.tsx',
    'components/market/macro/FactorDetails.tsx',
    'components/market/macro/FactorRow.tsx',
  ];
  for (const relative of files) {
    const text = await readFile(path.join(srcDir, relative), 'utf8');
    for (const forbidden of ['MacroDial', 'macrodial', 'bhadial', '53.8']) {
      assert.ok(!text.includes(forbidden), `${relative} must not mention ${forbidden}`);
    }
  }
});

test('macro components use design tokens only — no hardcoded colours', async () => {
  const componentDir = path.join(srcDir, 'components', 'market', 'macro');
  const files = [
    'MacroConditionsPanel.tsx',
    'CompositeCard.tsx',
    'ModuleCard.tsx',
    'ModuleGrid.tsx',
    'DriverList.tsx',
    'FactorDetails.tsx',
    'FactorRow.tsx',
    'MacroHistoryChart.tsx',
  ];
  // A literal colour would break the moment the palette moves; the chart layer
  // is allowed to read the shared CH palette because that is the existing
  // single source for canvas colours.
  const forbidden = /#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/;
  for (const file of files) {
    const text = await readFile(path.join(componentDir, file), 'utf8');
    const offenders = text
      .split('\n')
      .map((line, index) => [index + 1, line])
      .filter(([, line]) => forbidden.test(line));
    assert.deepEqual(offenders, [], `${file} must not hardcode colours`);
  }
});

test('every module and factor referenced by the panel has hint copy', () => {
  for (const moduleId of MACRO_MODULE_ORDER) {
    assert.ok(MACRO_MODULE_HINTS[moduleId], `missing module hint ${moduleId}`);
    assert.ok(MACRO_MODULE_HINTS[moduleId].body.length > 10);
  }
  assert.equal(Object.keys(MACRO_FACTOR_HINTS).length, 30);
  for (const [factorId, hint] of Object.entries(MACRO_FACTOR_HINTS)) {
    assert.ok(hint.title.includes('0–100 分'), factorId);
    assert.ok(hint.body.length > 10, factorId);
    assert.ok(hint.note.includes('方向：'), factorId);
  }
  assert.ok(SCORE_HINTS_MACRO.macroComposite.body.includes('至少 5 个模块'));
  assert.ok(SCORE_HINTS_MACRO.macroComposite.note.includes('不是预测概率'));
  assert.ok(SCORE_HINTS_MACRO.macroHistoryBasis.body.includes('不是当时市场已知的分数'));
});

test('the panel polls macro data at fifteen minutes, not sixty seconds', async () => {
  const text = await readFile(
    path.join(srcDir, 'components', 'market', 'macro', 'MacroConditionsPanel.tsx'),
    'utf8',
  );
  assert.match(text, /const POLL_MS = 15 \* 60 \* 1000;/);
  assert.ok(!text.includes('60_000'), 'macro data is low frequency');
  // 访客看不到 Owner 动作
  assert.match(text, /isOwner \?/);
  assert.match(text, /登录后可手动刷新/);
});

test('the source note states the real sources and the percentile disclaimer', async () => {
  const text = await readFile(
    path.join(srcDir, 'components', 'market', 'macro', 'MacroConditionsPanel.tsx'),
    'utf8',
  );
  for (const phrase of [
    'FRED',
    '纽约联储',
    '联储理事会',
    '芝加哥联储',
    'Cboe',
    'Option Pro 当前股票日线数据源',
    '过去 5 年历史分位，不是预测',
  ]) {
    assert.ok(text.includes(phrase), `source note must mention ${phrase}`);
  }
});

test('the market page places macro between the regime panel and the signal reading', async () => {
  const text = await readFile(path.join(srcDir, 'pages', 'Market.tsx'), 'utf8');
  const regime = text.indexOf('<RegimePanel');
  const macro = text.indexOf('<MacroConditionsPanel');
  const signals = text.indexOf('<SignalsReading');
  assert.ok(regime > -1 && macro > -1 && signals > -1);
  assert.ok(regime < macro && macro < signals, 'macro sits at B4');
  assert.match(text, /B4 宏观环境/);
  assert.match(text, /B5 信号解读 \+ B6 强度分布/);
  assert.match(text, /B7 联动卡/);
});

test('useCountUp honours prefers-reduced-motion so the score never sticks at zero', async () => {
  const text = await readFile(path.join(srcDir, 'hooks', 'useCountUp.ts'), 'utf8');
  assert.match(text, /prefers-reduced-motion: reduce/);
  assert.match(text, /if \(reduced\) \{/);
  // 命中 reduced 分支时直接设为目标值，不启动逐帧动画
  assert.match(text, /setValue\(target\);/);
});

test('the macro panel adds no high-frequency flashing for score changes', async () => {
  const dir = path.join(srcDir, 'components', 'market', 'macro');
  for (const file of [
    'MacroConditionsPanel.tsx',
    'CompositeCard.tsx',
    'ModuleCard.tsx',
    'DriverList.tsx',
    'FactorDetails.tsx',
    'FactorRow.tsx',
    'MacroHistoryChart.tsx',
  ]) {
    const text = await readFile(path.join(dir, file), 'utf8');
    for (const forbidden of ['animate-pulse', 'animate-tick-flash', 'animate-led-pulse', 'infinite']) {
      assert.ok(!text.includes(forbidden), `${file} must not add flashing (${forbidden})`);
    }
  }
});
