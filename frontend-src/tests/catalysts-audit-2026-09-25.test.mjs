/**
 * 2026-09-25 审计（第 5 节 FE-1 ~ FE-8 与催化剂相关的低优先级条目）回归。
 *
 * 组件用 React 桩驱动真实源码；新闻抽屉接的是真实 api.ts 归一化，喂的是后端真实形状
 * （GET /catalysts/analysis-jobs/{id} 只有 AIJobPublic + submission_source，没有 news_id 与 progress）。
 * 计时器全部换成虚拟时钟，advance() 按到期顺序触发。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { createReactStub } from './helpers/react-hooks.mjs';
import { deferred } from './helpers/deferred.mjs';
import * as boundedReadRetry from '../src/lib/boundedReadRetry.ts';
import * as live from '../src/api/live.ts';
import * as focusCycleRequest from '../src/components/catalysts/focusCycleRequest.ts';
import * as analysisErrorText from '../src/components/catalysts/analysisErrorText.ts';
import * as feedSnapshot from '../src/components/catalysts/feedSnapshot.ts';
import * as feedPatches from '../src/components/catalysts/feedPatches.ts';
import * as filters from '../src/components/catalysts/filters.ts';
import { cacheStatusProps } from '../src/components/catalysts/cacheStatusProps.ts';
import { ResourceCache } from '../src/components/catalysts/resourceCache.ts';
import { createReadBackoff } from '../src/components/catalysts/readBackoff.ts';
import { normalizeNewsAnalysisProgress } from '../src/components/catalysts/analysisProgressContract.ts';
import { remoteState } from '../src/hooks/remoteState.ts';
import { setLocale } from '../src/i18n/testing.ts';
import { SHARED_UI_STUBS } from './helpers/shared-ui-stubs.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(here, '../src');

/* ---------------- 通用桩 ---------------- */

async function settle() {
  for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function stubT(msgid, vars) {
  return vars ? msgid.replace(/\{(\w+)\}/g, (whole, key) => (vars[key] === undefined || vars[key] === null ? whole : String(vars[key]))) : msgid;
}
const i18n = { t: stubT };

function fakeClock(start = 1_000_000) {
  const timers = new Map();
  let serial = 0;
  let now = start;
  const api = {
    setTimeout(fn, ms = 0) { const id = ++serial; timers.set(id, { fn, at: now + ms, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, ms) { const id = ++serial; timers.set(id, { fn, at: now + ms, ms, every: ms }); return id; },
    clearInterval(id) { timers.delete(id); },
  };
  class FakeDate extends Date {
    static now() { return now; }
  }
  async function advance(ms) {
    const target = now + ms;
    for (;;) {
      const next = [...timers.entries()]
        .filter(([, row]) => row.at <= target)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!next) break;
      const [id, row] = next;
      now = Math.max(now, row.at);
      if (row.every) row.at = now + row.every;
      else timers.delete(id);
      row.fn();
      await settle();
    }
    now = target;
    await settle();
  }
  return { api, timers, advance, Date: FakeDate, now: () => now };
}

function fakeEventTarget(extra = {}) {
  const listeners = new Map();
  return {
    ...extra,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    dispatch(type) { for (const fn of [...(listeners.get(type) ?? [])]) fn({ type }); },
    count(type) { return listeners.get(type)?.size ?? 0; },
  };
}

function compile(rel, imports, globals = {}) {
  const source = fs.readFileSync(path.join(srcRoot, rel), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    console,
    URLSearchParams,
    queueMicrotask,
    Promise,
    Error,
    TypeError,
    ...globals,
    require(id) {
      if (id in imports) return imports[id];
      if (id in SHARED_UI_STUBS) return SHARED_UI_STUBS[id];
      throw new Error(`Unexpected import ${id} in ${rel}`);
    },
  });
  return module.exports;
}

const passthroughJsx = {
  jsx: (type, props) => ({ type, props }),
  jsxs: (type, props) => ({ type, props }),
  Fragment: 'Fragment',
};
/* 本文件内的小组件（CycleSummary 等）不含 hook，直接展开，好断言它们画出的文案。 */
const invokingJsx = {
  jsx: (type, props) => (typeof type === 'function' ? type(props) : { type, props }),
  jsxs: (type, props) => (typeof type === 'function' ? type(props) : { type, props }),
  Fragment: 'Fragment',
};
const motionStub = { AnimatePresence: 'AnimatePresence', motion: new Proxy({}, { get: (_, key) => `motion.${String(key)}` }), useReducedMotion: () => true };

/* children 之外，只顺着像节点的属性（如 StatusNotice 的 action）往下找。 */
function childNodes(node) {
  if (!node || typeof node !== 'object' || !node.props) return [];
  const out = [];
  for (const [key, value] of Object.entries(node.props)) {
    if (key === 'children') out.push(value);
    else if (value && typeof value === 'object' && (Array.isArray(value) || 'props' in value)) out.push(value);
  }
  return out;
}

function collectText(node, out = []) {
  if (node == null || node === false || node === true) return out;
  if (typeof node === 'string' || typeof node === 'number') {
    out.push(String(node));
    return out;
  }
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, out);
    return out;
  }
  if (typeof node === 'object' && node.props) {
    for (const child of childNodes(node)) collectText(child, out);
  }
  return out;
}
const textOf = (node) => collectText(node).join(' ');

function findNode(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findNode(child, predicate);
      if (found) return found;
    }
    return null;
  }
  if (node.props && predicate(node)) return node;
  for (const child of childNodes(node)) {
    const found = findNode(child, predicate);
    if (found) return found;
  }
  return null;
}
const findButton = (tree, label) => findNode(tree, (node) => node.type === 'button' && textOf(node).trim() === label);

/* ---------------- 真实 api.ts（客户端层打桩） ---------------- */

class TestApiError extends Error {
  constructor(code, message = 'error', extras = {}) {
    super(message);
    this.code = code;
    Object.assign(this, extras);
  }
}

function loadCatalystApi({ get, post, postCreate } = {}) {
  const calls = { principalInvalid: 0, invalidations: 0, get: [], post: [] };
  const api = compile('components/catalysts/api.ts', {
    '@/api/client': {
      ApiError: TestApiError,
      get: async (url) => { calls.get.push(url); return get ? get(url) : {}; },
      post: async (url, body) => { calls.post.push([url, body]); return post ? post(url, body) : {}; },
      postCreate: async (url, body) => { calls.post.push([url, body]); return postCreate ? postCreate(url, body) : { data: {}, location: null }; },
      idFromLocation: (location) => (location ? location.split('/').filter(Boolean).pop() : null),
      invalidateBootPrefetch: () => {},
      mockOr: (_mock, liveRead) => liveRead(),
      notifyPrincipalInvalid: () => { calls.principalInvalid += 1; },
      toQuery: (params) => new URLSearchParams(
        Object.entries(params).filter(([, value]) => value !== undefined).map(([key, value]) => [key, String(value)]),
      ).toString(),
    },
    '@/api/live': live,
    '@/mocks/fixtures2': new Proxy({}, { get: () => () => { throw new Error('测试不应进入演示数据分支'); } }),
    '@/components/catalysts/focusCycleRequest': focusCycleRequest,
    '../../i18n/core.ts': i18n,
    './resourceSignals': { notifyCatalystReadsInvalidated: () => { calls.invalidations += 1; } },
  });
  return { api, calls };
}

/* 后端 AIJobPublic + submission_source：刻意没有 news_id 与 progress。 */
function aiJob(partial = {}) {
  return {
    job_id: 'job_news_0000000001',
    job_type: 'news_impact',
    status: 'queued',
    model: 'gpt-test',
    reasoning: 'low',
    submitted_at: '2026-09-25T00:00:00Z',
    updated_at: '2026-09-25T00:00:01Z',
    completed_at: null,
    error_code: null,
    error_detail: null,
    retry_after: null,
    result: null,
    cached: false,
    cancellable: true,
    cancel_requested: false,
    analysis_revision: 1,
    cycle_revision: null,
    budget_charge_usd: 0,
    usage: {},
    submission_source: 'manual',
    ...partial,
  };
}

function newsRow(partial = {}) {
  return {
    news_id: 9600,
    source: 'Reuters',
    source_count: 1,
    title: '第9600条快讯',
    title_zh: '第9600条快讯',
    summary: '摘要',
    summary_zh: '摘要',
    url: 'https://example.test/n',
    published_at: '2026-09-25T00:00:00Z',
    fetched_at: '2026-09-25T00:00:00Z',
    source_tickers: ['AAPL'],
    analysis_status: 'not_requested',
    analysis: null,
    ...partial,
  };
}

const completedAnalysis = {
  classification: 'bullish',
  confidence: 80,
  headline_summary: '完整分析',
  causal_summary: '分析原因',
  affected_stocks: [],
  generated_at: '2026-09-25T00:10:00Z',
};

