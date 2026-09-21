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
    toQuery: params => new URLSearchParams(Object.entries(params).filter(([, value]) => value != null)).toString(),
  };
  const { strengthApi } = load('api/modules/strength.ts', {
    '../client': client,
    '../marketRead': { marketGet: async (path, options) => { calls.push({ path, options }); return payload; } },
    '../sharedRead': {},
    '../live': live,
    '../macroFields': load('api/macroFields.ts', { './live': live }),
    '@/mocks/fixtures': {},
    '../../i18n/core.ts': { t: text => text },
  });
  const { strengthScanPath } = load('lib/screenerScanFlow.ts', {
    '../api/client.ts': client,
    './liveQuotes.ts': { fallbackQuoteLabel: value => value },
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
