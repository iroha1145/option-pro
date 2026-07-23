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
const pageSourcePath = path.resolve(here, '..', 'src', 'pages', 'Earnings.tsx');
const listSourcePath = path.resolve(here, '..', 'src', 'components', 'earnings', 'EarningsList.tsx');
const controlsSourcePath = path.resolve(here, '..', 'src', 'components', 'earnings', 'EarningsAnalysisControls.tsx');
const densitySourcePath = path.resolve(here, '..', 'src', 'components', 'earnings', 'DensityStrip.tsx');
const earningsComponentsPath = path.resolve(here, '..', 'src', 'components', 'earnings');
const commandPaletteSourcePath = path.resolve(here, '..', 'src', 'components', 'CommandPalette.tsx');

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

test('财报日历保留全市场覆盖状态和真实供应方', () => {
  const source = fs.readFileSync(moduleSourcePath, 'utf8');
  const page = fs.readFileSync(pageSourcePath, 'utf8');

  for (const field of [
    'data_limited',
    'source_status',
    'providers',
    'as_of',
    'refresh_status',
    'refresh_retry_after_seconds',
  ]) {
    assert.equal(source.includes(field), true, `财报元数据缺少 ${field}`);
  }
  assert.equal(source.includes('mapUpcomingPayload'), true);
  assert.equal(page.includes('全市场财报源暂时不完整'), true);
  assert.equal(page.includes('未返回的公司不会用热门名单或估算值补齐'), true);
  assert.equal(page.includes("q.data.providers.join(' + ')"), true);
  assert.equal(page.includes('继续使用上一次完整日历'), true);
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
  assert.equal(source.includes('if (!ticker || !aiEnabled)'), false);
  assert.equal(source.includes("setPhase(!ticker ? 'idle' : 'loading')"), true);
  assert.equal(source.includes('已有分析仍会照常显示'), true);
});

test('财报页面保留近期已公布结果并把低交互图表放到分析栏', () => {
  const page = fs.readFileSync(pageSourcePath, 'utf8');
  const list = fs.readFileSync(listSourcePath, 'utf8');

  assert.equal(page.includes('distance >= -3 && distance <= 30'), true);
  assert.equal(page.includes('const LIST_PAGE_SIZE = 80'), true);
  assert.equal(page.includes('filteredItems.slice(0, visibleLimit)'), true);
  assert.equal(page.includes('再显示 {Math.min(LIST_PAGE_SIZE'), true);
  assert.equal(page.includes('row={selectedRow}'), true);
  const rightColumn = page.slice(page.indexOf('B3 AI 影响 + 低交互图表'));
  assert.equal(rightColumn.includes('<ImpactCard'), true);
  assert.equal(rightColumn.includes('<EpsHatchChart'), true);
  assert.equal(rightColumn.includes('<DensityStrip'), true);
  assert.equal(list.includes('2xl:grid-cols-['), true);
  assert.equal(list.includes(' xl:grid-cols-['), false);
  assert.equal(list.includes('row.revEstimate * 1e6'), false);
  assert.equal(list.includes('fmtCompact(row.revEstimate)'), true);
});

test('财报模型任务带真实财报上下文、逐条进度和管理员批量入口', () => {
  const card = fs.readFileSync(cardSourcePath, 'utf8');
  const controls = fs.readFileSync(controlsSourcePath, 'utf8');

  for (const field of [
    'earnings_date',
    'eps_estimate',
    'revenue_estimate',
    'market_cap',
  ]) {
    assert.equal(card.includes(field), true, `单股任务缺少 ${field}`);
  }
  assert.equal(card.includes('正在分析第 1 / 1 条'), true);
  assert.equal(controls.includes('earningsScheduledAnalysisEnabled'), true);
  assert.equal(controls.includes("runtimeApi.workerAction('earnings_analysis')"), true);
  assert.equal(controls.includes('runtimeApi.waitForWorkerAction(action.requestId)'), true);
  assert.equal(controls.includes("adminApi.workerAction('earnings_analysis')"), false);
  for (const field of ['eligible', 'queued', 'existing', 'invalid']) {
    assert.equal(controls.includes(field), true, `批量任务回执缺少 ${field}`);
  }
  assert.equal(controls.includes('自动分析设置已保存，但首次任务失败'), true);
  assert.equal(controls.includes('最近任务检查'), true);
  assert.equal(controls.includes('未来 30 天'), true);
});

test('财报组件不再伪造 Optix Research 来源', () => {
  const componentSources = fs.readdirSync(earningsComponentsPath)
    .filter((name) => name.endsWith('.tsx'))
    .map((name) => fs.readFileSync(path.join(earningsComponentsPath, name), 'utf8'));
  const density = fs.readFileSync(densitySourcePath, 'utf8');
  const card = fs.readFileSync(cardSourcePath, 'utf8');

  for (const source of componentSources) {
    assert.equal(source.includes('Optix Research'), false);
  }
  assert.equal(density.includes('后端财报日历接口'), true);
  assert.equal(density.includes('slice(0, MAX_TOOLTIP_TICKERS)'), true);
  assert.equal(density.includes('+{n - MAX_TOOLTIP_TICKERS}'), true);
  assert.equal(card.includes('基于当前财报日历字段'), true);
});

test('股票搜索失败显示明确错误态，不伪装成空结果', () => {
  const source = fs.readFileSync(commandPaletteSourcePath, 'utf8');

  assert.equal(source.includes('catch (cause)'), true);
  assert.equal(source.includes('setSearchError(searchErrorText(cause))'), true);
  assert.equal(source.includes('role="alert"'), true);
  assert.equal(source.includes('搜索未完成'), true);
  assert.equal(source.includes('!searching && !searchError && flat.length === 0'), true);
});