function detail(row, job) {
  return job === undefined ? { item: row } : { item: row, analysis_job: job };
}

/* ---------------- 新闻抽屉（真实 api + 真实 NewsDrawer） ---------------- */

function drawerHarness({ get, post, postCreate, document: doc } = {}) {
  const clock = fakeClock();
  const runner = createReactStub();
  const toasts = [];
  const updates = [];
  const { api, calls } = loadCatalystApi({ get, post, postCreate });
  const Drawer = compile('components/catalysts/NewsDrawer.tsx', {
    react: runner.React,
    'react/jsx-runtime': passthroughJsx,
    'framer-motion': motionStub,
    '@/components/Drawer': { default: 'Drawer' },
    '@/components/icons': { default: 'Icon' },
    '@/components/shared/AnalysisIcon': { default: 'AnalysisIcon' },
    '@/components/shared/Skeleton': { SkeletonBlock: 'SkeletonBlock', SkeletonText: 'SkeletonText' },
    '@/components/shared/SoftBadge': { default: 'SoftBadge' },
    '@/hooks/useAccess': { useAccess: () => ({ isOwner: true, loading: false }) },
    '@/hooks/useToast': { useToast: () => ({
      error: (title, body) => toasts.push(['error', title, body]),
      success: (title, body) => toasts.push(['success', title, body]),
      info: (title, body) => toasts.push(['info', title, body]),
    }) },
    '@/hooks/useShell': { useShell: () => ({ openTicker() {} }) },
    '@/lib/format': { fmtLocaleDateTime: () => 't', fmtLocaleTime: () => 't' },
    '@/api/queryRegistry': { getQueryPrincipalGeneration: () => 0 },
    '@/lib/boundedReadRetry': boundedReadRetry,
    './api': api,
    './analysisErrorText': analysisErrorText,
    './bits': {
      AnalysisStatusChip: 'AnalysisStatusChip', ClassificationChip: 'ClassificationChip', ConfidenceLabel: 'ConfidenceLabel',
      ImpactValue: 'ImpactValue', Led: 'Led', StaleChip: 'StaleChip', TickerChip: 'TickerChip',
    },
    './ConfirmDialog': { default: 'ConfirmDialog' },
    '../../i18n/core.ts': i18n,
  }, {
    window: { setTimeout: clock.api.setTimeout, clearTimeout: clock.api.clearTimeout },
    Date: clock.Date,
    ...(doc ? { document: doc } : {}),
  }).default;
  let read = () => null;
  return {
    clock,
    calls,
    toasts,
    updates,
    tree: () => read(),
    render(props) {
      read = runner.mount(() => Drawer({ onClose() {}, onUpdate: (row) => updates.push(row), ...props }));
    },
    unmount: () => runner.unmount(),
    jobReads: () => calls.get.filter((url) => url.startsWith('/catalysts/analysis-jobs/')).length,
    newsReads: () => calls.get.filter((url) => url.startsWith('/catalysts/news/')).length,
  };
}

function chipOf(tree) {
  const chip = findNode(tree, (node) => node.type === 'AnalysisStatusChip');
  return chip?.props.status ?? null;
}

async function confirm(h, buttonLabel, confirmLabel) {
  const button = findButton(h.tree(), buttonLabel);
  assert.ok(button, `缺少按钮 ${buttonLabel}`);
  button.props.onClick();
  const dialog = findNode(h.tree(), (node) => node.type === 'ConfirmDialog' && node.props.open && node.props.confirmLabel === confirmLabel);
  assert.ok(dialog, `缺少确认框 ${confirmLabel}`);
  dialog.props.onConfirm();
  await settle();
}

test('FE-1 打开正在分析的新闻：任务响应不带 news_id 时轮询照常走到完成并提示', async () => {
  const statuses = ['queued', 'in_progress', 'in_progress', 'completed'];
  let jobRound = 0;
  let finished = false;
  const h = drawerHarness({
    get: (url) => {
      if (url.startsWith('/catalysts/analysis-jobs/')) {
        const status = statuses[Math.min(jobRound, statuses.length - 1)];
        jobRound += 1;
        if (status === 'completed') finished = true;
        return aiJob({ status, completed_at: status === 'completed' ? '2026-09-25T00:10:00Z' : null });
      }
      if (url === '/catalysts/news/9600') {
        return finished
          ? detail(newsRow({ analysis_status: 'completed', analysis: completedAnalysis }), aiJob({ status: 'completed' }))
          : detail(newsRow({ analysis_status: 'queued' }), aiJob({ status: 'queued' }));
      }
      throw new TestApiError(404, 'unexpected');
    },
  });
  h.render({ newsId: '9600' });
  await settle();
  assert.equal(h.jobReads(), 1, '恢复读取一次');
  await h.clock.advance(2_000);
  assert.equal(h.jobReads(), 2, '第二拍必须照常查询（修复前守卫永假，停在这里）');
  await h.clock.advance(3_000);
  await h.clock.advance(5_000);
  assert.equal(h.jobReads(), 4);
  assert.ok(h.toasts.some((row) => row[0] === 'success' && row[1] === 'AI 分析已完成'));
  assert.equal(h.updates.at(-1).analysisStatus, 'completed');
  assert.match(textOf(h.tree()), /完整分析/);
  h.unmount();
});

test('FE-1 新提交的任务：状态从排队变进行中后仍继续轮询到完成', async () => {
  const statuses = ['in_progress', 'in_progress', 'completed'];
  let jobRound = 0;
  let finished = false;
  const h = drawerHarness({
    get: (url) => {
      if (url.startsWith('/catalysts/analysis-jobs/')) {
        const status = statuses[Math.min(jobRound, statuses.length - 1)];
        jobRound += 1;
        if (status === 'completed') finished = true;
        return aiJob({ job_id: 'job_news_0000000002', status });
      }
      return finished
        ? detail(newsRow({ analysis_status: 'completed', analysis: completedAnalysis }), aiJob({ job_id: 'job_news_0000000002', status: 'completed' }))
        : detail(newsRow());
    },
    postCreate: () => ({
      data: aiJob({ job_id: 'job_news_0000000002', status: 'queued' }),
      location: '/api/catalysts/analysis-jobs/job_news_0000000002',
    }),
  });
  h.render({ newsId: '9600' });
  await settle();
  await confirm(h, '生成 AI 分析', '生成分析');
  assert.equal(h.calls.post.length, 1);
  await h.clock.advance(2_000);
  await h.clock.advance(3_000);
  await h.clock.advance(5_000);
  assert.equal(h.jobReads(), 3, '排队→进行中之后不得停查');
  assert.ok(h.toasts.some((row) => row[0] === 'success' && row[1] === 'AI 分析已完成'));
  assert.equal(h.updates.at(-1).analysisStatus, 'completed');
  h.unmount();
});

test('FE-1 截止时间按本次轮询计：关抽屉超过 5 分钟后重开，同一任务重新自动查询', async () => {
  const h = drawerHarness({
    get: (url) => (url.startsWith('/catalysts/analysis-jobs/')
      ? aiJob({ status: 'in_progress' })
      : detail(newsRow({ analysis_status: 'in_progress' }), aiJob({ status: 'in_progress' }))),
  });
  h.render({ newsId: '9600' });
  await settle();
  await h.clock.advance(2_000);
  h.render({ newsId: null });
  await settle();
  await h.clock.advance(6 * 60_000);
  const before = h.jobReads();
  h.render({ newsId: '9600' });
  await settle();
  await h.clock.advance(2_000);
  assert.ok(h.jobReads() >= before + 2, '重开后恢复读取与下一拍轮询都要发生');
  assert.doesNotMatch(textOf(h.tree()), /自动查询已暂停/);
  h.unmount();
});

test('1-C 页面隐藏时暂停轮询、不计入 5 分钟，回到前台立即补查', async () => {
  const doc = fakeEventTarget({ visibilityState: 'visible' });
  const h = drawerHarness({
    document: doc,
    get: (url) => (url.startsWith('/catalysts/analysis-jobs/')
      ? aiJob({ status: 'in_progress' })
      : detail(newsRow({ analysis_status: 'in_progress' }), aiJob({ status: 'in_progress' }))),
  });
  h.render({ newsId: '9600' });
  await settle();
  doc.visibilityState = 'hidden';
  const before = h.jobReads();
  await h.clock.advance(10 * 60_000);
  assert.equal(h.jobReads(), before, '隐藏期间不查');
  assert.equal(doc.count('visibilitychange'), 1);
  doc.visibilityState = 'visible';
  doc.dispatch('visibilitychange');
  await settle();
  assert.equal(h.jobReads(), before + 1, '回到前台立即补查');
  assert.doesNotMatch(textOf(h.tree()), /自动查询已暂停/, '隐藏时间不消耗自动查询额度');
  h.unmount();
  assert.equal(doc.count('visibilitychange'), 0, '卸载后不留监听');
});

