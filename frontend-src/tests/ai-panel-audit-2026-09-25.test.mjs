/**
 * 2026-09-25 审计修复：个股 AI 面板（useAiJob / AiAnalysisCard / 期权解读）、错误码共用表、
 * 查询注册表恢复路径、行情快照、行展开手动重读，以及结构清理后的共用工具。
 * 行为测试直接执行真实源码（React 用 helpers/react-hooks.mjs 的桩）；标注「接线检查」
 * 的几条只读源码，确认调用点用上了共用实现、清理掉的死代码没有回来。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { ApiError } from '../src/api/client.ts';
import * as normalize from '../src/api/aiJobNormalize.ts';
import { setLocale } from '../src/i18n/testing.ts';
import { t } from '../src/i18n/core.ts';
import { DICT } from '../src/i18n/dict/index.ts';
import { fmtRelative } from '../src/lib/format.ts';
import { sortWatchlistItems } from '../src/pages/watchlistSort.ts';
import { clamp, finite } from '../src/components/detail/chart-drawings/analysis/numeric.ts';
import { createReactStub } from './helpers/react-hooks.mjs';
import { deferred } from './helpers/deferred.mjs';
import { SHARED_UI_STUBS } from './helpers/shared-ui-stubs.mjs';

const CJK = /[一-鿿]/;
const settle = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };
const read = (file) => fs.readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');

const interpolate = (msg, vars) => (vars ? String(msg).replace(/\{(\w+)\}/g, (whole, key) => (vars[key] ?? whole)) : msg);
const JSX = {
  jsx: (type, props) => ({ type, props: props ?? {} }),
  jsxs: (type, props) => ({ type, props: props ?? {} }),
  Fragment: 'Fragment',
};

/** 把真实 TS/TSX 源码转成 CJS 在沙箱里执行；未声明的依赖直接报错，防止悄悄漏桩。 */
function compile(file, React, imports, environment = {}) {
  const module = { exports: {} };
  const code = ts.transpileModule(read(file), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const sandbox = {
    module, exports: module.exports, Error, TypeError, Promise, Date, URLSearchParams,
    require(id) {
      if (id === 'react') return React;
      if (id === 'react/jsx-runtime') return JSX;
      if (id.includes('i18n/core')) return { t: interpolate };
      if (id in imports) return imports[id];
      if (id in SHARED_UI_STUBS) return SHARED_UI_STUBS[id];
      throw new Error(`Unexpected import: ${id}`);
    },
    ...environment,
  };
  vm.runInNewContext(code, sandbox);
  return { exports: module.exports, sandbox };
}

function textOf(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  return textOf(node.props?.children);
}

function findAll(node, predicate, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) {
    for (const child of node) findAll(child, predicate, out);
    return out;
  }
  if (predicate(node)) out.push(node);
  findAll(node.props?.children, predicate, out);
  return out;
}

function snapshot(tree) {
  return {
    text: textOf(tree),
    buttons: findAll(tree, (node) => node.type === 'button').map((node) => ({
      label: textOf(node.props.children),
      disabled: node.props.disabled === true,
    })),
  };
}

function timers() {
  let serial = 0;
  const queued = new Map();
  return {
    queued,
    setTimeout(fn, delay) { const id = ++serial; queued.set(id, { fn, delay }); return id; },
    clearTimeout(id) { queued.delete(id); },
    async next() {
      const [id, entry] = queued.entries().next().value;
      queued.delete(id);
      await entry.fn();
      await settle();
      return entry.delay;
    },
  };
}

function fakeDocument() {
  const listeners = new Set();
  return {
    hidden: false,
    addEventListener(type, fn) { if (type === 'visibilitychange') listeners.add(fn); },
    removeEventListener(type, fn) { if (type === 'visibilitychange') listeners.delete(fn); },
    setHidden(value) { this.hidden = value; for (const fn of [...listeners]) fn(); },
    listenerCount: () => listeners.size,
  };
}

function jobHarness({ get, cancel } = {}) {
  const stub = createReactStub();
  const clock = timers();
  const doc = fakeDocument();
  const queries = [];
  const cancels = [];
  const { exports } = compile('components/detail/useAiJob.ts', stub.React, {
    '@/api/client': { ApiError },
    '@/api/aiJobNormalize': { aiJobCreateErrorMessage: normalize.aiJobCreateErrorMessage },
    '@/api/modules/ai-jobs': {
      aiJobsApi: {
        get: (id) => { queries.push(id); return get(id); },
        cancel: (id) => { cancels.push(id); return cancel ? cancel(id) : Promise.resolve({ id, status: 'cancelled', progress: null }); },
      },
    },
  }, { setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout, document: doc });
  const current = stub.mount(() => exports.useAiJob());
  return { ...stub, clock, doc, queries, cancels, current };
}

const queuedJob = (extra = {}) => async () => ({ id: 'paid-1', status: 'queued', progress: null, ...extra });

