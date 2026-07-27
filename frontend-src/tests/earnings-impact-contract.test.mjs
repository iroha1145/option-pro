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
const earningsTypesSourcePath = path.resolve(here, '..', 'src', 'components', 'earnings', 'types.ts');

/**
 * i18n/core 的最小桩：这些测试断言的是数据归一/校验逻辑，不是翻译本身，回退原文
 * 即可（与真实 t() 在 zh 语言下的行为一致），{n} 占位符按真实 core.ts 同款规则替换。
 */
function stubT(msgid, vars) {
  return vars ? msgid.replace(/\{(\w+)\}/g, (whole, key) => (vars[key] === undefined || vars[key] === null ? whole : String(vars[key]))) : msgid;
}

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
    if (id === '@/mocks/fixtures2') return {};
    if (id === '../../i18n/core.ts') return { t: stubT };
    throw new Error(`unexpected import: ${id}`);
  };
  vm.runInNewContext(compiled, { module, exports: module.exports, require });
  return module.exports.normalizeLiveEarningsImpact;
}

function loadUpcomingMapper() {
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
    if (id === '@/mocks/fixtures2') return {};
    if (id === '../../i18n/core.ts') return { t: stubT };
    throw new Error(`unexpected import: ${id}`);
  };
  vm.runInNewContext(compiled, { module, exports: module.exports, require });
  return module.exports.mapUpcoming;
}