test('1-C 轮询失败的间隔取本地退避与 Retry-After 的较大值', async () => {
  let jobRound = 0;
  const h = drawerHarness({
    get: (url) => {
      if (!url.startsWith('/catalysts/analysis-jobs/')) return detail(newsRow({ analysis_status: 'in_progress' }), aiJob({ status: 'in_progress' }));
      jobRound += 1;
      if (jobRound === 2) throw new TestApiError(503, 'busy', { retryAfter: 20, retryable: true });
      return aiJob({ status: 'in_progress' });
    },
  });
  h.render({ newsId: '9600' });
  await settle();
  await h.clock.advance(2_000);
  assert.equal(h.jobReads(), 2);
  await h.clock.advance(5_000);
  assert.equal(h.jobReads(), 2, '服务端要求 20 秒，不能按本地 5 秒重试');
  await h.clock.advance(15_000);
  assert.equal(h.jobReads(), 3);
  h.unmount();
});

test('FE-3 失败按错误码给文案：提示不露原始码，失败原因不会几百毫秒后退回笼统文案', async () => {
  let failed = false;
  let jobRound = 0;
  const h = drawerHarness({
    get: (url) => {
      if (url.startsWith('/catalysts/analysis-jobs/')) {
        jobRound += 1;
        if (jobRound < 2) return aiJob({ status: 'in_progress' });
        failed = true;
        return aiJob({ status: 'failed', error_code: 'provider_credit_exhausted' });
      }
      return failed
        ? detail(newsRow({ analysis_status: 'failed' }), aiJob({ status: 'failed', error_code: 'provider_credit_exhausted' }))
        : detail(newsRow({ analysis_status: 'in_progress' }), aiJob({ status: 'in_progress' }));
    },
  });
  h.render({ newsId: '9600' });
  await settle();
  await h.clock.advance(2_000);
  const toast = h.toasts.find((row) => row[0] === 'error' && row[1] === '分析失败');
  assert.ok(toast);
  assert.equal(toast[2], '模型服务余额不足，充值后可重试');
  assert.ok(h.toasts.every((row) => !String(row[2] ?? '').includes('provider_credit_exhausted')));
  await h.clock.advance(5_000);
  const text = textOf(h.tree());
  assert.match(text, /模型服务余额不足，充值后可重试/);
  assert.doesNotMatch(text, /分析结果未通过检查/);
  assert.ok(findButton(h.tree(), '重试分析（强制）'));
  h.unmount();
});

test('FE-3 重开失败的新闻：原因取自详情里的关联任务', async () => {
  const h = drawerHarness({
    get: () => detail(newsRow({ analysis_status: 'failed' }), aiJob({ status: 'failed', error_code: 'schema_validation_failed' })),
  });
  h.render({ newsId: '9600' });
  await settle();
  assert.match(textOf(h.tree()), /模型返回的结果没有通过格式或语言检查，请重试/);
  assert.equal(h.jobReads(), 0, '终态不恢复轮询');
  h.unmount();
});

test('FE-3 budget_blocked 在条目层与任务层同一口径：失败带原因，只给强制重试', async () => {
  const { api } = loadCatalystApi({
    get: (url) => {
      if (url.startsWith('/catalysts/analysis-jobs/job_news_0000000009')) return aiJob({ job_id: 'job_news_0000000009', status: 'budget_blocked' });
      if (url.startsWith('/catalysts/analysis-jobs/')) return aiJob({ status: 'budget_blocked', error_code: 'daily_token_limit_reached' });
      if (url.startsWith('/catalysts/feed')) return { items: [newsRow({ analysis_status: 'budget_blocked' })], summary: { count: 1 } };
      return detail(newsRow({ analysis_status: 'budget_blocked' }), aiJob({ status: 'budget_blocked', error_code: 'daily_token_limit_reached' }));
    },
  });
  const item = await api.catalystsContract.news('9600');
  assert.equal(item.analysisStatus, 'failed');
  assert.equal(item.analysisErrorCode, 'daily_token_limit_reached');
  const job = await api.catalystsContract.analysisJob('job_news_0000000001');
  assert.equal(job.status, 'failed');
  assert.equal(job.error, 'daily_token_limit_reached');
  const bare = await api.catalystsContract.analysisJob('job_news_0000000009');
  assert.equal(bare.error, 'budget_blocked', '没有错误码时用状态本身作原因');
  const feed = await api.catalystsContract.feed({ limit: 12 });
  assert.equal(feed.items[0].analysisStatus, 'failed');

  const h = drawerHarness({
    get: () => detail(newsRow({ analysis_status: 'budget_blocked' }), aiJob({ status: 'budget_blocked', error_code: 'daily_token_limit_reached' })),
  });
  h.render({ newsId: '9600' });
  await settle();
  assert.match(textOf(h.tree()), /今天的 AI 用量已用完，这次没有执行分析/);
  assert.ok(findButton(h.tree(), '重试分析（强制）'));
  assert.equal(findButton(h.tree(), '生成 AI 分析'), null, '不带 force 只会拿回同一条受限任务');
  h.unmount();
});

test('进度字段：真实后端不给 progress 时不渲染「等待服务端状态」或百分比占位', async () => {
  const h = drawerHarness({
    get: (url) => (url.startsWith('/catalysts/analysis-jobs/')
      ? aiJob({ status: 'in_progress' })
      : detail(newsRow({ analysis_status: 'in_progress' }), aiJob({ status: 'in_progress' }))),
  });
  h.render({ newsId: '9600' });
  await settle();
  const stepper = findNode(h.tree(), (node) => typeof node.type === 'function' && node.type.name === 'JobStepper');
  assert.ok(stepper, '运行中显示任务状态');
  assert.equal(stepper.props.job.progress, null);
  const rendered = textOf(stepper.type(stepper.props));
  assert.match(rendered, /模型分析中/);
  assert.doesNotMatch(rendered, /等待服务端状态|%/);
  h.unmount();
});

test('1-B 提交响应回来时抽屉已换到别的新闻：结果只回写列表，不写进新打开的那条', async () => {
  const created = deferred();
  const h = drawerHarness({
    get: (url) => (url === '/catalysts/news/9601'
      ? detail(newsRow({ news_id: 9601, title_zh: '第9601条快讯' }))
      : detail(newsRow())),
    postCreate: () => created.promise,
  });
  h.render({ newsId: '9600' });
  await settle();
  await confirm(h, '生成 AI 分析', '生成分析');
  h.render({ newsId: '9601' });
  await settle();
  const newsReadsBefore = h.newsReads();
  created.resolve({ data: aiJob({ job_id: 'job_news_0000000003' }), location: '/api/catalysts/analysis-jobs/job_news_0000000003' });
  await settle();
  await h.clock.advance(10_000);
  const text = textOf(h.tree());
  assert.match(text, /第9601条快讯/);
  assert.doesNotMatch(text, /任务排队中|模型分析中/);
  assert.equal(h.jobReads(), 0, '不得替新打开的新闻轮询别人的任务');
  assert.equal(h.newsReads(), newsReadsBefore, '不得作废新新闻的详情读取');
  const written = h.updates.filter((row) => row.analysisJobId === 'job_news_0000000003');
  assert.equal(written.length, 1);
  assert.equal(written[0].newsId, '9600', '列表按原新闻回写');
  h.unmount();
});

test('1-B 取消响应回来时已换到别的新闻：不写进新新闻', async () => {
  const cancelled = deferred();
  const h = drawerHarness({
    get: (url) => {
      if (url === '/catalysts/news/9601') return detail(newsRow({ news_id: 9601, title_zh: '第9601条快讯' }));
      if (url.startsWith('/catalysts/analysis-jobs/')) return aiJob({ status: 'queued' });
      return detail(newsRow({ analysis_status: 'queued' }), aiJob({ status: 'queued' }));
    },
    post: () => cancelled.promise,
  });
  h.render({ newsId: '9600' });
  await settle();
  await confirm(h, '取消任务', '取消任务');
  h.render({ newsId: '9601' });
  await settle();
  cancelled.resolve(aiJob({ status: 'cancelled', cancellable: false }));
  await settle();
  const text = textOf(h.tree());
  assert.match(text, /第9601条快讯/);
  assert.doesNotMatch(text, /任务已取消|已取消/);
  assert.ok(!h.toasts.some((row) => row[1] === '任务已取消'));
  h.unmount();
});