/* ---------------- useAiJob ---------------- */

const jobNotFound = () => new ApiError(404, 'Not Found', { payload: { detail: 'AI job not found' } });

test('查询 404：放下任务并说明记录已不存在，允许关闭后重新发起', async () => {
  const h = jobHarness({ get: async () => { throw jobNotFound(); } });
  let creates = 0;
  const create = async () => { creates += 1; return { id: `paid-${creates}`, status: 'queued', progress: null }; };
  await h.current().start(create);
  await h.clock.next();
  assert.equal(h.current().queryIssue, 'missing');
  assert.equal(h.current().job, null);
  assert.equal(h.current().error, '任务记录已不存在');
  assert.equal(h.clock.queued.size, 0, '404 不再自动重查');
  h.current().reset();
  assert.equal(h.current().error, null);
  assert.equal(h.current().queryIssue, null);
  await h.current().start(create);
  assert.equal(creates, 2, '记录消失后能重新发起，不被旧任务锁住');
  assert.equal(h.current().job.id, 'paid-2');
  h.unmount();
});

test('不是后端给的 404（代理或路由错误）不放下任务，保留继续查询', async () => {
  const h = jobHarness({ get: async () => { throw new ApiError(404, 'Not Found'); } });
  let creates = 0;
  await h.current().start(async () => { creates += 1; return { id: 'paid-1', status: 'queued', progress: null }; });
  await h.clock.next();
  assert.equal(h.current().queryIssue, 'blocked');
  assert.equal(h.current().job.id, 'paid-1');
  h.current().reset();
  assert.equal(h.current().job.id, 'paid-1', '任务可能还在跑，不能忘掉它');
  await h.current().start(async () => { creates += 1; return { id: 'paid-2', status: 'queued', progress: null }; });
  assert.equal(creates, 1, '不能再建一个付费任务');
  h.unmount();
});

test('页面隐藏时到点也不查询，回到前台立即补查一次', async () => {
  const h = jobHarness({ get: async (id) => ({ id, status: 'in_progress', progress: null }) });
  await h.current().start(queuedJob());
  await h.clock.next();
  assert.equal(h.queries.length, 1);
  h.doc.hidden = true;
  await h.clock.next();
  assert.equal(h.queries.length, 1, '隐藏期间不发查询');
  assert.equal(h.clock.queued.size, 0, '隐藏期间不再排下一拍');
  h.doc.setHidden(false);
  await settle();
  assert.equal(h.queries.length, 2, '回到前台立即补查');
  assert.equal(h.clock.queued.size, 1, '补查后回到正常节奏');
  h.doc.setHidden(true);
  h.doc.setHidden(false);
  await settle();
  assert.equal(h.queries.length, 2, '没有错过的一拍时，切回前台不额外查询');
  h.unmount();
  assert.equal(h.doc.listenerCount(), 0, '卸载时移除可见性监听');
});

test('创建响应的 Retry-After 决定首次查询时机，缺省 2 秒，钳在 1–300 秒', async () => {
  for (const [retryAfter, expected] of [[7, 7000], [undefined, 2000], [null, 2000], [0.2, 1000], [1000, 300_000]]) {
    const h = jobHarness({ get: async (id) => ({ id, status: 'succeeded', progress: null, result: { summary: 'ok' } }) });
    await h.current().start(queuedJob({ retryAfter }));
    const [first] = [...h.clock.queued.values()];
    assert.equal(first.delay, expected, `retryAfter=${retryAfter}`);
    h.unmount();
  }
});

test('创建失败按业务码给文案：队列满带上 Retry-After 秒数', async () => {
  const h = jobHarness({ get: async () => { throw new Error('unused'); } });
  await h.current().start(async () => {
    throw new ApiError(429, 'Too Many Requests', { bizCode: 'ai_job_queue_full', retryAfter: 60 });
  });
  assert.equal(h.current().error, '分析队列已满，请约 60 秒后再试');
  assert.equal(h.current().job, null);
  assert.equal(h.clock.queued.size, 0);
  h.unmount();
});

test('运行中取消只记下请求：不重复提交取消，继续轮询到真正取消', async () => {
  let stopped = false;
  const h = jobHarness({
    get: async (id) => (stopped
      ? { id, status: 'cancelled', progress: null }
      : { id, status: 'in_progress', progress: null, cancelRequested: true }),
    cancel: async (id) => ({ id, status: 'in_progress', progress: null, cancelRequested: true }),
  });
  await h.current().start(async () => ({ id: 'paid-1', status: 'in_progress', progress: null }));
  await Promise.all([h.current().cancel(), h.current().cancel()]);
  assert.equal(h.cancels.length, 1, '连点只发一次取消');
  assert.equal(h.current().job.cancelRequested, true);
  await h.current().cancel();
  assert.equal(h.cancels.length, 1, '已请求取消后不再提交');
  assert.equal(h.clock.queued.size, 1, '轮询留着观察 worker 真正停下');
  stopped = true;
  await h.clock.next();
  assert.equal(h.current().job.status, 'cancelled');
  assert.equal(h.clock.queued.size, 0);
  h.unmount();
});

