import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { buildStrengthScanRequest } from '../src/components/screener/scanRequest.ts';
import { DEFAULT_FILTERS } from '../src/components/screener/types.ts';

function load(relativePath, imports = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(new URL(`../src/${relativePath}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { exports, require: key => {
    if (key in imports) return imports[key];
    throw new Error(`Unexpected dependency: ${key}`);
  }});
  return exports;
}

const live = load('api/live.ts', { '../i18n/core.ts': { t: text => text } });
const plain = value => JSON.parse(JSON.stringify(value));

function apiFor(payload) {
  const calls = [];
  const client = {
    mockOr: (_mock, request) => request(),
    get: async () => payload,
    toQuery: params => new URLSearchParams(Object.entries(params).filter(([, value]) => value != null)).toString(),
  };
  const { strengthApi } = load('api/modules/strength.ts', {
    '../client': client,
    '../marketRead': { marketGet: async (path, options) => { calls.push({ path, options }); return payload; } },
    '../sharedRead': { sharedGlobalGet: async () => payload },
    '../live': live,
    '../macroFields': load('api/macroFields.ts', { './live': live }),
    '@/mocks/fixtures': {},
    '../../i18n/core.ts': { t: text => text },
  });
  const { strengthScanPath } = load('lib/screenerScanFlow.ts', {
    '../api/client.ts': client,
  });
  return { strengthApi, strengthScanPath, calls };
}

test('全市场响应映射完整目录、缺数与实际评分数量，不拿返回行数代替扫描覆盖', async () => {
  const { strengthApi } = apiFor({
    rows: [], universe: 'all_market', universe_count: 11_752, screened_count: 11_600,
    observation_n: 483,
    coverage: {
      directory_count: 12_000, excluded_count: 248, missing_session_count: 152,
      short_history_count: 900, scored_count: 11_600, status: 'complete',
    },
  });
  const mapped = await strengthApi.scanEnvelope();
  assert.equal(mapped.universe, 'all_market');
  assert.equal(mapped.universeCount, 11_752);
  assert.equal(mapped.screenedCount, 11_600);
  assert.equal(mapped.observationN, 483);
  assert.deepEqual(plain(mapped.coverage), {
    directoryCount: 12_000, excludedCount: 248, missingSessionCount: 152,
    shortHistoryCount: 900, scoredCount: 11_600, status: 'complete',
  });
});

test('覆盖字段缺失或为 null 时保留未知，不编造成零', async () => {
  for (const coverage of [undefined, null, {}, {
    directory_count: null, excluded_count: null, missing_session_count: null,
    short_history_count: null, scored_count: null, status: null,
  }]) {
    const { strengthApi } = apiFor({ rows: [], coverage });
    const mapped = await strengthApi.scanEnvelope();
    assert.equal(mapped.universe, null);
    assert.deepEqual(plain(mapped.coverage), {
      directoryCount: null, excludedCount: null, missingSessionCount: null,
      shortHistoryCount: null, scoredCount: null, status: null,
    });
  }
});

test('覆盖统计中的真实零值保持为零', async () => {
  const { strengthApi } = apiFor({
    rows: [], universe_count: 0, screened_count: 0, observation_n: 0,
    coverage: { directory_count: 0, excluded_count: 0, missing_session_count: 0,
      short_history_count: 0, scored_count: 0, status: 'complete' },
  });
  const mapped = await strengthApi.scanEnvelope();
  assert.equal(mapped.universeCount, 0);
  assert.equal(mapped.screenedCount, 0);
  assert.equal(mapped.observationN, 0);
  assert.deepEqual(plain(mapped.coverage), {
    directoryCount: 0, excludedCount: 0, missingSessionCount: 0,
    shortHistoryCount: 0, scoredCount: 0, status: 'complete',
  });
});

test('默认实时读取、轮询路径和刷新请求均使用同一全市场范围', async () => {
  const { strengthApi, strengthScanPath, calls } = apiFor({ rows: [] });
  await strengthApi.scanEnvelope();
  assert.equal(calls[0].path, '/strength/scan?universe=all_market');
  assert.equal(strengthScanPath({}), calls[0].path);
  const request = buildStrengthScanRequest(DEFAULT_FILTERS);
  assert.equal(request.apiParams.universe, 'all_market');
  assert.equal(request.refreshParameters.universe, 'all_market');
  await strengthApi.scanEnvelope(request.apiParams, true);
  assert.equal(calls[1].path, strengthScanPath(request.apiParams));
  assert.equal(calls[1].options.force, true);
  const query = new URL(calls[1].path, 'https://fixture.invalid').searchParams;
  for (const [key, value] of Object.entries(request.refreshParameters)) {
    assert.equal(query.get(key), value == null ? null : String(value), `读取与刷新参数必须一致: ${key}`);
  }
});


test('当前市场接口只映射六维形态，保留零值和缺失值', async () => {
  const { strengthApi } = apiFor({ market_regime: {
    score: 57.1, index_trend_score: 80, market_momentum_score: 0,
    market_breadth_score: null, market_volume_score: 54,
    risk_appetite_score: 69.7, risk_on_spread_score: 62.6,
  } });
  const data = plain(await strengthApi.market());
  assert.deepEqual(Object.keys(data), ['regime']);
  assert.equal(data.regime.dims.indexTrend, 80);
  assert.equal(data.regime.dims.momentum, 0);
  assert.equal(data.regime.dims.breadth, null);
  assert.deepEqual(plain(await apiFor({}).strengthApi.market()), {});
});

test('当前档位只显示真实枚举名称，旧重复接口不再导出', async () => {
  const { strengthApi } = apiFor({ profiles: ['conservative', 'balanced', 'aggressive'], sectors: [] });
  assert.deepEqual(plain(await strengthApi.profilesMeta()), {
    profiles: [{ id: 'conservative', name: '稳健' }, { id: 'balanced', name: '均衡' }, { id: 'aggressive', name: '进取' }],
    sectors: [],
  });
  assert.equal('profiles' in strengthApi, false);
  assert.equal('scan' in strengthApi, false);
});

test('真实八因子和有效权重保留，缺失行业读数不变成零', async () => {
  const keys = ['T', 'M', 'S', 'B', 'P', 'V', 'R', 'G'];
  const dims = keys.map((key, index) => ({ key: `factor_${key}`, label: key, value: index === 7 ? null : index * 10 }));
  const weights = { T: 0.2, M: 0.3, S: 0.1, B: 0.1, P: 0.1, V: 0.1, R: 0.1 };
  const { strengthApi } = apiFor({ rows: [{ ticker: 'AAA', price: 100, final_score: 80, factor_dims: dims, effective_weights: weights }] });
  const row = plain((await strengthApi.scanEnvelope()).rows[0]);
  assert.deepEqual(row.subscoreDims, dims);
  assert.deepEqual(row.effectiveWeights, weights);
  assert.equal('subscores' in row, false);
  assert.equal(row.subscoreDims[0].value, 0);
  assert.equal(row.subscoreDims[7].value, null);
});