test('1-E 排队中的任务当场取消：芯片、列表与提示一致，并只给强制重试', async () => {
  const h = drawerHarness({
    get: (url) => (url.startsWith('/catalysts/analysis-jobs/')
      ? aiJob({ status: 'queued' })
      : detail(newsRow({ analysis_status: 'queued' }), aiJob({ status: 'queued' }))),
    post: () => aiJob({ status: 'cancelled', cancellable: false, completed_at: '2026-09-25T00:02:00Z' }),
  });
  h.render({ newsId: '9600' });
  await settle();
  assert.equal(chipOf(h.tree()), 'queued');
  await confirm(h, '取消任务', '取消任务');
  const tree = h.tree();
  const text = textOf(tree);
  assert.match(text, /已取消/);
  assert.match(text, /任务已取消/);
  assert.equal(chipOf(tree), null, '不再显示「排队中」芯片');
  assert.doesNotMatch(text, /任务排队中|模型分析中/);
  assert.equal(h.updates.at(-1).analysisStatus, 'pending');
  assert.ok(findButton(tree, '重试分析（强制）'));
  assert.equal(findButton(tree, '生成 AI 分析'), null);
  h.unmount();
});

test('1-E 运行中的任务请求取消：显示「取消中」，不再给取消按钮', async () => {
  let requested = false;
  const running = () => aiJob({ status: 'in_progress', cancellable: !requested, cancel_requested: requested });
  const h = drawerHarness({
    get: (url) => (url.startsWith('/catalysts/analysis-jobs/')
      ? running()
      : detail(newsRow({ analysis_status: 'in_progress' }), aiJob({ status: 'in_progress' }))),
    post: () => {
      requested = true;
      return running();
    },
  });
  h.render({ newsId: '9600' });
  await settle();
  await confirm(h, '取消任务', '取消任务');
  const tree = h.tree();
  assert.match(textOf(tree), /取消中/);
  assert.equal(findButton(tree, '取消任务'), null);
  const stepper = findNode(tree, (node) => typeof node.type === 'function' && node.type.name === 'JobStepper');
  assert.match(textOf(stepper.type(stepper.props)), /已请求取消，等待服务端确认/);
  assert.ok(h.toasts.some((row) => row[1] === '已请求取消'));
  h.unmount();
});

/* ---------------- api.ts 归一化 ---------------- */

test('FE-2 已登录客户账户收到 owner_login_required：只是能力说明，不触发身份失效', async () => {
  const { api, calls } = loadCatalystApi({
    get: () => ({
      status: 'active',
      enabled: true,
      last_sync_at: '2026-09-25T00:00:00Z',
      streams: {},
      analysis_availability: { enabled: false, reason: 'owner_login_required' },
    }),
  });
  for (let i = 0; i < 3; i += 1) {
    const status = await api.catalystsContract.status();
    assert.equal(status.analysisReason, 'owner_login_required');
    assert.equal(status.analysisAvailable, false);
    api.clearCatalystReadCache();
  }
  assert.equal(calls.principalInvalid, 0);
});

test('FE-5 焦点周期归一化保留取消与预算受限终态，并读出原因码', async () => {
  const cycleId = `mfc_${'a'.repeat(32)}`;
  const records = {
    cancelled: { cycle_id: cycleId, status: 'cancelled', error_code: 'provider_cancelled' },
    budget: { cycle_id: cycleId, status: 'budget_blocked', error_code: 'daily_token_limit_reached' },
    running: { cycle_id: cycleId, status: 'in_progress', error_code: 'global_concurrency_limit' },
    requested: { cycle_id: cycleId, status: 'cancel_requested' },
  };
  let current = 'cancelled';
  const { api } = loadCatalystApi({ get: () => records[current] });
  let job = await api.catalystsContract.focusCycleJob(cycleId);
  assert.equal(job.status, 'cancelled');
  assert.equal(job.errorCode, 'provider_cancelled');
  current = 'budget';
  job = await api.catalystsContract.focusCycleJob(cycleId);
  assert.equal(job.status, 'budget_blocked');
  assert.equal(job.errorCode, 'daily_token_limit_reached');
  current = 'running';
  job = await api.catalystsContract.focusCycleJob(cycleId);
  assert.equal(job.status, 'in_progress');
  assert.equal(job.errorCode, null, '运行中的排队残留码不是失败原因');
  current = 'requested';
  job = await api.catalystsContract.focusCycleJob(cycleId);
  assert.equal(job.status, 'in_progress');
});

test('FE-5 latest 失败尝试带出原因码', async () => {
  const { api } = loadCatalystApi({
    get: () => ({
      status: 'active',
      cycle: { cycle_id: `mfc_${'b'.repeat(32)}`, status: 'budget_blocked', error_code: 'daily_token_limit_reached', created_at: '2026-09-25T00:00:00Z' },
      latest_successful_cycle: { cycle_id: `mfc_${'c'.repeat(32)}`, status: 'completed', result: { title_zh: '上次结果', summary_zh: '摘要' } },
    }),
  });
  const cycle = await api.catalystsContract.latestFocusCycle();
  assert.equal(cycle.dominantEvent, '上次结果');
  assert.equal(cycle.latestAttempt.status, 'budget_blocked');
  assert.equal(cycle.latestAttempt.errorCode, 'daily_token_limit_reached');
});

test('原因码文案：审计点名的码都有可读说法，未知码与排队推迟码不当失败原因', () => {
  for (const code of [
    'schema_validation_failed',
    'provider_credit_exhausted',
    'daily_token_limit_reached',
    'budget_blocked',
    'submission_outcome_unknown',
    'provider_incomplete_max_output_tokens',
    'manual_analysis_disabled',
    'runtime_configuration_changed',
    'provider_poll_timeout',
    'focus_prepare_expired',
    'market_focus_payload_mismatch',
  ]) {
    const text = analysisErrorText.analysisFailureReason(code);
    assert.ok(text, code);
    assert.ok(!text.includes(code) && !/[a-z]+_[a-z]+/.test(text), `${code} 不得露出原始码：${text}`);
  }
  assert.equal(analysisErrorText.analysisFailureReason('some_new_code'), null);
  assert.equal(analysisErrorText.analysisFailureReason('global_concurrency_limit'), null);
  assert.equal(analysisErrorText.newsAnalysisFailureText('some_new_code'), '这次分析没有完成，可以重试');
  assert.equal(analysisErrorText.newsAnalysisFailureText(null), '这次分析没有完成，可以重试');
  assert.deepEqual(analysisErrorText.focusCycleOutcome('cancelled', 'x'), { title: '焦点周期已取消', reason: '可以重新触发计算' });
  assert.equal(analysisErrorText.focusCycleOutcome('budget_blocked', null).title, '焦点周期没有执行');
  assert.equal(analysisErrorText.focusCycleOutcome('failed', 'focus_prepare_expired').reason, '热点准备已过期，请重新计算');
});

/* ---------------- 焦点周期卡（真实 usePolling + remoteState） ---------------- */

const CYCLE_ID = `mfc_${'d'.repeat(32)}`;

function cycle(partial = {}) {
  return {
    cycleId: CYCLE_ID,
    dominantEvent: '主导事件',
    stage: null,
    startedAt: '2026-09-25T00:00:00Z',
    generatedAt: '2026-09-25T00:05:00Z',
    trigger: 'manual',
    model: 'gpt-test',
    newsCount: 3,
    summary: '周期摘要',
    assessments: [],
    status: 'completed',
    errorCode: null,
    ...partial,
  };
}

function focusJob(partial = {}) {
  return { jobId: 'job_focus_000001', status: 'queued', progress: null, submittedAt: '', updatedAt: '', cycleId: CYCLE_ID, errorCode: null, ...partial };
}