test('取消前发出、取消后才回来的旧查询，不能把「已请求取消」冲掉', async () => {
  const pending = [];
  const h = jobHarness({
    get: () => { const d = deferred(); pending.push(d); return d.promise; },
    cancel: async (id) => ({ id, status: 'in_progress', progress: null, cancelRequested: true }),
  });
  await h.current().start(async () => ({ id: 'paid-1', status: 'in_progress', progress: null }));
  await h.clock.next();
  assert.equal(pending.length, 1, '查询已发出、尚未返回');
  await h.current().cancel();
  assert.equal(h.current().job.cancelRequested, true);
  pending[0].resolve({ id: 'paid-1', status: 'in_progress', progress: null, cancelRequested: false });
  await settle();
  assert.equal(h.clock.queued.size, 1, '旧查询已处理完，排好了下一拍');
  assert.equal(h.current().job.cancelRequested, true, '旧结果晚到也保持已请求取消');
  await h.current().cancel();
  assert.equal(h.cancels.length, 1);
  h.unmount();
});

/* ---------------- aiJobNormalize：共用错误码表与推迟原因 ---------------- */

const BACKEND_CODES = [
  'manual_analysis_disabled', 'scheduled_analysis_disabled', 'runtime_configuration_changed',
  'submission_outcome_unknown', 'provider_incomplete_max_output_tokens', 'provider_credit_exhausted',
  'provider_incomplete', 'provider_failed', 'provider_refusal', 'provider_poll_timeout',
  'provider_poll_timeout_cancelled', 'provider_response_expired', 'provider_auth_failed',
  'provider_rate_limited', 'provider_server_error', 'provider_request_rejected', 'provider_unavailable',
  'local_storage_error', 'schema_validation_failed', 'invalid_job_payload', 'ai_empty_response',
  'ai_input_too_large', 'daily_token_limit_reached', 'budget_blocked', 'global_concurrency_limit',
  'analysis_cooldown_active', 'provider_credit_exhausted_hold', 'legacy_output_hidden',
  'duplicate_request_migrated',
  // 以下由 worker 写入，任务书的清单之外（backend/app/services/ai_jobs/worker.py、runtime.py）
  'provider_cancelled', 'provider_response_id_missing', 'provider_status_unsupported',
  'ai_job_result_too_large', 'ai_job_lease_lost', 'signal_ticker_mismatch',
  'ai_not_configured', 'ai_sdk_unavailable', 'runtime_configuration_invalid',
];

test('错误码共用表覆盖任务书清单与 worker 会写入的其余失败码，认不出的码回落通用句', () => {
  const fallback = normalize.aiJobErrorMessage(null);
  assert.equal(fallback, '分析任务失败');
  assert.equal(normalize.aiJobErrorMessage('some_future_code'), fallback);
  assert.equal(normalize.aiJobErrorMessage('constructor'), fallback, '原型链上的名字不能当成错误码');
  for (const code of BACKEND_CODES) {
    const text = normalize.aiJobErrorMessage(code);
    assert.notEqual(text, fallback, `${code} 缺专门文案`);
    assert.doesNotMatch(text, /[a-z]+_[a-z_]+/, `${code} 的文案混入了原始码：${text}`);
  }
  assert.equal(normalize.aiJobErrorMessage('provider_credit_exhausted'), 'AI 供应商余额耗尽，需充值');
  assert.match(normalize.aiJobErrorMessage('legacy_output_hidden'), /旧结果不符合当前的校验规则/);
});

test('英日界面给出译文，英文里没有中文，后端中文原文不外露', () => {
  try {
    setLocale('en');
    for (const code of BACKEND_CODES) {
      const text = normalize.aiJobErrorMessage(code);
      assert.doesNotMatch(text, CJK, `${code}: ${text}`);
    }
    assert.equal(normalize.aiJobErrorMessage('provider_credit_exhausted'), 'The AI provider is out of credit. Top up to continue.');
    assert.equal(
      normalize.aiJobCreateErrorMessage(new ApiError(409, '技术信号已过期，请先手动拉取最新行情后再分析', { bizCode: 'stale_signal_evidence' })),
      'The technical signals are out of date. Pull the latest market data first, then analyze.',
    );
    assert.equal(normalize.aiJobCreateErrorMessage(new ApiError(400, '某个中文原因')), 'Job creation failed');
    assert.equal(normalize.aiJobCreateErrorMessage(new ApiError(400, 'Plain English detail')), 'Plain English detail');
    setLocale('ja');
    assert.equal(normalize.aiJobErrorMessage('schema_validation_failed'), '分析結果が形式チェックを通りませんでした。再試行してください。');
    assert.equal(normalize.aiJobCreateErrorMessage(new ApiError(400, '某个中文原因')), 'ジョブの作成に失敗しました');
    // 本地已翻译的日文提示也有汉字，但带假名，不能被当成后端中文换掉
    const timeout = t('请求超时，请重试');
    assert.match(timeout, /[\u3040-\u30ff]/);
    assert.equal(normalize.aiJobCreateErrorMessage(new ApiError(408, timeout, { bizCode: 'request_timeout' })), timeout);
  } finally {
    setLocale('zh');
  }
});

