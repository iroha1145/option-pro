import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const earningsApiPath = path.join(root, 'src', 'api', 'modules', 'earnings.ts');
const impactCardPath = path.join(root, 'src', 'components', 'earnings', 'ImpactCard.tsx');
const earningsPagePath = path.join(root, 'src', 'pages', 'Earnings.tsx');
const newsPanelPath = path.join(root, 'src', 'components', 'detail', 'NewsPanel.tsx');

/**
 * i18n/core 的最小桩：这些测试断言的是数据归一逻辑，不是翻译本身，回退原文即可
 * （与真实 t() 在 zh 语言下的行为一致），{n} 占位符按真实 core.ts 同款规则替换。
 */
function stubT(msgid, vars) {
  return vars ? msgid.replace(/\{(\w+)\}/g, (whole, key) => (vars[key] === undefined || vars[key] === null ? whole : String(vars[key]))) : msgid;
}

function loadEarningsModule(
  onGet = () => Promise.resolve({}),
  onPost = () => Promise.resolve({}),
) {
  const source = fs.readFileSync(earningsApiPath, 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  const asRec = (value) => (
    value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {}
  );
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require: (id) => {
      if (id === '../client') {
        return {
          get: onGet,
          post: onPost,
          mockOr: (_mock, live) => live(),
          toQuery: (values) => {
            const query = new URLSearchParams();
            for (const [key, value] of Object.entries(values)) {
              if (value !== null && value !== undefined && value !== '') {
                query.set(key, String(value));
              }
            }
            return query.toString();
          },
        };
      }
      if (id === '../live') {
        return {
          asRec,
          pickB: (row, ...keys) => {
            for (const key of keys) {
              if (typeof row[key] === 'boolean') return row[key];
            }
            return null;
          },
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
          unwrap: () => [],
        };
      }
      if (id === '@/mocks/fixtures2') return {};
      if (id === '../../i18n/core.ts') return { t: stubT };
      throw new Error(`unexpected import: ${id}`);
    },
  });
  return module.exports;
}

function loadImpactNormalizer() {
  return loadEarningsModule().normalizeLiveEarningsImpact;
}

const completedResult = {
  output_language: 'zh-CN',
  ticker: 'googl',
  summary: '财报结果显示云业务增速稳健。',
  expectation: '重点观察下一季度资本开支和利润率指引。',
  impacted: [{
    ticker: 'msft',
    name: '微软',
    relation: 'competitor',
    direction: 'mixed',
    reason: '云业务增速会改变同行估值比较。',
  }],
};

test('财报影响结果兼容终版锁定元数据，也兼容旧缓存缺少元数据', () => {
  const normalize = loadImpactNormalizer();
  const finalResult = normalize({
    ...completedResult,
    _locked: true,
    _final: true,
    _finalization_in_progress: false,
    _analysis_stage: 'post_release_final',
    _report_id: 'GOOGL:2026-07-23:2026:2',
    _report_date: '2026-07-23',
  });
  const legacyResult = normalize(completedResult);

  assert.equal(finalResult.ticker, 'GOOGL');
  assert.equal(finalResult.locked, true);
  assert.equal(finalResult.final, true);
  assert.equal(finalResult.finalizationInProgress, false);
  assert.equal(finalResult.analysisStage, 'post_release_final');
  assert.equal(finalResult.reportId, 'GOOGL:2026-07-23:2026:2');
  assert.equal(finalResult.reportDate, '2026-07-23');
  assert.equal('locked' in legacyResult, false);
  assert.equal('final' in legacyResult, false);
  assert.equal('analysisStage' in legacyResult, false);
  assert.equal('finalizationInProgress' in legacyResult, false);
  assert.equal('reportDate' in legacyResult, false);
  assert.equal('reportId' in legacyResult, false);
});