function focusHarness({ latest, previous, trigger, poll }) {
  const clock = fakeClock();
  const runner = createReactStub();
  const doc = fakeEventTarget({ visibilityState: 'visible' });
  const toasts = [];
  const refreshed = [];
  const calls = { latest: 0, previous: 0, trigger: [], poll: [] };
  const usePollingModule = compile('hooks/usePolling.ts', {
    react: runner.React,
    '@/api/client': { ApiError: TestApiError },
    '../i18n/core.ts': i18n,
  }, { document: doc, setInterval: clock.api.setInterval, clearInterval: clock.api.clearInterval });
  const Card = compile('components/catalysts/FocusCycleCard.tsx', {
    react: runner.React,
    'react/jsx-runtime': invokingJsx,
    'framer-motion': motionStub,
    '@/components/shared/AnalysisIcon': { default: 'AnalysisIcon' },
    '@/components/shared/SoftBadge': { default: 'SoftBadge' },
    '@/components/shared/StatusNotice': { default: 'StatusNotice' },
    '@/components/shared/Skeleton': { SkeletonBlock: 'SkeletonBlock', SkeletonText: 'SkeletonText' },
    '@/components/shared/InfoHint': { default: 'InfoHint' },
    '@/components/icons': { default: 'Icon' },
    '@/hooks/useAccess': { useAccess: () => ({ isOwner: true }) },
    '@/hooks/usePolling': usePollingModule,
    '@/hooks/remoteState': { remoteState },
    '@/hooks/useToast': { useToast: () => ({
      error: (title, body) => toasts.push(['error', title, body]),
      success: (title, body) => toasts.push(['success', title, body]),
      info: (title, body) => toasts.push(['info', title, body]),
    }) },
    '@/lib/boundedReadRetry': boundedReadRetry,
    '@/lib/scoreHints': { SCORE_HINTS: {} },
    '@/lib/utils': { cn: (...xs) => xs.filter(Boolean).join(' ') },
    '@/lib/format': { fmtLocaleDate: () => 'd', fmtLocaleDateTime: () => 'dt' },
    './api': { catalystsContract: {
      latestFocusCycle: async () => { calls.latest += 1; return latest(calls.latest); },
      previousFocusCycle: async () => { calls.previous += 1; return previous ? previous(calls.previous) : Promise.reject(new TestApiError(404, 'none')); },
      triggerFocusCycle: async (...args) => { calls.trigger.push(args); return trigger(...args); },
      focusCycleJob: async (id) => { calls.poll.push(id); return poll(calls.poll.length); },
    } },
    './analysisErrorText': analysisErrorText,
    './bits': { ImpactValue: 'ImpactValue', Led: 'Led' },
    './ConfirmDialog': { default: 'ConfirmDialog' },
    '../../i18n/core.ts': i18n,
  }, {
    window: { setTimeout: clock.api.setTimeout, clearTimeout: clock.api.clearTimeout },
    Date: clock.Date,
  }).default;
  let props = { refreshToken: 0, onDataRefreshed: (options) => refreshed.push(options) };
  let read = () => null;
  return {
    clock,
    calls,
    toasts,
    refreshed,
    tree: () => read(),
    mount() { read = runner.mount(() => Card(props)); },
    setProps(next) { props = { ...props, ...next }; read = runner.mount(() => Card(props)); },
    rerender: () => runner.rerender(),
    unmount: () => runner.unmount(),
  };
}

function triggerButton(tree) {
  return findNode(tree, (node) => node.type === 'button' && /触发新周期|重试焦点周期|周期计算中/.test(textOf(node)));
}

async function startFocus(h) {
  const button = triggerButton(h.tree());
  assert.ok(button && !button.props.disabled, '触发按钮可点');
  button.props.onClick();
  const dialog = findNode(h.tree(), (node) => node.type === 'ConfirmDialog' && node.props.open);
  dialog.props.onConfirm();
  await settle();
}

test('FE-6 焦点周期状态读取失败按退避重试，不停表清任务，按钮保持禁用', async () => {
  let round = 0;
  const h = focusHarness({
    latest: () => cycle(),
    trigger: () => focusJob(),
    poll: () => {
      round += 1;
      if (round <= 2) throw new TestApiError(503, 'busy');
      if (round === 3) return focusJob({ status: 'in_progress' });
      return focusJob({ status: 'completed' });
    },
  });
  h.mount();
  await settle();
  await startFocus(h);
  assert.equal(h.refreshed[0]?.cacheCleared, true, '2-E：API 层已清缓存，页面只重读');
  await h.clock.advance(2_000);
  assert.equal(h.calls.poll.length, 1);
  assert.ok(!h.toasts.some((row) => row[0] === 'error'), '一次读取失败不报错停表');
  assert.ok(triggerButton(h.tree()).props.disabled, '任务仍在跟踪，按钮不可点');
  await h.clock.advance(5_000);
  assert.equal(h.calls.poll.length, 2);
  assert.match(textOf(h.tree()), /焦点周期状态暂时读不到，正在重试/);
  await h.clock.advance(9_999);
  assert.equal(h.calls.poll.length, 2, '第二次失败后退避到 10 秒');
  await h.clock.advance(1);
  assert.equal(h.calls.poll.length, 3);
  assert.doesNotMatch(textOf(h.tree()), /暂时读不到/);
  await h.clock.advance(2_000);
  assert.ok(h.toasts.some((row) => row[0] === 'success' && row[1] === '新焦点周期已生成'));
  h.unmount();
});

test('FE-6 页面加载时已有周期在跑：低频跟踪 latest、禁用触发，结束后刷新历史对照', async () => {
  let done = false;
  const h = focusHarness({
    latest: () => (done ? cycle({ status: 'completed' }) : cycle({ status: 'in_progress', dominantEvent: '—', summary: '' })),
    previous: () => cycle({ cycleId: `mfc_${'e'.repeat(32)}`, dominantEvent: '上一周期' }),
    trigger: () => focusJob(),
    poll: () => focusJob(),
  });
  h.mount();
  await settle();
  const button = triggerButton(h.tree());
  assert.ok(button.props.disabled);
  assert.match(textOf(button), /周期计算中/);
  const latestReads = h.calls.latest;
  const previousReads = h.calls.previous;
  await h.clock.advance(15_000);
  assert.ok(h.calls.latest > latestReads, '进行中按 15 秒跟踪');
  done = true;
  await h.clock.advance(15_000);
  assert.ok(!triggerButton(h.tree()).props.disabled, '周期结束后恢复可点');
  assert.ok(h.calls.previous > previousReads, '历史对照随之刷新');
  const reads = h.calls.latest;
  await h.clock.advance(60_000);
  assert.equal(h.calls.latest, reads, '结束后不再轮询');
  h.unmount();
});

test('FE-5 焦点周期终态按原因提示：预算受限与取消不再一律「计算失败」', async () => {
  for (const [final, kind, title, reason] of [
    [focusJob({ status: 'budget_blocked', errorCode: 'daily_token_limit_reached' }), 'error', '焦点周期没有执行', '今天的 AI 用量已用完，这次没有执行分析'],
    [focusJob({ status: 'cancelled' }), 'info', '焦点周期已取消', '可以重新触发计算'],
    [focusJob({ status: 'failed', errorCode: 'market_focus_payload_mismatch' }), 'error', '焦点周期计算失败', '热点数据已更新，请重新计算'],
  ]) {
    const h = focusHarness({ latest: () => cycle(), trigger: () => focusJob(), poll: () => final });
    h.mount();
    await settle();
    await startFocus(h);
    await h.clock.advance(2_000);
    assert.deepEqual(h.toasts.at(-1), [kind, title, reason]);
    h.unmount();
  }
});

test('FE-5 徽标与说明：预算受限显示中文，失败尝试写明原因', async () => {
  const h = focusHarness({
    latest: () => cycle({ status: 'budget_blocked', errorCode: 'daily_token_limit_reached' }),
    trigger: () => focusJob(),
    poll: () => focusJob(),
  });
  h.mount();
  await settle();
  const text = textOf(h.tree());
  assert.match(text, /额度已用完/);
  assert.match(text, /今天的 AI 用量已用完，这次没有执行分析/);
  assert.doesNotMatch(text, /budget_blocked/);
  assert.match(textOf(triggerButton(h.tree())), /重试焦点周期/);
  h.unmount();

  const h2 = focusHarness({
    latest: () => cycle({ latestAttempt: { cycleId: `mfc_${'f'.repeat(32)}`, status: 'cancelled', startedAt: '2026-09-25T00:00:00Z', errorCode: null } }),
    trigger: () => focusJob(),
    poll: () => focusJob(),
  });
  h2.mount();
  await settle();
  assert.match(textOf(h2.tree()), /最近一次更新已取消，当前展示上次成功结果/);
  h2.unmount();
});

test('FE-7 刷新失败保留旧数据并给出说明与重试；没有旧数据时给错误与重试，不说「暂无数据」', async () => {
  let fail = false;
  const h = focusHarness({
    latest: () => { if (fail) throw new TestApiError(503, 'down'); return cycle(); },
    trigger: () => focusJob(),
    poll: () => focusJob(),
  });
  h.mount();
  await settle();
  assert.match(textOf(h.tree()), /主导事件/);
  fail = true;
  h.setProps({ refreshToken: 1 });
  await settle();
  let text = textOf(h.tree());
  assert.match(text, /主导事件/, '旧数据继续显示');
  assert.match(text, /最新状态读取失败，显示上次结果/);
  assert.doesNotMatch(text, /暂无焦点周期数据/);
  const retry = findButton(h.tree(), '重试');
  assert.ok(retry);
  fail = false;
  const reads = h.calls.latest;
  retry.props.onClick();
  await settle();
  assert.equal(h.calls.latest, reads + 1);
  assert.doesNotMatch(textOf(h.tree()), /最新状态读取失败/);
  h.unmount();

  const h2 = focusHarness({ latest: () => { throw new TestApiError(503, 'down'); }, trigger: () => focusJob(), poll: () => focusJob() });
  h2.mount();
  await settle();
  text = textOf(h2.tree());
  assert.match(text, /焦点周期暂时读不到，可以稍后重试/);
  assert.doesNotMatch(text, /暂无焦点周期数据/);
  assert.ok(findButton(h2.tree(), '重试'));
  h2.unmount();
});