test('创建失败文案：队列满、手动分析关闭、证据陈旧、只读模式与服务未就绪', () => {
  const message = normalize.aiJobCreateErrorMessage;
  assert.equal(message(new ApiError(429, 'Too Many Requests', { bizCode: 'ai_job_queue_full', retryAfter: 60 })), '分析队列已满，请约 60 秒后再试');
  assert.equal(message(new ApiError(429, 'Too Many Requests', { bizCode: 'ai_job_queue_full' })), '分析队列已满，请稍后再试');
  assert.equal(message(new ApiError(429, 'Too many requests; try again in 7s', { bizCode: 'rate_limited', retryAfter: 7 })), '请求过于频繁，请约 7 秒后再试');
  assert.equal(message(new ApiError(409, '手动分析已关闭', { bizCode: 'manual_analysis_disabled' })), '手动分析功能当前未启用');
  assert.equal(message(new ApiError(409, '技术信号已过期', { bizCode: 'stale_signal_evidence' })), '技术信号已过期，请先手动拉取最新行情再分析');
  assert.equal(message(new ApiError(409, '当前为只读模式', { bizCode: 'read_only_mode' })), '当前为只读模式，不能发起分析');
  assert.equal(message(new ApiError(503, 'Persistent AI analysis is not available', { bizCode: 'not_configured' })), 'AI 分析服务尚未就绪，暂时不能发起分析');
  assert.equal(message(new ApiError(413, 'Signal snapshot is too large for AI analysis')), '输入数据过多，超出了分析上限');
  assert.equal(message(new ApiError(400, '中文界面保留后端原文')), '中文界面保留后端原文');
  assert.equal(message('not an error'), '任务创建失败');
});

test('推迟原因只在任务仍排队时显示；只有失败才翻译错误码', () => {
  const queued = normalize.normalizeAiJob({ job_id: 'job-1', status: 'pending', error_code: 'global_concurrency_limit' });
  assert.equal(queued.status, 'queued');
  assert.equal(normalize.aiJobDeferralMessage(queued), '同时进行的分析较多，稍后自动开始');
  assert.equal(normalize.aiJobDeferralMessage({ status: 'queued', error: 'analysis_cooldown_active' }), '上一次分析刚结束，冷却后自动开始');
  assert.equal(normalize.aiJobDeferralMessage({ status: 'queued', error: 'provider_credit_exhausted_hold' }), 'AI 供应商余额耗尽，充值后自动继续');
  assert.equal(normalize.aiJobDeferralMessage({ status: 'queued', error: 'provider_poll_deferred' }), null);
  const running = normalize.normalizeAiJob({ job_id: 'job-1', status: 'in_progress', error_code: 'global_concurrency_limit' });
  assert.equal(normalize.aiJobDeferralMessage(running), null, '开始处理后留下的是旧码');
  const cancelled = normalize.normalizeAiJob({ job_id: 'job-1', status: 'cancelled', error_code: 'global_concurrency_limit' });
  assert.equal(normalize.aiJobBlockedMessage(cancelled), null);
  const budget = normalize.normalizeAiJob({ job_id: 'job-1', status: 'budget_blocked', error_code: 'daily_token_limit_reached' });
  assert.equal(budget.status, 'failed');
  assert.equal(normalize.aiJobBlockedMessage(budget), '今日 Token 额度已用完，额度重置后再试');
  const credit = normalize.normalizeAiJob({ job_id: 'job-1', status: 'failed', error_code: 'provider_credit_exhausted' });
  assert.equal(normalize.aiJobBlockedMessage(credit), 'AI 供应商余额耗尽，需充值');
  const schema = normalize.normalizeAiJob({ job_id: 'job-1', status: 'failed', error_code: 'schema_validation_failed' });
  assert.equal(normalize.aiJobBlockedMessage(schema), null, '能重试的失败保留「重试」');
  const unconfigured = normalize.normalizeAiJob({ job_id: 'job-1', status: 'failed', error_code: 'ai_not_configured' });
  assert.equal(normalize.aiJobBlockedMessage(unconfigured), 'AI 分析服务尚未就绪，暂时不能发起分析');
});

test('任务归一读出 cancel_requested（含旧契约的 status 写法）', () => {
  assert.equal(normalize.normalizeAiJob({ job_id: 'job-1', status: 'in_progress', cancel_requested: true }).cancelRequested, true);
  assert.equal(normalize.normalizeAiJob({ job_id: 'job-1', status: 'cancel_requested' }).cancelRequested, true);
  assert.equal(normalize.normalizeAiJob({ job_id: 'job-1', status: 'in_progress' }).cancelRequested, false);
});

