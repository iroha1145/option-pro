import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(here, '..', 'src');
const modulePath = path.join(
  sourceRoot,
  'components',
  'catalysts',
  'api.ts',
);

function liveHelpers() {
  const asRec = (value) =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? value
      : {};
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
    },
  };
}

function loadCatalystsModule(responses = {}) {
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
  class ApiError extends Error {
    constructor(code, message) {
      super(message);
      this.code = code;
    }
  }
  const fixtures = new Proxy(
    {},
    {
      get: () => () => {
        throw new Error('测试不应进入演示数据分支');
      },
    },
  );
  const require = (id) => {
    if (id === '@/api/client') {
      return {
        ApiError,
        get: async (url) => {
          calls.push(url);
          return responses[url] ?? {};
        },
        idFromLocation: () => null,
        mockOr: (_mock, live) => live(),
        notifyOwnerSessionInvalid: () => {},
        post: async () => ({}),
        postCreate: async () => ({ data: {}, location: null }),
        toQuery: (params) => new URLSearchParams(
          Object.entries(params).filter(([, value]) => value !== undefined),
        ).toString(),
      };
    }
    if (id === '@/api/live') return liveHelpers();
    if (id === '@/mocks/fixtures2') return fixtures;
    if (id === '@/components/catalysts/focusCycleRequest') {
      return {
        buildFocusCycleRequestBody: () => ({}),
        focusCyclePollPath: (idValue) =>
          `/catalysts/market-focus-cycles/${idValue}`,
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

test('采集状态只接受后端明确的启用与成功字段', async () => {
  const loaded = loadCatalystsModule({
    '/catalysts/status': {
      status: 'active',
      streams: {
        news: {
          last_success_at: '2026-07-23T08:00:00Z',
          consecutive_failures: 0,
        },
        calendar: {
          remote_status: 'ok',
          last_success_at: '2026-07-23T08:01:00Z',
          consecutive_failures: 0,
        },
      },
      analysis_availability: { enabled: true },
    },
  });

  const status = await loaded.exports.catalystsContract.status();

  assert.equal(status.collecting, false);
  assert.equal(status.sourcesActive, 1);
  assert.equal(status.sourcesTotal, 2);
  assert.equal(status.streams[0].ok, false);
  assert.equal(status.streams[1].ok, true);
  assert.deepEqual(
    Array.from(status.streams, (stream) => stream.name),
    ['新闻采集流', '经济日历流'],
  );
  assert.deepEqual(loaded.calls, ['/catalysts/status']);
});

test('数据源卡映射真实近24小时条数与新鲜度滞后', async () => {
  const loaded = loadCatalystsModule({
    '/catalysts/status': {
      streams: {
        news: {
          remote_status: 'ok',
          last_success_at: '2026-07-23T08:00:00Z',
          consecutive_failures: 0,
          items_last_24h: 17,
          lag_ms: 90_000,
        },
      },
    },
  });

  const sources = await loaded.exports.catalystsContract.sources();

  assert.equal(sources.length, 1);
  assert.equal(sources[0].source, '新闻采集流');
  assert.equal(sources[0].itemsToday, 17);
  assert.equal(sources[0].latencyMs, 90_000);

  const panel = fs.readFileSync(
    path.join(sourceRoot, 'components', 'catalysts', 'SourcesPanel.tsx'),
    'utf8',
  );
  assert.equal(panel.includes('延迟 ms'), false);
  assert.equal(panel.includes('数据滞后'), true);
  assert.equal(panel.includes('不代表接口请求耗时'), true);
});

test('股票影响圆点由外层定位，缩放动画不会覆盖居中位移', () => {
  const source = fs.readFileSync(
    path.join(sourceRoot, 'components', 'catalysts', 'StocksPanel.tsx'),
    'utf8',
  );

  assert.equal(source.includes('calc(${pct}% - 5px)'), false);
  assert.equal(source.includes('style={{ left: `${pct}%` }}'), true);
  assert.match(
    source,
    /className="absolute top-1\/2 -translate-x-1\/2 -translate-y-1\/2"[\s\S]*?<motion\.span[\s\S]*?initial=\{\{ scale: 0 \}\}/,
  );
});

test('状态栏不再把产品名伪装成文章来源', () => {
  const source = fs.readFileSync(
    path.join(sourceRoot, 'components', 'catalysts', 'StatusHero.tsx'),
    'utf8',
  );

  assert.equal(source.includes('来源：Optix NewsDesk'), false);
  assert.equal(source.includes('每条新闻展示原始文章来源'), true);
});
