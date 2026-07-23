import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const moduleSourcePath = path.resolve(here, '..', 'src', 'api', 'modules', 'earnings.ts');
const cardSourcePath = path.resolve(here, '..', 'src', 'components', 'earnings', 'ImpactCard.tsx');

function loadNormalizer() {
  const source = fs.readFileSync(moduleSourcePath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  const require = (id) => {
    if (id === '../client') {
      return {
        get: () => Promise.resolve({}),
        post: () => Promise.resolve({}),
        mockOr: (_mock, live) => live(),
      };
    }
    if (id === '../live') {
      const asRec = (value) => (
        value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {}
      );
      return {
        asRec,
        pickN: (row, ...keys) => {
          for (const key of keys) {
            const value = typeof row[key] === 'string' ? Number(row[key]) : row[key];
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
        unwrap: (body, ...keys) => {
          if (Array.isArray(body)) return body;
          for (const key of keys) {
            if (Array.isArray(body?.[key])) return body[key];
          }
          return [];
        },
      };
    }
    if (id === '@/mocks/fixtures2') return {};
    throw new Error(`unexpected import: ${id}`);
  };
  vm.runInNewContext(compiled, { module, exports: module.exports, require });
  return module.exports.normalizeLiveEarningsImpact;
}

test('财报影响在线契约归一化 snake_case 和 camelCase 语言字段', () => {
  const normalize = loadNormalizer();
  const impacted = [
    {
      ticker: 'amd',
      name: '超威半导体',
      relation: 'competitor',
      direction: 'bullish',
      reason: '数据中心需求上修会带动同类算力标的估值。',
    },
  ];
  const snake = normalize({
    output_language: 'zh-CN',
    ticker: 'nvda',
    summary: '财报重点在数据中心增长和毛利率。',
    expectation: '市场关注指引是否继续上修。',
    impacted,
    _cached: true,
  });
  const camel = normalize({
    outputLanguage: 'zh-CN',
    ticker: 'nvda',
    summary: '财报重点在数据中心增长和毛利率。',
    expectation: '市场关注指引是否继续上修。',
    impacted,
  });

  assert.equal(snake.outputLanguage, 'zh-CN');
  assert.equal(snake.ticker, 'NVDA');
  assert.equal(snake.impacted[0].ticker, 'AMD');
  assert.equal(camel.outputLanguage, 'zh-CN');
  assert.deepEqual(JSON.parse(JSON.stringify(camel)), JSON.parse(JSON.stringify(snake)));
  assert.equal('expectedMovePct' in snake, false);
  assert.equal('sentiment' in snake, false);
  assert.equal('ivRank' in snake, false);
  assert.equal('related' in snake, false);
});

test('财报影响在线契约拒绝缺字段、非简体中文或非法枚举', () => {
  const normalize = loadNormalizer();
  const base = {
    output_language: 'zh-CN',
    ticker: 'NVDA',
    summary: '摘要',
    expectation: '预期',
    impacted: [{
      ticker: 'AMD',
      name: '超威半导体',
      relation: 'competitor',
      direction: 'bullish',
      reason: '关联原因',
    }],
  };

  assert.throws(() => normalize({ ...base, expectation: '' }), /字段不完整/);
  assert.throws(() => normalize({ ...base, output_language: 'en-US' }), /字段不完整/);
  assert.throws(
    () => normalize({ ...base, impacted: [{ ...base.impacted[0], direction: 'neutral' }] }),
    /关联标的字段不完整/,
  );
});

test('财报日程不把缺失或未知时间伪装成盘前', () => {
  const source = fs.readFileSync(moduleSourcePath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  const require = (id) => {
    if (id === '../client') {
      return {
        get: () => Promise.resolve({}),
        post: () => Promise.resolve({}),
        mockOr: (_mock, live) => live(),
      };
    }
    if (id === '../live') {
      const asRec = (value) => (
        value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {}
      );
      return {
        asRec,
        pickN: () => null,
        pickS: (row, ...keys) => {
          for (const key of keys) {
            if (typeof row[key] === 'string' && row[key]) return row[key];
          }
          return null;
        },
        unwrap: (body, ...keys) => {
          for (const key of keys) {
            if (Array.isArray(body?.[key])) return body[key];
          }
          return [];
        },
      };
    }
    if (id === '@/mocks/fixtures2') return {};
    throw new Error(`unexpected import: ${id}`);
  };
  vm.runInNewContext(compiled, { module, exports: module.exports, require });
  const rows = module.exports.mapUpcoming({
    earnings: [
      { ticker: 'AAPL', earnings_date: '2026-07-30' },
      { ticker: 'MSFT', earnings_date: '2026-07-31', earnings_time: 'amc' },
      { ticker: 'NVDA', earnings_date: '2026-08-01', timing: 'unknown' },
    ],
  });

  assert.equal(rows[0].timing, null);
  assert.equal(rows[1].timing, 'amc');
  assert.equal(rows[2].timing, null);
  assert.equal(source.includes("?? 'bmo'"), false);
});

test('财报影响卡只消费真实分析字段', () => {
  const source = fs.readFileSync(cardSourcePath, 'utf8');
  for (const forbidden of [
    'impact.expectedMovePct',
    'impact.sentiment',
    'impact.ivRank',
    'impact.related',
    '预期波动',
    'IV 排名',
    '预测涨跌幅',
  ]) {
    assert.equal(source.includes(forbidden), false, `不应再展示或访问 ${forbidden}`);
  }
  for (const required of [
    'impact.outputLanguage',
    'impact.ticker',
    'impact.summary',
    'impact.expectation',
    'impact.impacted',
  ]) {
    assert.equal(source.includes(required), true, `应展示 ${required}`);
  }
});