test('postAiJob 把创建响应的 Retry-After 随任务带回', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const bundle = await build({
    stdin: { contents: "export { postAiJob } from './src/api/modules/ai-jobs.ts';", resolveDir: root },
    bundle: true, write: false, platform: 'node', format: 'esm', logLevel: 'silent',
    alias: { '@': `${root}/src` }, define: { 'import.meta.env': '{"VITE_API_MODE":"live"}' },
  });
  const { postAiJob } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
  const originalFetch = globalThis.fetch;
  const reply = (headers) => async () => new Response(
    JSON.stringify({ job_id: 'job_0123456789', job_type: 'signal_analysis', status: 'pending' }),
    { status: 202, headers: { 'Content-Type': 'application/json', ...headers } },
  );
  try {
    globalThis.fetch = reply({ Location: '/api/ai/jobs/job_0123456789', 'Retry-After': '7' });
    const job = await postAiJob('/signals/stock/NVDA/ai-analysis', { force: false });
    assert.equal(job.id, 'job_0123456789');
    assert.equal(job.status, 'queued');
    assert.equal(job.retryAfter, 7);
    globalThis.fetch = reply({});
    assert.equal((await postAiJob('/signals/stock/NVDA/ai-analysis', { force: false })).retryAfter, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

/* ---------------- 卡片渲染 ---------------- */

const hookState = (state) => ({
  job: null, error: null, queryIssue: null, starting: false,
  start() {}, cancel() {}, resume() {}, reset() {}, ...state,
});

function renderSignalCard(state) {
  const stub = createReactStub();
  const { exports } = compile('components/detail/AiAnalysisCard.tsx', stub.React, {
    'react-router': { Link: 'Link' },
    'framer-motion': { AnimatePresence: 'AnimatePresence', motion: new Proxy({}, { get: (_target, tag) => `motion.${String(tag)}` }) },
    '@/hooks/useAccess': { useAccess: () => ({ isOwner: true, loading: false }) },
    '@/components/icons': { default: 'Icon' },
    './api': { createSignalAnalysisJob: () => Promise.reject(new Error('unused')) },
    './useAiJob': { useAiJob: () => hookState(state) },
    '@/api/modules/ai-jobs': { aiJobBlockedMessage: normalize.aiJobBlockedMessage, aiJobResultSummary: normalize.aiJobResultSummary },
    '@/api/aiJobNormalize': normalize,
    '@/lib/quoteSymbol': { isIndexSymbol: () => false },
  });
  return snapshot(stub.mount(() => exports.default({ ticker: 'NVDA' }))());
}

function renderOptionInsight(state) {
  const stub = createReactStub();
  const { sandbox } = compile('components/detail/OptionsPanel.tsx', stub.React, {
    '@/api/client': { isMock: false },
    '@/api/modules/options': { optionsApi: {} },
    '@/api/modules/ai-jobs': { aiJobBlockedMessage: normalize.aiJobBlockedMessage, aiJobsApi: {} },
    '@/api/aiJobNormalize': normalize,
    '@/hooks/usePolling': { usePolling() { throw new Error('unused'); } },
    '@/hooks/useRetryCountdown': { useRetryCountdown() { throw new Error('unused'); } },
    '@/hooks/useAccess': { useAccess: () => ({ isOwner: true }) },
    '@/components/shared/EmptyState': { default: 'EmptyState' },
    '@/components/shared/SourceNote': { default: 'SourceNote' },
    '@/components/shared/Skeleton': { SkeletonRows: 'SkeletonRows' },
    '@/components/shared/MenuSelect': { default: 'MenuSelect' },
    '@/components/icons': { default: 'Icon' },
    '@/lib/utils': { cn: (...names) => names.filter(Boolean).join(' ') },
    '@/lib/format': { fmtPrice: String, fmtRelative: String },
    '@/mocks/fixtures2': { OPTION_SUPPORTED_LIST: '', optionsSupported: () => true },
    '@/lib/optionCapability': { isDeclaredUnsupported: () => false },
    './useAiJob': { useAiJob: () => hookState(state) },
    './optionAnalysis': { buildOptionAlertEvidence: () => [], parseOptionAlertResult: () => null },
    './options/ChainBrowser.tsx': { default: 'ChainBrowser' },
    './options/SummaryTiles.tsx': { default: 'SummaryTiles' },
  });
  return snapshot(stub.mount(() => sandbox.AiOptionInsight({ ticker: 'NVDA', expiration: '2026-10-16', chain: null }))());
}

for (const [name, render] of [['个股 AI 卡片', renderSignalCard], ['期权解读', renderOptionInsight]]) {
  test(`${name}：已请求取消时显示原因并禁用取消按钮`, () => {
    const view = render({ job: { id: 'job-1', status: 'in_progress', progress: null, cancelRequested: true } });
    assert.match(view.text, /已请求取消/);
    assert.deepEqual(view.buttons.filter((b) => b.label === '取消任务').map((b) => b.disabled), [true]);
    const active = render({ job: { id: 'job-1', status: 'in_progress', progress: null } });
    assert.match(active.text, /模型分析中…/);
    assert.deepEqual(active.buttons.filter((b) => b.label === '取消任务').map((b) => b.disabled), [false]);
  });

  test(`${name}：排队推迟显示原因，开始处理后不再显示旧码`, () => {
    const queued = render({ job: { id: 'job-1', status: 'queued', progress: null, error: 'global_concurrency_limit' } });
    assert.match(queued.text, /排队中…/);
    assert.match(queued.text, /同时进行的分析较多，稍后自动开始/);
    const running = render({ job: { id: 'job-1', status: 'in_progress', progress: null, error: 'global_concurrency_limit' } });
    assert.doesNotMatch(running.text, /同时进行的分析/);
  });

  test(`${name}：失败按共用表说明原因，取消与旧结果隐藏各有说法`, () => {
    const credit = render({ job: { id: 'job-1', status: 'failed', progress: null, error: 'provider_credit_exhausted' } });
    assert.match(credit.text, /AI 供应商余额耗尽，需充值/);
    assert.ok(credit.buttons.some((b) => b.label === '关闭'), '重试也会被挡下时按钮写「关闭」');
    const schema = render({ job: { id: 'job-1', status: 'failed', progress: null, error: 'schema_validation_failed' } });
    assert.match(schema.text, /分析结果未通过格式检查，请重试/);
    assert.ok(schema.buttons.some((b) => b.label === '重试'));
    const cancelled = render({ job: { id: 'job-1', status: 'cancelled', progress: null, error: 'global_concurrency_limit' } });
    assert.match(cancelled.text, /任务已取消/);
    assert.doesNotMatch(cancelled.text, /同时进行的分析/, '取消状态的残留码不当失败原因');
    const legacy = render({ job: { id: 'job-1', status: 'succeeded', progress: null, error: 'legacy_output_hidden' } });
    assert.match(legacy.text, /旧结果不符合当前的校验规则/);
  });
}

test('个股 AI 卡片：任务记录不存在时只给「重试」，不给「继续查询原任务」', () => {
  const view = renderSignalCard({ job: null, error: '任务记录已不存在', queryIssue: 'missing' });
  assert.match(view.text, /任务记录已不存在/);
  assert.ok(view.buttons.some((b) => b.label === '重试'));
  assert.ok(!view.buttons.some((b) => b.label === '继续查询原任务'));
});

/* ---------------- 行展开：手动拉取后的重读 ---------------- */

test('行展开：换行后，旧行手动拉取的晚到结果不能写进新行', async () => {
  const stub = createReactStub();
  const calls = [];
  const { sandbox } = compile('components/screener/RowExpansion.tsx', stub.React, {
    '@/components/shared/SoftBadge': { default: 'SoftBadge' },
    'react-router': { Link: 'Link' },
    '@/api/modules/stocks': { stocksApi: { chart: (ticker, _range, _adjust, force) => { const d = deferred(); calls.push({ ticker, force, d }); return d.promise; } } },
    '@/api/client': { ApiError },
    '@/lib/utils': { cn: (...names) => names.filter(Boolean).join(' ') },
    '@/lib/format': { fmtCompact: String },
    '@/components/icons': { default: 'Icon' },
    '@/components/shared/SignalLines': { default: 'SignalLines' },
    '@/components/charts/Sparkline': { default: 'Sparkline' },
    '@/components/shared/Skeleton': { SkeletonBlock: 'SkeletonBlock' },
    '@/lib/strengthColor': { strengthBarClass: () => '' },
    '@/components/shared/InfoHint': { default: 'InfoHint' },
    '@/components/shared/MacroFitPanel': { default: 'MacroFitPanel' },
    '@/lib/scoreHints': { SCORE_HINTS: {} },
    './types': { subscoreDimsOf: () => [] },
    '@/components/detail/ManualStockPull': { default: 'ManualStockPull' },
  });
  let row = { ticker: 'AAA', sparkline: [] };
  const view = stub.mount(() => sandbox.DotMatrixBlock({ row }));
  const candles = (...closes) => ({ candles: closes.map((c) => ({ c })) });
  const sparkData = () => findAll(view(), (node) => node.type === 'Sparkline').map((node) => node.props.data.join(','));

  calls[0].d.resolve(candles(1));
  await settle();
  const [pull] = findAll(view(), (node) => node.type === 'ManualStockPull');
  assert.ok(pull, '日线不足时给出手动拉取入口');
  pull.props.onPulled();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].force, true);

  row = { ticker: 'BBB', sparkline: [] };
  stub.rerender();
  assert.equal(calls[2].ticker, 'BBB');
  calls[2].d.resolve(candles(5, 6, 7));
  await settle();
  assert.deepEqual(sparkData(), ['5,6,7']);

  calls[1].d.resolve(candles(9, 9, 9));
  await settle();
  assert.deepEqual(sparkData(), ['5,6,7'], '旧行手动拉取的结果晚到，也不能覆盖新行');
  stub.unmount();
});

/* ---------------- 行情快照：隐藏页面撤下在途的定时快照 ---------------- */

test('页面隐藏时一并撤下在途的定时行情快照', async () => {
  let now = 0;
  let serial = 0;
  const timersById = new Map();
  const schedule = (fn, delay) => { const id = ++serial; timersById.set(id, { fn, at: now + delay }); return id; };
  const clear = (id) => { timersById.delete(id); };
  const exports = {};
  const context = vm.createContext({
    exports, require: () => ({ t: interpolate }), AbortController, URLSearchParams, console,
    Date: class extends Date { static now() { return Date.UTC(2026, 8, 8) + now; } },
    setTimeout: schedule, clearTimeout: clear,
    setInterval(fn, delay) { const id = ++serial; const repeat = () => { fn(); timersById.set(id, { fn: repeat, at: now + delay }); }; timersById.set(id, { fn: repeat, at: now + delay }); return id; },
    clearInterval: clear,
  });
  vm.runInContext(ts.transpileModule(read('lib/liveQuotes.ts'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, context);
  const status = { enabled: true, configured: true, public_enabled: true, allowed: true, connected: true, connection_status: 'connected', market_session: 'regular' };
  const requests = [];
  let hold = false;
  const store = new exports.QuoteStore({
    async fetch(url, init) {
      requests.push({ url, init });
      if (hold) return new Promise(() => {});
      return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({ quotes: [], status }) };
    },
    stream() { return { addEventListener() {}, close() {}, onerror: null }; },
  });
  const advance = async (ms) => {
    const target = now + ms;
    for (;;) {
      await settle();
      const next = [...timersById.entries()].sort((a, b) => a[1].at - b[1].at).find(([, entry]) => entry.at <= target);
      if (!next) break;
      const [id, entry] = next;
      now = entry.at; timersById.delete(id); entry.fn();
    }
    now = target;
    await settle();
  };
  store.register(['AAPL']);
  store.start(false);
  await advance(1000);
  hold = true;
  const before = requests.length;
  await advance(60_000);
  const poll = requests.slice(before).at(-1);
  assert.ok(poll, '定时快照已发出');
  assert.equal(poll.init.signal.aborted, false);
  store.setVisible(false);
  assert.equal(poll.init.signal.aborted, true);
  store.stop();
});

/* ---------------- 查询注册表：恢复路径的查询串规范化 ---------------- */

test('恢复持久记录时按写入时同样规范化的查询串读取', async () => {
  const principal = `visitor${String.fromCharCode(0)}`;
  const record = {
    path: '/macro/conditions?a=1&b=2', principal, appCommit: null, etag: '"v1"',
    raw: { ok: 1 }, storedAt: Date.now(), validatedAt: Date.now(),
  };
  const reads = [];
  const requests = [];
  const { exports: registry } = compile('api/queryRegistry.ts', null, {
    './client.ts': {
      REQUEST_TIMEOUT_MS: 20_000,
      requestRaw: async (path, init) => { requests.push({ path, init }); return new Response(null, { status: 304 }); },
    },
    './persistedCache.ts': {
      readPersisted: async (path) => { reads.push(path); return path === record.path ? record : null; },
      writePersisted: async () => {},
      deletePersisted: async () => {},
      clearPersisted: async () => {},
    },
  }, { setTimeout, clearTimeout });
  registry.setQueryPrincipal(principal);
  const restored = await registry.restorePersistedQuery('/macro/conditions?b=2&a=1');
  assert.deepEqual(reads, ['/macro/conditions?a=1&b=2']);
  assert.equal(restored?.ok, 1);
  const value = await registry.registryGet('/macro/conditions?b=2&a=1');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].init.headers['If-None-Match'], '"v1"', '恢复值挂在规范化键上，随后的读取发条件请求');
  assert.equal(value.ok, 1);
});