function loadEarningsDateTools() {
  const source = fs.readFileSync(earningsTypesSourcePath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require: (id) => {
      if (id === '../../i18n/core.ts') return { t: stubT };
      throw new Error(`unexpected runtime import: ${id}`);
    },
  });
  return module.exports;
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
        }, pickLabel: (row, ...keys) => {
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
    if (id === '../../i18n/core.ts') return { t: stubT };
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

test('财报市值只保留数据源提供的正数，缺失和占位值保持为空', () => {
  const mapUpcoming = loadUpcomingMapper();
  const rows = mapUpcoming({
    earnings: [
      { ticker: 'REAL', earnings_date: '2026-07-30', market_cap: 123_456_789 },
      { ticker: 'ZERO', earnings_date: '2026-07-30', market_cap: 0 },
      { ticker: 'NEG', earnings_date: '2026-07-30', market_cap: -1 },
      { ticker: 'MISS', earnings_date: '2026-07-30' },
    ],
  });

  assert.equal(rows[0].marketCap, 123_456_789);
  assert.equal(rows[0].market_cap, 123_456_789);
  for (const row of rows.slice(1)) {
    assert.equal(row.marketCap, null);
    assert.equal(row.market_cap, null);
  }

  const list = fs.readFileSync(listSourcePath, 'utf8');
  assert.equal(list.includes("marketCap != null ? `$${fmtCompact(marketCap)}` : '—'"), true);
  const fixtures = fs.readFileSync(
    path.resolve(here, '..', 'src', 'mocks', 'fixtures2.ts'),
    'utf8',
  );
  assert.equal(fixtures.includes('marketCap: info ? Math.round'), false);
  assert.equal(fixtures.includes('marketCap: null'), true);
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
  assert.equal(page.includes('财报数据暂时不完整'), true);
  assert.equal(page.includes('缺失的公司不会用估算值顶替'), true);
  assert.equal(page.includes('const coverageLimited = q.data?.dataLimited === true;'), true);
  assert.equal(page.includes("q.data?.sourceStatus === 'degraded'"), false);
  /* providers 由 mapper 如实保留（上方字段断言），但不印供应商名给普通读者 */
  assert.equal(page.includes("q.data.providers.join(' + ')"), false);
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

test('财报页面保留近期已公布结果并默认收纳长列表', () => {
  const page = fs.readFileSync(pageSourcePath, 'utf8');
  const list = fs.readFileSync(listSourcePath, 'utf8');

  // 滚动窗口与渐进裁切迁入 components/earnings/types.ts 的
  // computeEarningsListState（重点/全部双模式与页面共用同一实现）。
  const typesSource = fs.readFileSync(
    path.join(path.dirname(listSourcePath), 'types.ts'),
    'utf8',
  );
  assert.equal(typesSource.includes('distance >= -3 && distance <= 30'), true);
  assert.equal(typesSource.includes('prioritizeEarningsRows(listItems, visibleLimit)'), true);
  assert.equal(page.includes('const LIST_PAGE_SIZE = 24'), true);
  assert.equal(page.includes('computeEarningsListState({'), true);
  assert.equal(page.includes('visibleItems.length < filteredItems.length'), true);
  assert.equal(page.includes("{t('显示更多 ·')} {Math.min(LIST_PAGE_SIZE"), true);
  assert.equal(page.includes("{t('收起至前')} {LIST_PAGE_SIZE} {t('条')}"), true);
  assert.equal(page.includes('row={selectedRow}'), true);
  assert.equal(page.includes('xl:col-span-8'), true);
  assert.equal(page.includes('xl:col-span-4'), true);
  assert.equal(page.includes('xl:col-span-7'), false);
  assert.equal(page.includes('xl:col-span-5'), false);
  const rightColumn = page.slice(page.indexOf('B3 AI 影响 + 低交互图表'));
  assert.equal(rightColumn.includes('<ImpactCard'), true);
  assert.equal(rightColumn.includes('<EpsHatchChart'), true);
  assert.equal(rightColumn.includes('<DensityStrip'), true);
  assert.equal(list.includes('md:max-h-[min(72vh,880px)]'), true);
  assert.equal(list.includes('md:overflow-y-auto'), true);
  assert.equal(list.includes('sticky top-0'), true);
  assert.equal(list.includes('2xl:grid-cols-['), true);
  assert.equal(list.includes('row.revEstimate * 1e6'), false);
  assert.equal(list.includes('fmtCompact(row.revEstimate)'), true);
});

test('预期波动仅在存在真实数值时显示', () => {
  const list = fs.readFileSync(listSourcePath, 'utf8');
  const cell = list.slice(
    list.indexOf('function ExpectedMoveCell'),
    list.indexOf('/* ---------------- AI 影响操作钮'),
  );

  assert.equal(list.includes("items.some((row) => exNum(row, 'expectedMovePct') != null)"), true);
  assert.equal(list.includes("hasExpectedMove && <span className=\"eyebrow\">{t('预期波动')}</span>"), true);
  assert.equal(list.includes('hasExpectedMove && <ExpectedMoveCell'), true);
  assert.equal(cell.includes('if (pct == null) return <span aria-hidden="true" />'), true);
  assert.equal(cell.includes('—'), false);
});

test('财报首屏同时保留已公布大市值结果和近期待公布项目', () => {
  const { addDays, etToday, prioritizeEarningsRows } = loadEarningsDateTools();
  const today = etToday();
  const rows = [];

  for (let index = 0; index < 100; index += 1) {
    rows.push({
      ticker: index === 73 ? 'GOOGL' : `R${String(index).padStart(3, '0')}`,
      date: addDays(today, -1),
      epsActual: index + 0.1,
      marketCap: index === 73 ? 2_000_000_000_000 : 1_000_000 + index,
    });
  }
  for (let index = 0; index < 100; index += 1) {
    rows.push({
      ticker: `F${String(index).padStart(3, '0')}`,
      date: addDays(today, 1),
      epsActual: null,
      marketCap: 2_000_000 - index,
    });
  }
  rows.unshift({
    ticker: 'MISSING_ACTUAL',
    date: addDays(today, -1),
    epsActual: null,
    marketCap: 5_000_000_000_000,
  });

  const visible = prioritizeEarningsRows(rows, 80);
  assert.equal(visible.length, 80);
  assert.equal(visible.some((row) => row.ticker === 'GOOGL'), true);
  assert.equal(visible.some((row) => row.ticker.startsWith('R')), true);
  assert.equal(visible.some((row) => row.ticker.startsWith('F')), true);
  assert.equal(visible.some((row) => row.ticker === 'MISSING_ACTUAL'), false);
  assert.deepEqual(
    visible.map((row) => rows.indexOf(row)),
    visible.map((row) => rows.indexOf(row)).toSorted((a, b) => a - b),
  );

  const expanded = prioritizeEarningsRows(rows, 81);
  assert.equal(visible.every((row) => expanded.includes(row)), true);
});

test('财报模型使用精确报告级接口、逐条进度和管理员批量入口', () => {
  const api = fs.readFileSync(moduleSourcePath, 'utf8');
  const card = fs.readFileSync(cardSourcePath, 'utf8');
  const controls = fs.readFileSync(controlsSourcePath, 'utf8');

  assert.equal(card.includes('earningsApi.reportAnalysis'), true);
  assert.equal(card.includes('earningsApi.requestReportAnalysis'), true);
  assert.equal(api.includes('post(reportAnalysisPath(ticker, reportDate, identity), { confirm: true })'), true);
  assert.equal(card.includes('只轮询报告级 GET'), true);
  for (const field of [
    'earnings_date',
    'eps_estimate',
    'eps_actual',
    'revenue_estimate',
    'revenue_actual',
    'market_cap',
  ]) {
    assert.equal(card.includes(field), false, `访客请求不应携带客户端财务字段 ${field}`);
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
  assert.equal(controls.includes('未来 5 天'), true);
});

test('访客能看到单股分析入口和终版状态', () => {
  const page = fs.readFileSync(pageSourcePath, 'utf8');
  const list = fs.readFileSync(listSourcePath, 'utf8');

  assert.equal(page.includes('登录后可用 AI 分析'), false);
  assert.equal(page.includes('单股分析可用'), true);
  assert.equal(list.includes('ready === false && !isOwner'), false);
  /* 终版与重分析仍要各有可辨的状态，但标签必须短到能塞进 96px 的列里 */
  assert.equal(list.includes("[t('最终'), t('查看最终分析')]"), true);
  assert.equal(list.includes("[t('分析中'), t('最终分析生成中')]"), true);
  assert.match(list, /whitespace-nowrap/);
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
  assert.equal(density.includes('财报日程 · 以公司公告为准'), true);
  assert.equal(density.includes('slice(0, MAX_TOOLTIP_TICKERS)'), true);
  assert.equal(density.includes('+{n - MAX_TOOLTIP_TICKERS}'), true);
  assert.equal(card.includes('AI 依据本次财报日程生成'), true);
});

test('股票搜索失败显示明确错误态，不伪装成空结果', () => {
  const source = fs.readFileSync(commandPaletteSourcePath, 'utf8');

  assert.equal(source.includes('catch (cause)'), true);
  assert.equal(source.includes('setSearchError(searchErrorText(cause))'), true);
  assert.equal(source.includes('role="alert"'), true);
  assert.equal(source.includes('搜索未完成'), true);
  assert.equal(source.includes('!searching && !searchError && flat.length === 0'), true);
});