test('3-E 焦点卡的兜底与收尾定时器随卸载清理', async () => {
  const h = focusHarness({ latest: () => cycle(), trigger: () => focusJob({ cycleId: null }), poll: () => focusJob() });
  h.mount();
  await settle();
  await startFocus(h);
  assert.ok([...h.clock.timers.values()].some((row) => row.ms === 12_000), '无周期编号时登记 12 秒兜底');
  h.unmount();
  assert.ok(![...h.clock.timers.values()].some((row) => row.ms === 12_000), '卸载后兜底定时器已取消');

  const h2 = focusHarness({ latest: () => cycle(), trigger: () => focusJob(), poll: () => focusJob({ status: 'completed' }) });
  h2.mount();
  await settle();
  await startFocus(h2);
  await h2.clock.advance(2_000);
  assert.ok([...h2.clock.timers.values()].some((row) => row.ms === 1_200));
  h2.unmount();
  assert.ok(![...h2.clock.timers.values()].some((row) => row.ms === 1_200), '卸载后收尾定时器已取消');
});

/* ---------------- 页面：刷新只清一次缓存、补丁带时间戳 ---------------- */

test('2-E 页面刷新：写操作已清缓存时不再清第二次；页头刷新照常清', async () => {
  const runner = createReactStub();
  runner.React.useOptimistic = (value) => [value, () => {}];
  runner.React.startTransition = (fn) => fn();
  let clears = 0;
  const components = Object.fromEntries([
    '@/components/shared/PageHeader', '@/components/shared/Segmented', '@/components/icons',
    '@/components/catalysts/StatusHero', '@/components/catalysts/AnalysisProgressCard', '@/components/catalysts/HotspotsStrip',
    '@/components/catalysts/FocusCycleCard', '@/components/catalysts/ManagePanel', '@/components/catalysts/FilterBar',
    '@/components/catalysts/FeedPanel', '@/components/catalysts/StocksPanel', '@/components/catalysts/CalendarPanel',
    '@/components/catalysts/SourcesPanel', '@/components/catalysts/NewsDrawer',
  ].map((id) => [id, { default: id.split('/').pop() }]));
  const clock = fakeClock();
  const Page = compile('pages/Catalysts.tsx', {
    react: runner.React,
    'react/jsx-runtime': passthroughJsx,
    'react-router': { useSearchParams: () => [new URLSearchParams(''), () => {}] },
    ...components,
    '@/lib/format': { fmtTimeHHMMSS: () => 't' },
    '@/components/catalysts/filters': filters,
    '@/components/catalysts/api': { clearCatalystReadCache: () => { clears += 1; } },
    '@/components/catalysts/feedPatches': feedPatches,
    '../i18n/core.ts': i18n,
  }, { window: { setTimeout: clock.api.setTimeout, clearTimeout: clock.api.clearTimeout }, Date: clock.Date }).default;
  const read = runner.mount(() => Page());
  const focus = findNode(read(), (node) => node.type === 'FocusCycleCard');
  focus.props.onDataRefreshed({ cacheCleared: true });
  assert.equal(clears, 0);
  assert.equal(findNode(read(), (node) => node.type === 'FocusCycleCard').props.refreshToken, 1, '页面各区仍重新读取');
  findNode(read(), (node) => node.type === 'FocusCycleCard').props.onDataRefreshed();
  assert.equal(clears, 1);
  findNode(read(), (node) => node.type === 'button' && /刷新/.test(textOf(node))).props.onClick({ type: 'click' });
  assert.equal(clears, 2);

  const drawer = findNode(read(), (node) => node.type === 'NewsDrawer');
  drawer.props.onUpdate({ newsId: '1', analysisStatus: 'queued' });
  const patches = findNode(read(), (node) => node.type === 'FeedPanel').props.patches;
  assert.equal(patches['1'].item.newsId, '1');
  assert.equal(patches['1'].at, clock.now(), '补丁记下回写时刻');
  runner.unmount();
});

/* ---------------- FeedPanel：补丁与翻页 ---------------- */

function feedItem(newsId, analysisStatus, extra = {}) {
  return {
    newsId, source: 'Reuters', sourceCount: 1, title: newsId, titleZh: `新闻${newsId}`, summary: '', summaryZh: '摘要',
    url: '', publishedAt: '2026-09-25T00:00:00Z', fetchedAt: '', sourceTickers: [], isStale: false, themeIds: [],
    analysisStatus, analysis: null, analysisJobId: null, ...extra,
  };
}

function snapshotOf(items, nextCursor = null) {
  return { items, nextCursor, total: items.length, hiddenUnanalyzed: 0, pages: 1 };
}

function feedHarness() {
  let now = 1_000;
  const cache = new ResourceCache(undefined, () => now);
  const policy = { freshMs: 120_000, retainMs: 10 ** 9 };
  const runner = createReactStub();
  let key = 'feed:a';
  let feedImpl = async () => snapshotOf([]);
  const subscriptions = new Map();
  const watch = (k) => {
    if (!subscriptions.has(k)) subscriptions.set(k, cache.subscribe(k, policy, () => runner.rerender()));
  };
  const Panel = compile('components/catalysts/FeedPanel.tsx', {
    react: runner.React,
    'react/jsx-runtime': passthroughJsx,
    'framer-motion': motionStub,
    '@/components/shared/AnalysisIcon': { default: 'AnalysisIcon' },
    '@/api/client': { ApiError: TestApiError },
    '@/hooks/useShell': { useShell: () => ({ openTicker() {} }) },
    '@/components/shared/EmptyState': { default: 'EmptyState' },
    '@/components/shared/Skeleton': { SkeletonBlock: 'SkeletonBlock' },
    '@/components/icons': { default: 'Icon' },
    '@/components/shared/InfoHint': { default: 'InfoHint' },
    '@/components/shared/SoftBadge': { default: 'SoftBadge' },
    '@/lib/utils': { cn: (...xs) => xs.filter(Boolean).join(' ') },
    '@/lib/format': { fmtLocaleDate: () => 'd', fmtLocaleTime: () => 't', fmtRelative: () => 'r' },
    '@/lib/scoreHints': { SCORE_HINTS: {} },
    './api': { catalystsContract: { feed: (q) => feedImpl(q) } },
    './feedPrefetch': { DEFAULT_FEED_PAGE_SIZE: 12 },
    './useFeedResource': { useFeedResource: () => {
      watch(key);
      const snap = cache.snapshot(key, policy);
      return {
        ...snap, error: null, loading: snap.data === null, lastUpdatedAt: snap.validatedAt || null, key, enabled: true,
        refresh() {}, update: (change, expected) => cache.update(key, policy, change, expected),
      };
    } },
    './feedSnapshot': feedSnapshot,
    './feedPatches': feedPatches,
    './CatalystCacheStatus': { default: 'CatalystCacheStatus' },
    './cacheStatusProps': { cacheStatusProps },
    './filters': filters,
    './bits': {
      AnalysisStatusChip: 'AnalysisStatusChip', ClassificationChip: 'ClassificationChip', ConfidenceLabel: 'ConfidenceLabel',
      ImpactValue: 'ImpactValue', StaleChip: 'StaleChip', TickerChip: 'TickerChip',
    },
    '../../i18n/core.ts': i18n,
  }).default;
  let props = { filters: filters.DEFAULT_FILTERS, onOpenNews() {}, patches: {}, refreshToken: 0, onFeedResult() {}, onClearFilters() {} };
  let read = () => null;
  return {
    cache,
    policy,
    setNow: (value) => { now = value; },
    seed: (k, snap) => cache.ensure(k, policy, async () => snap, true),
    setKey: (k) => { key = k; },
    setFeed: (fn) => { feedImpl = fn; },
    rows: (k = key) => cache.snapshot(k, policy).data?.items ?? [],
    tree: () => read(),
    render(next = {}) { props = { ...props, ...next }; read = runner.mount(() => Panel(props)); },
    unmount() { runner.unmount(); for (const off of subscriptions.values()) off(); },
  };
}