/* ---------------- 结构清理后的共用工具 ---------------- */

test('fmtRelative 可选小时小数位：10 小时内保留小数，默认仍取整', () => {
  const ago = (ms) => new Date(Date.now() - ms).toISOString();
  assert.equal(fmtRelative(ago(2.5 * 3_600_000), 1), '2.5 小时前');
  assert.equal(fmtRelative(ago(2.5 * 3_600_000)), '2 小时前');
  assert.equal(fmtRelative(ago(12.5 * 3_600_000), 1), '12 小时前');
  assert.equal(fmtRelative(ago(30 * 60_000), 1), '30 分钟前');
  assert.equal(fmtRelative(null, 1), '—');
});

test('图表分析共用的 clamp / finite', () => {
  assert.equal(clamp(5, 0, 1), 1);
  assert.equal(clamp(-5, 0, 1), 0);
  assert.equal(clamp(0.5, 0, 1), 0.5);
  assert.equal(finite(1), true);
  for (const value of [Number.NaN, Infinity, '1', null, undefined]) assert.equal(finite(value), false);
});

test('自选排序：null 的涨跌幅与强度沉底，与升降序无关', () => {
  const base = { name: '', sector: '', price: 1, change: null, sparkline: [], signals: [], updatedAt: '' };
  const rows = [
    { ...base, ticker: 'A', changePct: null, strengthScore: null },
    { ...base, ticker: 'B', changePct: 2, strengthScore: 50 },
    { ...base, ticker: 'C', changePct: -1, strengthScore: 70 },
  ];
  const order = (sort) => sortWatchlistItems(rows, sort).map((row) => row.ticker).join('');
  assert.equal(order({ key: 'changePct', desc: true }), 'BCA');
  assert.equal(order({ key: 'changePct', desc: false }), 'CBA');
  assert.equal(order({ key: 'strength', desc: true }), 'CBA');
});