test('单标的财报任务向访客开放且只使用报告级公开接口', () => {
  const card = fs.readFileSync(impactCardPath, 'utf8');
  const earningsApi = fs.readFileSync(earningsApiPath, 'utf8');

  assert.equal(card.includes("setPhase(!isOwner ? 'locked-visitor'"), false);
  assert.equal(card.includes('登录 Owner 后可创建模型任务'), false);
  assert.equal(card.includes("setPhase(isOwner && !aiAvailable ? 'locked-ai' : 'needs-analysis')"), true);
  assert.equal(card.includes('owner / visitor 均可'), true);
  assert.equal(card.includes('earningsApi.reportAnalysis'), true);
  assert.equal(card.includes('earningsApi.requestReportAnalysis'), true);
  assert.equal(card.includes('aiJobsApi'), false);
  assert.equal(card.includes('createEarningsImpact'), false);
  assert.equal(card.includes('cancelJob'), false);
  assert.equal(card.includes('job.id'), false);
  assert.equal(card.includes('会产生模型费用'), false);
  assert.equal(card.includes('year: reportYear'), true);
  assert.equal(card.includes('quarter: reportQuarter'), true);
  assert.equal(earningsApi.includes('/reports/${encodeURIComponent(reportDate)}'), true);
  assert.equal(earningsApi.includes("post(reportAnalysisPath(ticker, reportDate, identity), { confirm: true })"), true);
  const newsPanel = fs.readFileSync(newsPanelPath, 'utf8');
  assert.equal(newsPanel.includes('isOwner ? earningsApi.impact(ticker) : Promise.resolve(null)'), true);
});

test('报告级 GET/POST 绑定代码、财报日和季度，POST 正文只有确认字段', async () => {
  const reads = [];
  const writes = [];
  const response = {
    status: 'completed',
    _analysis_stage: 'post_release_final',
    _report_id: 'earnings:GOOGL:2026-07-23:2026:q2',
    _report_date: '2026-07-23',
    _locked: true,
    _final: true,
    _finalization_in_progress: false,
    result: completedResult,
  };
  const { earningsApi } = loadEarningsModule(
    (requestPath) => {
      reads.push(requestPath);
      return Promise.resolve(response);
    },
    (requestPath, body) => {
      writes.push({ requestPath, body });
      return Promise.resolve({ ...response, status: 'queued', result: null, _locked: false, _final: false });
    },
  );

  const readResult = await earningsApi.reportAnalysis(
    'googl',
    '2026-07-23',
    { year: 2026, quarter: 2 },
  );
  const writeResult = await earningsApi.requestReportAnalysis(
    'googl',
    '2026-07-23',
    { year: 2026, quarter: 2 },
  );

  assert.equal(readResult.result.ticker, 'GOOGL');
  assert.equal(readResult.locked, true);
  assert.equal(writeResult.status, 'queued');
  assert.equal(
    reads[0],
    '/ai/earnings-impact/googl/reports/2026-07-23?year=2026&quarter=2',
  );
  assert.deepEqual(JSON.parse(JSON.stringify(writes)), [{
    requestPath: '/ai/earnings-impact/googl/reports/2026-07-23?year=2026&quarter=2',
    body: { confirm: true },
  }]);
});

test('财报发布后显示自动重分析、进度和最终锁定状态', () => {
  const card = fs.readFileSync(impactCardPath, 'utf8');
  const page = fs.readFileSync(earningsPagePath, 'utf8');

  for (const required of [
    'earnings_finalization_in_progress',
    'earnings_analysis_locked',
    'post_release_final',
    '正在分析第 1 / 1 条',
    '模型处理中',
    'role="progressbar"',
    '财报已发布，正在自动重分析',
    '自动重分析中',
    '已锁定 · 最终分析',
    '最终分析已锁定',
  ]) {
    assert.equal(card.includes(required), true, `状态界面缺少 ${required}`);
  }
  assert.equal(card.includes("phase === 'finalizing'"), true);
  assert.equal(card.includes("phase === 'final-locked'"), true);
  assert.equal(card.includes('isFinalImpact(impact)'), true);
  assert.equal(card.includes('reportAnalysisNeedsPolling'), true);
  assert.equal(card.includes('费用或取消入口'), true);
  assert.equal(card.includes('onAnalyzed(ticker, resolvedAnalysis)'), true);
  assert.equal(page.includes('reportAnalysisStates[reportAnalysisKey(row.ticker, row.date)]'), true);
  assert.equal(page.includes('impactReady: analysis.result != null'), true);
  assert.equal(page.includes('onAnalyzed={onReportAnalysis}'), true);
});