test('FE-4 已完成分析的条目不会被旧补丁退回「未分析」：换条、切换筛选都不重套', async () => {
  const h = feedHarness();
  h.setNow(1_000);
  await h.seed('feed:a', snapshotOf([feedItem('X', 'completed'), feedItem('Y', 'pending')]));
  const stale = { X: { item: feedItem('X', 'pending'), at: 500 } };
  h.render({ patches: stale });
  await settle();
  assert.equal(h.rows().find((row) => row.newsId === 'X').analysisStatus, 'completed');

  // 打开另一条新闻 Y：只套 Y 的新补丁，X 的旧补丁不动。
  h.render({ patches: { ...stale, Y: { item: feedItem('Y', 'queued'), at: 2_000 } } });
  await settle();
  assert.equal(h.rows().find((row) => row.newsId === 'X').analysisStatus, 'completed');
  assert.equal(h.rows().find((row) => row.newsId === 'Y').analysisStatus, 'queued');

  // 切换筛选（新缓存键、较新的快照）：旧补丁一律不重套。
  h.setNow(3_000);
  await h.seed('feed:b', snapshotOf([feedItem('X', 'completed'), feedItem('Y', 'completed')]));
  h.setKey('feed:b');
  h.render({});
  await settle();
  assert.equal(h.rows('feed:b').find((row) => row.newsId === 'X').analysisStatus, 'completed');
  assert.equal(h.rows('feed:b').find((row) => row.newsId === 'Y').analysisStatus, 'completed');
  h.unmount();
});

test('FE-4 后台刷新在途时补丁不写入；刷新带回的服务端数据比补丁新就以它为准', async () => {
  const h = feedHarness();
  h.setNow(1_000);
  await h.seed('feed:a', snapshotOf([feedItem('X', 'pending')]));
  h.render({});
  await settle();
  const reload = deferred();
  h.setNow(2_000);
  const refreshing = h.cache.ensure('feed:a', h.policy, () => reload.promise, true);
  await settle();
  h.render({ patches: { X: { item: feedItem('X', 'queued'), at: 2_500 } } });
  await settle();
  assert.equal(h.rows()[0].analysisStatus, 'pending', '刷新在途时不写补丁');
  h.setNow(3_000);
  reload.resolve(snapshotOf([feedItem('X', 'completed')]));
  await refreshing;
  await settle();
  assert.equal(h.rows()[0].analysisStatus, 'completed', '刷新结果比补丁新');
  h.unmount();
});

test('FE-4 补丁集合有上限，比快照新的补丁才会套用', () => {
  let patches = {};
  for (let i = 0; i < feedPatches.MAX_NEWS_PATCHES + 5; i += 1) patches = feedPatches.addNewsPatch(patches, feedItem(String(i), 'queued'), i);
  assert.equal(Object.keys(patches).length, feedPatches.MAX_NEWS_PATCHES);
  assert.ok(!('0' in patches) && String(feedPatches.MAX_NEWS_PATCHES + 4) in patches, '淘汰最旧的补丁');
  const snap = snapshotOf([feedItem('A', 'completed')]);
  assert.equal(feedPatches.applyNewsPatches(snap, { A: { item: feedItem('A', 'pending'), at: 5 } }, 10), snap);
  const newer = feedPatches.applyNewsPatches(snap, { A: { item: feedItem('A', 'queued'), at: 20 } }, 10);
  assert.equal(newer.items[0].analysisStatus, 'queued');
});

test('1-H 翻页结果被刷新后的缓存拒收：提示列表已更新；后台刷新期间禁用加载更多', async () => {
  const h = feedHarness();
  h.setNow(1_000);
  await h.seed('feed:a', snapshotOf([feedItem('A', 'completed')], 'c1'));
  const page = deferred();
  h.setFeed(() => page.promise);
  h.render({});
  await settle();
  findButton(h.tree(), '加载更多').props.onClick();
  await settle();
  h.setNow(2_000);
  h.cache.invalidate();
  await h.cache.ensure('feed:a', h.policy, async () => snapshotOf([feedItem('A', 'completed'), feedItem('B', 'pending')], 'c9'));
  await settle();
  page.resolve({ items: [feedItem('C', 'pending')], nextCursor: null, total: 1, hiddenUnanalyzed: 0 });
  await settle();
  assert.match(textOf(h.tree()), /列表已更新，请再试一次/);
  assert.deepEqual(h.rows().map((row) => row.newsId), ['A', 'B'], '旧游标链的页不混进新快照');

  const reload = deferred();
  const refreshing = h.cache.ensure('feed:a', h.policy, () => reload.promise, true);
  await settle();
  assert.equal(findButton(h.tree(), '加载更多').props.disabled, true);
  reload.resolve(snapshotOf([feedItem('A', 'completed')], 'c2'));
  await refreshing;
  await settle();
  assert.equal(findButton(h.tree(), '加载更多').props.disabled, false);
  h.unmount();
});

/* ---------------- 分析进度卡 ---------------- */

test('1-G 分析进度持续失败按退避读取，Retry-After 更长时听服务端，成功即复位', async () => {
  let now = 0;
  let reads = 0;
  let fail = true;
  const error = new TestApiError(503, 'busy', { retryAfter: 20 });
  const read = createReadBackoff(async () => {
    reads += 1;
    if (fail) throw error;
    return 'ok';
  }, [5_000, 15_000], () => now);
  await assert.rejects(read(), (thrown) => thrown === error);
  assert.equal(reads, 1);
  now = 19_999;
  await assert.rejects(read(), (thrown) => thrown === error);
  assert.equal(reads, 1, '退避期内不发请求');
  now = 20_000;
  await assert.rejects(read());
  assert.equal(reads, 2);
  fail = false;
  now = 39_999;
  await assert.rejects(read());
  assert.equal(reads, 2);
  now = 40_000;
  assert.equal(await read(), 'ok');
  assert.equal(await read(), 'ok');
  assert.equal(reads, 4, '成功后恢复正常频率');
});

test('1-G 分析进度字段错误经 t() 翻译', () => {
  const payload = {
    status: 'active', scope: 'latest_submission_batch', batch_source: 'manual', total: -1,
  };
  assert.throws(() => normalizeNewsAnalysisProgress(payload), /新闻分析进度字段无效：total/);
  setLocale('en');
  try {
    assert.throws(() => normalizeNewsAnalysisProgress(payload), /Invalid news analysis progress field: total/);
  } finally {
    setLocale('zh');
  }
});

/* ---------------- resourceRuntime：推送流重连与合并提示 ---------------- */

function runtimeHarness() {
  const clock = fakeClock();
  const doc = fakeEventTarget({ visibilityState: 'visible' });
  const win = fakeEventTarget();
  const sources = [];
  class FakeEventSource {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.listeners = new Map();
      sources.push(this);
    }
    addEventListener(type, fn) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(fn);
    }
    close() { this.readyState = 2; this.closed = true; }
    emit(type, event = {}) { for (const fn of this.listeners.get(type) ?? []) fn(event); }
  }
  const counts = { clears: 0, ticks: 0 };
  const runtime = compile('components/catalysts/resourceRuntime.ts', {
    '@/api/client': { API_MODE: 'live' },
    '@/api/queryRegistry': { getQueryPrincipalGeneration: () => 0 },
    './api': { clearCatalystReadCache: () => { counts.clears += 1; } },
    './resourceCache': { ResourceCache: class { tick() { counts.ticks += 1; } invalidate() {} } },
    './resourcePersistence': { catalystPersistence: {} },
    './resourceSignals': { onCatalystReadsInvalidated: () => () => {} },
  }, {
    document: doc,
    window: win,
    EventSource: FakeEventSource,
    setTimeout: clock.api.setTimeout,
    clearTimeout: clock.api.clearTimeout,
    setInterval: clock.api.setInterval,
    clearInterval: clock.api.clearInterval,
    Date: clock.Date,
  });
  const update = (revision) => ({ data: JSON.stringify({ revision }) });
  return { clock, doc, sources, counts, runtime, update };
}

test('2-C 推送流被服务端拒绝（非 200 关流）后按退避重连；网络闪断交给浏览器', async () => {
  const h = runtimeHarness();
  const release = h.runtime.retainCatalystRuntime();
  assert.equal(h.sources.length, 1);
  h.sources[0].emit('open');
  h.sources[0].readyState = 0;
  h.sources[0].emit('error');
  await h.clock.advance(60_000);
  assert.equal(h.sources.length, 1, '浏览器仍在重连时不另起连接');
  h.sources[0].readyState = 2;
  h.sources[0].emit('error');
  await h.clock.advance(4_999);
  assert.equal(h.sources.length, 1);
  await h.clock.advance(1);
  assert.equal(h.sources.length, 2, '5 秒后重连');
  h.sources[1].readyState = 2;
  h.sources[1].emit('error');
  await h.clock.advance(14_999);
  assert.equal(h.sources.length, 2);
  await h.clock.advance(1);
  assert.equal(h.sources.length, 3, '再次失败退避到 15 秒');
  release();
  await h.clock.advance(1_000);
});