test('可空数值不再靠 null as unknown as number 冒充（接线检查）', () => {
  for (const file of ['api/modules/stocks.ts', 'components/detail/api.ts', 'mocks/fixtures.ts']) {
    assert.doesNotMatch(read(file), /null as unknown as/, file);
  }
  const types = read('api/types.ts');
  for (const field of ['change', 'changePct', 'strengthScore', 'open', 'high', 'low', 'prevClose', 'volume', 'avgVolume', 'marketCap', 'ivPercentile']) {
    assert.match(types, new RegExp(`\\n  ${field}: number \\| null;`), field);
  }
  assert.match(types, /\n {2}range52w: \[number, number\] \| null;/);
});

test('refresh 不再直接当事件处理器往下传，共用工具取代本地副本（接线检查）', () => {
  for (const page of ['Home', 'Market', 'Screener', 'Breakouts']) {
    assert.doesNotMatch(read(`pages/${page}.tsx`), /=\{\w+Q\.refresh\}/, `${page}.tsx`);
  }
  const lead = read('components/breakouts/LeadBigCard.tsx');
  assert.match(lead, /import \{ num, str \} from '@\/api\/live'/);
  assert.match(lead, /fmtRelative\(e\.event_at, 1\)/);
  assert.doesNotMatch(lead, /function observedAgo|const num =|const str =/);
  for (const file of ['smartLines.ts', 'structuralOverlays.ts', 'priceGaps.ts']) {
    assert.doesNotMatch(read(`components/detail/chart-drawings/analysis/${file}`), /const (clamp|finite) =/, file);
  }
});

