import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(here, '..', 'src');
const modulePath = path.join(sourceRoot, 'api', 'modules', 'sectors.ts');

function liveHelpers() {
  const asRec = (value) =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? value
      : {};
  return {
    asRec,
    pickB: (row, ...keys) => {
      for (const key of keys) {
        if (typeof row[key] === 'boolean') return row[key];
      }
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
      for (const key of keys) {
        if (typeof row[key] === 'string' && row[key]) return row[key];
      }
      return null;
    }, pickLabel: (row, ...keys) => {
      for (const key of keys) {
        if (typeof row[key] === 'string' && row[key]) return row[key];
      }
      return null;
    },
    unwrap: (body, ...keys) => {
      if (Array.isArray(body)) return body;
      for (const key of keys) {
        if (Array.isArray(body?.[key])) return body[key];
      }
      return [];
    },
  };
}

/**
 * 从 api/modules/strength.ts 里取出真实的 mapMacroFitDrivers。
 *
 * 整个 strength 模块要一堆依赖，而这里只需要那一个纯函数，所以单独编译并跑一遍
 * 它的函数体 —— 拿到的是真代码，不是替身。
 */
function loadRealMacroDriverMapper() {
  const source = fs.readFileSync(
    path.resolve(here, '../src/api/macroFields.ts'),
    'utf8',
  );
  const start = source.indexOf('export function mapMacroFitDrivers');
  assert.ok(start > 0, 'macroFields.ts 里找不到 mapMacroFitDrivers');
  const end = source.indexOf('\n}', start) + 2;
  const body = source
    .slice(start, end)
    .replace('export function', 'function');
  const compiled = ts.transpileModule(body, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const sandbox = { asRec: liveHelpers().asRec, pickS: liveHelpers().pickS, result: null };
  vm.runInNewContext(`${compiled}; result = mapMacroFitDrivers;`, sandbox);
  return sandbox.result;
}

function loadSectorsModule(responses = {}) {
  const source = fs.readFileSync(modulePath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const calls = [];
  const module = { exports: {} };
  const require = (id) => {
    if (id === '../client') {
      return {
        get: async (url) => {
          calls.push(url);
          return responses[url] ?? {};
        },
        mockOr: (_mock, live) => live(),
        toQuery: (params) => new URLSearchParams(params).toString(),
      };
    }
    if (id === '../live') return liveHelpers();
    if (id === '../macroFields') {
      // 加载真实实现，而不是替身：这个映射器决定宏观驱动因素长什么样，
      // 换成假的等于让测试同意任何形状。
      return { mapMacroFitDrivers: loadRealMacroDriverMapper() };
    }
    if (id === '@/mocks/fixtures2') {
      return {
        getSectors: () => [],
        getSectorIvRanking: () => [],
      };
    }
    throw new Error(`unexpected import: ${id}`);
  };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require,
    URLSearchParams,
  });
  return { exports: module.exports, calls };
}

test('板块目录与强度聚合按真实契约映射，缺失数值不补零', () => {
  const { exports } = loadSectorsModule();
  const catalog = exports.mapSectorCatalog({
    sectors: [
      { id: 'semi', name: '半导体', tickers: ['nvda', 'AMD'] },
    ],
  });
  const strength = exports.mapSectorStrengthEnvelope(
    {
      as_of: '2026-07-23T08:00:00Z',
      period: '3mo',
      period_days: 63,
      sectors: [
        {
          sector_id: 'semi',
          name: '半导体',
          count: 2,
          avg_return: 12.5,
          avg_return_1mo: 4.1,
          avg_return_3mo: 12.5,
          avg_return_6mo: 21.8,
          leaders: [
            { ticker: 'nvda', score: 91.2 },
            { ticker: 'AMD' },
          ],
        },
      ],
    },
    '3mo',
  );

  assert.deepEqual(
    JSON.parse(JSON.stringify(catalog)),
    [{ id: 'semi', name: '半导体', tickers: ['NVDA', 'AMD'] }],
  );
  assert.equal(strength.sectors[0].avgReturn, 12.5);
  assert.equal(strength.sectors[0].avgStrength, null);
  assert.equal(strength.sectors[0].leaders[1].score, null);
  assert.equal('flowRating' in strength.sectors[0], false);
  assert.equal('corr' in strength.sectors[0], false);
  assert.equal('trend30d' in strength.sectors[0], false);
});