test('2-C 合并窗口到期时页面隐藏：变更提示留到回到前台再生效', async () => {
  const h = runtimeHarness();
  const release = h.runtime.retainCatalystRuntime();
  const [stream] = h.sources;
  stream.emit('catalyst-update', h.update('a'.repeat(24)));
  stream.emit('catalyst-update', h.update('b'.repeat(24)));
  h.doc.visibilityState = 'hidden';
  h.doc.dispatch('visibilitychange');
  await h.clock.advance(30_000);
  assert.equal(h.counts.clears, 0, '隐藏时不刷新');
  h.doc.visibilityState = 'visible';
  h.doc.dispatch('visibilitychange');
  assert.equal(h.counts.clears, 1, '回到前台补上被合并的提示');
  release();
  await h.clock.advance(1_000);
});

/* ---------------- 财报卡 ---------------- */

function impactHarness({ reportAnalysis, requestReportAnalysis }) {
  const clock = fakeClock();
  const runner = createReactStub();
  const toasts = [];
  const analyzed = [];
  const reads = [];
  const Card = compile('components/earnings/ImpactCard.tsx', {
    react: runner.React,
    'react/jsx-runtime': passthroughJsx,
    'framer-motion': motionStub,
    '@/api/client': { ApiError: TestApiError },
    '@/api/modules/earnings': { earningsApi: {
      reportAnalysis: (...args) => { reads.push(args); return reportAnalysis(reads.length); },
      requestReportAnalysis: (...args) => requestReportAnalysis(...args),
    } },
    '@/hooks/useAccess': { useAccess: () => ({ isOwner: true, aiEnabled: true, aiAvailable: true, aiPending: false }) },
    '@/hooks/useToast': { useToast: () => ({
      error: (title, body) => toasts.push(['error', title, body]),
      success: (title, body) => toasts.push(['success', title, body]),
      info: (title, body) => toasts.push(['info', title, body]),
    }) },
    '@/hooks/useShell': { useShell: () => ({ openTicker() {} }) },
    '@/lib/utils': { cn: (...xs) => xs.filter(Boolean).join(' ') },
    './types': { exNum: () => null },
    '@/components/shared/AnalysisIcon': { default: 'AnalysisIcon' },
    '@/components/shared/SoftBadge': { default: 'SoftBadge' },
    '@/components/icons': { default: 'Icon' },
    '@/components/shared/SourceNote': { default: 'SourceNote' },
    './PulseDot': { default: 'PulseDot' },
    '@/components/shared/Skeleton': { SkeletonText: 'SkeletonText' },
    '../../i18n/core.ts': i18n,
  }, {
    window: { setTimeout: clock.api.setTimeout, clearTimeout: clock.api.clearTimeout },
    document: { visibilityState: 'visible' },
    Date: clock.Date,
  }).default;
  const props = { ticker: 'NVDA', row: { date: '2026-09-24' }, calendarRevision: 1, onAnalyzed: (ticker, value) => analyzed.push([ticker, value]) };
  const read = runner.mount(() => Card(props));
  return { clock, toasts, analyzed, reads, tree: () => read(), unmount: () => runner.unmount() };
}

const reportState = (partial = {}) => ({
  status: 'queued', errorCode: null, retryAfterSeconds: null, locked: false, final: false, finalizationInProgress: false, result: null, ...partial,
});
const impactResult = { outputLanguage: 'zh-CN', ticker: 'NVDA', summary: '财报摘要', expectation: '预期', impacted: [] };

test('FE-8 财报卡 5 分钟停表后给重新查询按钮，不再说「会自动显示」', async () => {
  const h = impactHarness({ reportAnalysis: async () => reportState({ status: 'in_progress' }), requestReportAnalysis: async () => reportState() });
  await h.clock.advance(0);
  assert.match(textOf(h.tree()), /分析完成后会自动显示，不用刷新页面。/);
  await h.clock.advance(6 * 60_000);
  let text = textOf(h.tree());
  assert.match(text, /自动查询已暂停，分析可能仍在进行/);
  assert.doesNotMatch(text, /分析完成后会自动显示/);
  const reads = h.reads.length;
  await h.clock.advance(60_000);
  assert.equal(h.reads.length, reads, '停表后不再自动查');
  findButton(h.tree(), '重新查询').props.onClick();
  await h.clock.advance(2_000);
  assert.equal(h.reads.length, reads + 1, '手动重新查询后恢复轮询');
  text = textOf(h.tree());
  assert.doesNotMatch(text, /自动查询已暂停/);
  h.unmount();
});

test('4-C 轮询中一次读取失败：保持「正在分析」并说明在自动重试，不切到「数据暂不可用」', async () => {
  const h = impactHarness({
    reportAnalysis: async (round) => {
      if (round === 2) throw new TestApiError(503, 'down');
      if (round >= 4) return reportState({ status: 'completed', result: impactResult });
      return reportState({ status: 'in_progress' });
    },
    requestReportAnalysis: async () => reportState(),
  });
  await h.clock.advance(0);
  await h.clock.advance(2_000);
  let text = textOf(h.tree());
  assert.match(text, /正在分析 ·/);
  assert.match(text, /暂时读不到最新状态，正在自动重试/);
  assert.doesNotMatch(text, /数据暂不可用|服务暂时不可用|稍后刷新再试/);
  await h.clock.advance(3_000);
  assert.doesNotMatch(textOf(h.tree()), /暂时读不到最新状态/);
  await h.clock.advance(5_000);
  assert.ok(h.toasts.some((row) => row[0] === 'success'));
  h.unmount();
});

test('4-D 卸载后在途读取回来：不回调父组件，也不弹提示', async () => {
  const pending = deferred();
  const h = impactHarness({
    reportAnalysis: (round) => (round === 1 ? reportState({ status: 'in_progress' }) : pending.promise),
    requestReportAnalysis: async () => reportState(),
  });
  await h.clock.advance(0);
  assert.equal(h.analyzed.length, 1);
  await h.clock.advance(2_000);
  h.unmount();
  pending.resolve(reportState({ status: 'completed', result: impactResult }));
  await settle();
  assert.equal(h.analyzed.length, 1, '卸载后不得回调父组件');
  assert.equal(h.toasts.length, 0, '卸载后不得弹提示');
});

test('4-F 财报提交 429 按业务码区分文案', async () => {
  for (const [bizCode, title] of [
    ['ai_job_queue_full', 'AI 任务队列已满'],
    ['earnings_analysis_rate_limited', '财报分析触发过于频繁'],
    ['rate_limited', '请求过于频繁'],
  ]) {
    const h = impactHarness({
      reportAnalysis: async () => { throw new TestApiError(409, 'required', { bizCode: 'analysis_required' }); },
      requestReportAnalysis: async () => { throw new TestApiError(429, 'limited', { bizCode, retryAfter: 42 }); },
    });
    await h.clock.advance(0);
    findButton(h.tree(), '生成分析').props.onClick();
    const confirmButton = findNode(h.tree(), (node) => node.type === 'button' && textOf(node).trim() === '生成分析' && node.props.disabled === false);
    confirmButton.props.onClick();
    await settle();
    assert.deepEqual(h.toasts.at(-1), ['error', title, '约 42s 后重试']);
    h.unmount();
  }
});

test('4-F 取消的财报任务说「已取消」，不显示「未知原因」或排队残留码', async () => {
  const h = impactHarness({
    reportAnalysis: async () => reportState({ status: 'cancelled', errorCode: 'global_concurrency_limit' }),
    requestReportAnalysis: async () => reportState(),
  });
  await h.clock.advance(0);
  const text = textOf(h.tree());
  assert.match(text, /分析任务已取消/);
  assert.doesNotMatch(text, /未知原因|同时进行的分析过多|分析任务失败/);
  assert.ok(findButton(h.tree(), '重试'));
  h.unmount();
});

test('财报卡：进行中带认不出的码不说「没有完成」；终态残留排队码按通用失败说明', async () => {
  const running = impactHarness({ reportAnalysis: async () => reportState({ status: 'in_progress', errorCode: 'provider_poll_deferred' }), requestReportAnalysis: async () => reportState() });
  await running.clock.advance(0);
  assert.doesNotMatch(textOf(running.tree()), /这次分析没有完成/);
  running.unmount();
  const failed = impactHarness({ reportAnalysis: async () => reportState({ status: 'failed', errorCode: 'global_concurrency_limit' }), requestReportAnalysis: async () => reportState() });
  await failed.clock.advance(0);
  const text = textOf(failed.tree());
  assert.match(text, /这次分析没有完成/);
  assert.doesNotMatch(text, /同时进行的分析过多/);
  failed.unmount();
});

test('visual-tests 桩的任务响应改成真实形状：不再带 news_id 与 progress', () => {
  const spec = fs.readFileSync(path.resolve(here, '../visual-tests/frontend-fix-local.spec.mjs'), 'utf8');
  const start = spec.indexOf("json: { job_id: 'job-local'");
  const stub = spec.slice(start, spec.indexOf('} });', start));
  assert.match(stub, /job_type: 'news_impact'/);
  assert.match(stub, /submission_source: 'manual'/);
  assert.doesNotMatch(stub, /news_id|progress:/);
});