test('零引用的旧代码与死词条已清掉（接线检查）', () => {
  const fixtures2 = read('mocks/fixtures2.ts');
  // 按旧版签名匹配：V2 去掉后缀后会重新用回这些名字，不能误伤。
  for (const signature of [
    /export function getCatalystsFeed\(page = 1/,
    /export function getNewsById\(/,
    /export function getCatalystsStatus\(\): CatalystsStatus \{/,
    /export function getHotspots\(\): Hotspot\[\]/,
    /export function getHotspotsStatus\(\): \{ scanning/,
    /export function getLatestFocusCycle\(\): FocusCycle \{/,
    /getEarningsRefreshCount/,
    /export function submitTickersBatch/,
  ]) {
    assert.doesNotMatch(fixtures2, signature);
  }
  assert.doesNotMatch(read('lib/motion.ts'), /EASE_SNAP/);
  assert.doesNotMatch(read('lib/screenerScanFlow.ts'), /export \{ fallbackQuoteLabel \}/);
  assert.doesNotMatch(read('components/detail/chart-drawings/DrawingToolbar.tsx'), /export \{ TOOLS \}/);
  assert.doesNotMatch(read('components/detail/chart-drawings/analysis/mapBundle.ts'), /export \{ sha256Hex \}/);
  for (const msgid of [
    '本页暂不提供板块资金流、相关性与历史趋势。', '该板块成分暂无可用的期权样本，可切换板块或重新加载',
    '板块排位是同板块成分之间的横向比较，不是该股自己的历史高低位；期权与价格均为延迟数据',
    '排位只比较当前板块成分的 ATM IV，不代表一年历史百分位。', '暂无日线走势，打开详情后可更新',
    '自选暂时都不在行情覆盖范围内', '上方列出的代码已保存在账号里，行情覆盖后会自动出现', '加自选', '添加自选股票代码',
    '读不到你的自选列表，下面显示的是系统默认关注池。', '你还没有自己的自选，下面是系统默认关注池。',
    '上方输入代码即可开始建立自己的列表。', '在上方输入股票代码，加入你的第一只自选',
  ]) {
    assert.equal(msgid in DICT, false, msgid);
  }
});