test('IV 排名保留顶层来源状态与覆盖率，不伪装成历史百分位', () => {
  const { exports } = loadSectorsModule();
  const result = exports.mapSectorIvRankingEnvelope({
    sector_id: 'semi',
    sector_name: '半导体',
    source_status: 'degraded',
    data_limited: true,
    success_count: 1,
    requested_count: 2,
    success_rate: 50,
    as_of: '2026-07-23T08:00:00Z',
    failed_symbols: ['AMD'],
    snapshot_source: 'strength_worker',
    snapshot_origin: 'public_live',
    providers: ['Yahoo/yfinance'],
    rankings: [
      {
        ticker: 'nvda',
        name: '英伟达',
        price: 171.2,
        price_provider: 'Massive',
        atm_iv_percent: 41.3,
        sector_iv_rank: 88.4,
      },
    ],
  });

  assert.equal(result.sourceStatus, 'degraded');
  assert.equal(result.dataLimited, true);
  assert.equal(result.successCount, 1);
  assert.equal(result.requestedCount, 2);
  assert.equal(result.rows[0].sectorIvRank, 88.4);
  assert.equal(result.rows[0].atmIvPercent, 41.3);
  assert.equal(result.snapshotSource, 'strength_worker');
  assert.equal(result.snapshotOrigin, 'public_live');
  assert.deepEqual(result.providers, ['Yahoo/yfinance']);
  assert.equal(result.rows[0].priceProvider, 'Massive');
  assert.equal('ivChange30d' in result.rows[0], false);
});

test('板块网关调用目录、强度聚合和 IV 排名真实路径', async () => {
  const responses = {
    '/sectors': { sectors: [{ id: 'semi', name: '半导体', tickers: ['NVDA'] }] },
    '/strength/sectors?period=6mo': {
      period: '6mo',
      sectors: [{ sector_id: 'semi', name: '半导体', avg_return: 8.2 }],
    },
    '/sectors/semi/iv-ranking': {
      sector_id: 'semi',
      rankings: [{ ticker: 'NVDA', atm_iv_percent: 40, sector_iv_rank: 50 }],
    },
  };
  const loaded = loadSectorsModule(responses);

  await loaded.exports.sectorsApi.list();
  await loaded.exports.sectorsApi.strength('6mo');
  await loaded.exports.sectorsApi.ivRanking('semi');

  assert.deepEqual(loaded.calls, [
    '/sectors',
    '/strength/sectors?period=6mo',
    '/sectors/semi/iv-ranking',
  ]);
});

test('板块组件不再消费无后端依据的趋势、资金流和相关性字段', () => {
  const files = [
    'components/sectors/DetailBand.tsx',
    'components/sectors/HeatMatrix.tsx',
    'components/sectors/SectorList.tsx',
    'components/sectors/SideRail.tsx',
    'components/sectors/model.ts',
    'pages/Sectors.tsx',
  ];
  const source = files
    .map((relative) => fs.readFileSync(path.join(sourceRoot, relative), 'utf8'))
    .join('\n');
  for (const forbidden of [
    'trend30d',
    'flowRating',
    'rsVsSpy',
    'CorrCard',
    'FlowDots',
    'TrendChart',
  ]) {
    assert.equal(source.includes(forbidden), false, `不应再使用 ${forbidden}`);
  }
  for (const required of [
    'sectorsApi.strength(period)',
    'avgReturn',
    'avgStrength',
    'coveredCount',
    'leaders',
  ]) {
    assert.equal(source.includes(required), true, `应消费 ${required}`);
  }

  const ivPanel = fs.readFileSync(
    path.join(sourceRoot, 'components', 'sectors', 'IvPanel.tsx'),
    'utf8',
  );
  assert.equal(ivPanel.includes('ivChange30d'), false);
  assert.equal(ivPanel.includes('基于 252 个交易日'), false);
  /* 供应商名与内部来源标记不再印给普通读者。字段本身仍由 api 层如实保留
     （见上方 mapper 断言），面板只呈现「板块内横向比较 + 延迟数据」这一条口径。 */
  assert.equal(ivPanel.includes('meta.providers'), false);
  assert.equal(ivPanel.includes('priceProvider'), false);
  assert.equal(ivPanel.includes('meta.snapshotSource'), false);
  assert.match(ivPanel, /板块排位是同板块成分之间的横向比较/);
  /* stale 提示必须留着：数据没刷新要让人看见 */
  assert.match(ivPanel, /meta\.stale/);
});

test('IV 排名等待真实板块目录，首屏不再请求旧占位编号', () => {
  const page = fs.readFileSync(
    path.join(sourceRoot, 'pages', 'Sectors.tsx'),
    'utf8',
  );

  assert.equal(page.includes("useState('semi')"), false);
  assert.equal(page.includes('catalogIds.has(ivSectorId)'), true);
  assert.equal(page.includes('Promise.resolve(null)'), true);
  assert.match(
    page,
    /ivSectorIdValid\s*\?\s*sectorsApi\.ivRanking\(ivSectorIdValid\)\s*:\s*Promise\.resolve\(null\)/,
  );
});
