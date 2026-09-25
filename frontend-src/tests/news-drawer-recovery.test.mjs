import { deferred } from './helpers/deferred.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { createReactStub } from './helpers/react-hooks.mjs';
import {
  ReadAttemptAborted,
  boundedReadRetryDelayMs,
  createCancellableSleep,
  isAutoRetryableReadError,
  runBoundedRead,
  shouldApplyRecoveryJob,
} from '../src/lib/boundedReadRetry.ts';
import * as analysisErrorText from '../src/components/catalysts/analysisErrorText.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');
const source = fs.readFileSync(path.join(root, 'components/catalysts/NewsDrawer.tsx'), 'utf8');

async function settle() {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

function item(partial) {
  return {
    newsId: '9600',
    source: 'Reuters',
    sourceCount: 1,
    title: 'Item',
    titleZh: '第9600条快讯',
    summary: 's',
    summaryZh: '摘要',
    url: 'https://example.test/n',
    publishedAt: '2026-09-13T00:00:00Z',
    fetchedAt: '2026-09-13T00:00:00Z',
    sourceTickers: ['AAPL'],
    isStale: false,
    themeIds: [],
    analysisStatus: 'pending',
    analysis: null,
    analysisJobId: null,
    ...partial,
  };
}

/* 任务查询接口（AIJobPublic）归一后的真实形状：后端不给 news_id 与 progress，
   newsId 为空串、progress 为 null。只有提交接口会由 api 层补上 newsId。 */
function job(partial) {
  return {
    jobId: 'job-1',
    newsId: '',
    status: 'in_progress',
    progress: null,
    submittedAt: '2026-09-13T00:00:00Z',
    updatedAt: '2026-09-13T00:00:01Z',
    error: null,
    cancellable: true,
    ...partial,
  };
}

function harness(DateImpl = Date) {
  const runner = createReactStub();
  const timers = new Map();
  let nextTimer = 0;
  const newsCalls = [];
  const jobCalls = [];
  const createCalls = [];
  const updates = [];
  const toasts = [];
  let sessionGen = 0;
  let newsImpl = async () => item({ analysisStatus: 'queued', analysisJobId: 'job-1' });
  let jobImpl = async () => job({ status: 'in_progress' });
  let createImpl = async () => { throw new Error('recovery must not POST createAnalysisJob'); };
  const env = {
    window: {
      setTimeout(fn, ms = 0) {
        const id = ++nextTimer;
        timers.set(id, { fn, ms });
        return id;
      },
      clearTimeout(id) { timers.delete(id); },
    },
    Date: DateImpl,
  };
  env.window.window = env.window;
  const passthrough = (type, props) => ({ type, props });
  const jsx = { jsx: passthrough, jsxs: passthrough, Fragment: 'fragment' };
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require(id) {
      if (id === 'react') return runner.React;
      if (id === 'react/jsx-runtime') return jsx;
      if (id === 'framer-motion') {
        const motion = new Proxy({}, { get: () => passthrough });
        return { AnimatePresence: passthrough, motion };
      }
      if (id === '@/components/Drawer') return { default: passthrough };
      if (id === '@/components/icons') return { default: passthrough };
      if (id === '@/components/shared/AnalysisIcon') return { default: passthrough };
      if (id === '@/components/shared/Skeleton') return { SkeletonBlock: passthrough, SkeletonText: passthrough };
      if (id === '@/components/shared/SoftBadge') return { default: passthrough };
      if (id === '@/hooks/useAccess') return { useAccess: () => ({ isOwner: true, loading: false }) };
      if (id === '@/hooks/useToast') return { useToast: () => ({
        error: (title, body) => toasts.push(['error', title, body]),
        success: (title) => toasts.push(['success', title]),
        info: (title) => toasts.push(['info', title]),
      }) };
      if (id === '@/hooks/useShell') return { useShell: () => ({ openTicker() {} }) };
      if (id === '@/lib/format') return { fmtLocaleDateTime: () => 't', fmtLocaleTime: () => 't' };
      if (id === '@/api/queryRegistry') return { getQueryPrincipalGeneration: () => sessionGen };
      if (id === '@/lib/boundedReadRetry') return {
        ReadAttemptAborted,
        boundedReadRetryDelayMs,
        createCancellableSleep,
        isAutoRetryableReadError,
        runBoundedRead,
        shouldApplyRecoveryJob,
      };
      if (id === './api') return {
        catalystsContract: {
          news(id) { newsCalls.push(id); return newsImpl(id); },
          analysisJob(id) { jobCalls.push(id); return jobImpl(id); },
          createAnalysisJob(id, force) {
            createCalls.push([id, force]);
            return createImpl(id, force);
          },
        },
      };
      if (id === './bits') return {
        AnalysisStatusChip: passthrough, ClassificationChip: passthrough, ConfidenceLabel: passthrough,
        ImpactValue: passthrough, Led: passthrough, StaleChip: passthrough, TickerChip: passthrough,
      };
      if (id === './ConfirmDialog') return { default: passthrough };
      if (id === './analysisErrorText') return analysisErrorText;
      if (id === '../../i18n/core.ts') return { t: (text) => text };
      throw new Error(`Unexpected import ${id}`);
    },
    Date,
    ...env,
  });
  const NewsDrawer = module.exports.default;
  const fireDue = async (ms) => {
    const due = [...timers.entries()].filter(([, row]) => row.ms <= ms);
    for (const [id, row] of due) {
      timers.delete(id);
      row.fn();
      await settle();
    }
  };
  let treeOf = () => null;
  return {
    newsCalls,
    jobCalls,
    createCalls,
    updates,
    toasts,
    timers,
    bumpSession: () => { sessionGen += 1; },
    setNews: (fn) => { newsImpl = fn; },
    setJob: (fn) => { jobImpl = fn; },
    setCreate: (fn) => { createImpl = fn; },
    fireDue,
    unmount: () => runner.unmount(),
    tree: () => treeOf(),
    render(props) {
      treeOf = runner.mount(
        () => NewsDrawer({
          onClose() {},
          onUpdate: (row) => updates.push(row),
          ...props,
        }),
      );
    },
  };
}

function collectText(node, out = []) {
  if (node == null) return out;
  if (typeof node === 'string' || typeof node === 'number') {
    out.push(String(node));
    return out;
  }
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, out);
    return out;
  }
  if (typeof node === 'object' && node.props) collectText(node.props.children, out);
  return out;
}

function completedItem(partial = {}) {
  return item({ analysisStatus: 'completed', analysisJobId: 'job-1', analysis: {
    classification: 'bullish', confidence: 0.8, headlineSummary: '完整分析', causalSummary: '分析原因',
    trustedStockImpacts: [], model: 'm', generatedAt: '2026-09-13T00:00:02Z',
  }, ...partial });
}

function findNode(node, predicate) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = findNode(child, predicate);
      if (found) return found;
    }
    return null;
  }
  if (predicate(node)) return node;
  return findNode(node.props?.children, predicate);
}

function findButton(node, label) {
  return findNode(node, (row) => row.type === 'button' && collectText(row).join('') === label);
}

test('持续轮询失败会提示，并在恢复后清除提示', async () => {
  const h = harness();
  let calls = 0;
  h.setJob(async () => {
    calls += 1;
    if (calls === 2 || calls === 3) throw new Error('status offline');
    return job({ status: 'in_progress' });
  });
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  await h.fireDue(2000);
  assert.doesNotMatch(collectText(h.tree()).join(' '), /任务状态暂时读不到/);
  await h.fireDue(5000);
  assert.match(collectText(h.tree()).join(' '), /任务状态暂时读不到/);
  // 第二次失败后本地退避升到 10 秒。
  await h.fireDue(10_000);
  assert.doesNotMatch(collectText(h.tree()).join(' '), /任务状态暂时读不到/);
  h.unmount();
});

test('轮询确认任务记录不存在后停止重试并显示原因', async () => {
  const h = harness();
  let calls = 0;
  h.setJob(async () => {
    if (++calls === 1) return job({ status: 'in_progress' });
    throw Object.assign(new Error('gone'), { code: 404 });
  });
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  await h.fireDue(2000);
  assert.match(collectText(h.tree()).join(' '), /任务记录已不存在/);
  await h.fireDue(5000);
  assert.equal(calls, 2);
  h.unmount();
});

test('自动查询超时后可手动重新查询同一任务', async () => {
  let now = 0;
  class TestDate extends Date { static now() { return now; } }
  const h = harness(TestDate);
  h.setJob(async () => job({ status: 'in_progress' }));
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  await h.fireDue(2000);
  now = 5 * 60_000 + 1;
  await h.fireDue(3000);
  assert.match(collectText(h.tree()).join(' '), /自动查询已暂停/);
  const retry = findButton(h.tree(), '重试');
  assert.ok(retry);
  const before = h.jobCalls.length;
  retry.props.onClick();
  await settle();
  await h.fireDue(2000);
  assert.equal(h.jobCalls.length, before + 1);
  assert.doesNotMatch(collectText(h.tree()).join(' '), /自动查询已暂停/);
  h.unmount();
});

for (const terminalPath of ['recovery', 'poll']) {
  test(`${terminalPath} 终态详情先返回后，迟到的初始 queued 不得覆盖已完成分析`, async () => {
    const h = harness();
    const initial = deferred();
    const terminal = deferred();
    let newsRound = 0;
    let jobRound = 0;
    h.setNews(() => ++newsRound === 1 ? initial.promise : terminal.promise);
    h.setJob(async () => job({
      status: terminalPath === 'poll' && ++jobRound === 1 ? 'in_progress' : 'completed',
    }));
    h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
    await settle();
    if (terminalPath === 'poll') await h.fireDue(2000);
    assert.equal(h.newsCalls.length, 2);
    terminal.resolve(completedItem());
    await settle();
    assert.match(collectText(h.tree()).join(' '), /完整分析/);
    initial.resolve(item({ analysisStatus: 'queued', analysisJobId: 'job-1' }));
    await settle();
    await h.fireDue(600);
    assert.equal(h.updates.at(-1).analysisStatus, 'completed');
    assert.match(collectText(h.tree()).join(' '), /完整分析/);
    assert.equal(h.newsCalls.length, 2);
    h.unmount();
  });
}

test('较新终态详情尚未返回时，旧详情也不能变更状态或中断终态读取', async () => {
  const h = harness();
  const initial = deferred();
  const terminal = deferred();
  let newsRound = 0;
  h.setNews(() => ++newsRound === 1 ? initial.promise : terminal.promise);
  h.setJob(async () => job({ status: 'completed' }));
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  assert.equal(h.newsCalls.length, 2);
  initial.resolve(item({ analysisStatus: 'in_progress', analysisJobId: 'job-1' }));
  await settle();
  assert.equal(h.updates.length, 0, '已有更新的详情请求时，旧响应不得回写列表');
  terminal.resolve(completedItem());
  await settle();
  assert.match(collectText(h.tree()).join(' '), /完整分析/);
  h.unmount();
});

test('终态详情成功后，旧初始请求失败不得再显示详情错误', async () => {
  const h = harness();
  const initial = deferred();
  let newsRound = 0;
  h.setNews(() => ++newsRound === 1 ? initial.promise : Promise.resolve(completedItem()));
  h.setJob(async () => job({ status: 'completed' }));
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  initial.reject(Object.assign(new Error('old failure'), { code: 404 }));
  await settle();
  assert.match(collectText(h.tree()).join(' '), /完整分析/);
  assert.doesNotMatch(collectText(h.tree()).join(' '), /详情更新失败|重试/);
  h.unmount();
});

test('手动重试可接收新任务 B，并使在途任务 A 的终态详情失效', async () => {
  const h = harness();
  const terminal = deferred();
  let newsRound = 0;
  let jobRound = 0;
  h.setNews(async () => {
    newsRound += 1;
    if (newsRound === 1) throw Object.assign(new Error('temporary'), { code: 404 });
    if (newsRound === 2) return terminal.promise;
    return item({ analysisStatus: 'queued', analysisJobId: 'job-B' });
  });
  h.setJob(async (id) => job({ jobId: id,
    status: id === 'job-1' && ++jobRound > 1 ? 'completed' : 'in_progress' }));
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  await h.fireDue(2000);
  assert.equal(h.newsCalls.length, 2);
  const retry = findButton(h.tree(), '重试');
  assert.ok(retry, '初始读取失败后必须保留重试入口');
  retry.props.onClick();
  await settle();
  assert.equal(h.updates.at(-1).analysisJobId, 'job-B');
  terminal.resolve(completedItem());
  await settle();
  assert.equal(h.updates.at(-1).analysisJobId, 'job-B');
  assert.equal(h.updates.at(-1).analysisStatus, 'queued');
  assert.ok(h.jobCalls.includes('job-B'));
  h.unmount();
});

test('成功提交新任务 B 后，在途初始详情中的旧 completed A 不得覆盖它', async () => {
  const h = harness();
  const initial = deferred();
  h.setNews(() => initial.promise);
  h.setJob(async (id) => job({ jobId: id, status: 'queued' }));
  h.setCreate(async () => job({ jobId: 'job-B', newsId: '9600', status: 'queued' }));
  h.render({ newsId: '9600', seed: completedItem() });
  await settle();
  const start = findButton(h.tree(), '重新分析（强制）');
  assert.ok(start);
  start.props.onClick();
  const confirm = findNode(h.tree(), (node) => node.props?.open && node.props?.confirmLabel === '重新分析');
  assert.ok(confirm);
  confirm.props.onConfirm();
  await settle();
  assert.deepEqual(h.createCalls, [['9600', true]]);
  assert.equal(h.updates.at(-1).analysisJobId, 'job-B');
  initial.resolve(completedItem());
  await settle();
  assert.equal(h.updates.at(-1).analysisJobId, 'job-B');
  assert.equal(h.updates.at(-1).analysisStatus, 'queued');
  h.unmount();
});

test('关闭并重开同一新闻后，上一轮轮询的终态详情不得写回', async () => {
  const h = harness();
  const terminal = deferred();
  let newsRound = 0;
  let jobRound = 0;
  h.setNews(async () => {
    newsRound += 1;
    if (newsRound === 1) return item({ analysisStatus: 'queued', analysisJobId: 'job-1' });
    if (newsRound === 2) return terminal.promise;
    return item({ titleZh: '重开新闻的新详情' });
  });
  h.setJob(async () => job({ status: ++jobRound === 1 ? 'in_progress' : 'completed' }));
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  await h.fireDue(2000);
  assert.equal(h.newsCalls.length, 2);
  h.render({ newsId: null });
  await settle();
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h.updates.at(-1).titleZh, '重开新闻的新详情');
  const updateCount = h.updates.length;
  terminal.resolve(completedItem());
  await settle();
  assert.equal(h.updates.length, updateCount);
  assert.equal(h.updates.at(-1).titleZh, '重开新闻的新详情');
  h.unmount();
});

test('关闭抽屉立即取消轮询终态详情的重试等待', async () => {
  const h = harness();
  let newsRound = 0;
  let jobRound = 0;
  h.setNews(async () => {
    if (++newsRound === 1) return item({ analysisStatus: 'queued', analysisJobId: 'job-1' });
    // Retry-After 在自动等待上限内才会真的睡；超过上限的情况见 429/30s 用例。
    throw Object.assign(new Error('busy'), { code: 503, retryAfter: 5 });
  });
  h.setJob(async () => job({ status: ++jobRound === 1 ? 'in_progress' : 'completed' }));
  h.render({ newsId: '9600', seed: item() });
  await settle();
  await h.fireDue(2000);
  assert.ok([...h.timers.values()].some((row) => row.ms === 5_000));
  h.render({ newsId: null });
  await settle();
  assert.ok(![...h.timers.values()].some((row) => row.ms === 5_000));
  await h.fireDue(5_000);
  assert.equal(h.newsCalls.length, 2);
  h.unmount();
});

test('seed 无任务时等详情带回 queued 才开始轮询并走到完成', async () => {
  const h = harness();
  const news = deferred();
  const firstJob = deferred();
  const pollJob = deferred();
  const refresh = deferred();
  let newsRound = 0;
  h.setNews(async () => {
    newsRound += 1;
    if (newsRound === 1) return news.promise;
    return refresh.promise;
  });
  let jobRound = 0;
  h.setJob(async () => {
    jobRound += 1;
    return jobRound === 1 ? firstJob.promise : pollJob.promise;
  });
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h.newsCalls.length, 1);
  assert.equal(h.jobCalls.length, 0, 'seed 没有 job id 时不得恢复轮询');
  news.resolve(item({ analysisStatus: 'queued', analysisJobId: 'job-1' }));
  await settle();
  assert.equal(h.jobCalls.length, 1);
  firstJob.resolve(job({ status: 'in_progress' }));
  await settle();
  assert.equal(h.jobCalls.length, 1, '恢复成功后等退避再打下一轮');
  await h.fireDue(2000);
  assert.equal(h.jobCalls.length, 2);
  pollJob.resolve(job({ status: 'completed' }));
  await settle();
  refresh.resolve(item({ analysisStatus: 'completed', analysisJobId: 'job-1', analysis: {
    classification: 'bullish', confidence: 0.8, headlineSummary: '好', causalSummary: '因为',
    trustedStockImpacts: [], model: 'm', generatedAt: '2026-09-13T00:00:02Z',
  } }));
  await settle();
  assert.equal(h.updates.at(-1).analysisStatus, 'completed');
  assert.ok(h.toasts.some((row) => row[0] === 'success'));
  h.unmount();
});

test('同一条新闻换成新任务 id 会重新恢复，关抽屉后迟到响应不写回', async () => {
  const h = harness();
  const first = deferred();
  const second = deferred();
  h.setNews(async () => item({ analysisStatus: 'queued', analysisJobId: 'job-1' }));
  h.setJob(async (id) => (id === 'job-2' ? second.promise : first.promise));
  h.render({ newsId: '9600', seed: item() });
  await settle();
  first.resolve(job({ jobId: 'job-1', status: 'in_progress' }));
  await settle();
  h.render({ newsId: null, seed: item() });
  await settle();
  h.setNews(async () => item({ analysisStatus: 'queued', analysisJobId: 'job-2' }));
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-2' }) });
  await settle();
  assert.ok(h.jobCalls.includes('job-2'));
  h.render({ newsId: null, seed: item({ analysisStatus: 'queued', analysisJobId: 'job-2' }) });
  await settle();
  const updatesBeforeLate = h.updates.length;
  second.resolve(job({ jobId: 'job-2', status: 'in_progress' }));
  await settle();
  assert.equal(h.updates.length, updatesBeforeLate, '关抽屉后迟到的 job 不得写回');
  h.unmount();
});

test('详情已是完成态时不再轮询；瞬时失败后重试能恢复任务', async () => {
  const h = harness();
  h.setNews(async () => item({
    analysisStatus: 'completed',
    analysisJobId: 'job-done',
    analysis: {
      classification: 'neutral', confidence: 0.1, headlineSummary: '完', causalSummary: '完',
      trustedStockImpacts: [], model: 'm', generatedAt: '2026-09-13T00:00:02Z',
    },
  }));
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h.jobCalls.length, 0);

  const h2 = harness();
  let newsRound = 0;
  const recovered = deferred();
  h2.setNews(async () => {
    newsRound += 1;
    if (newsRound === 1) throw new Error('429');
    return recovered.promise;
  });
  h2.setJob(async () => job({ status: 'queued' }));
  h2.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h2.jobCalls.length, 0);
  await h2.fireDue(1500);
  recovered.resolve(item({ analysisStatus: 'in_progress', analysisJobId: 'job-retry' }));
  await settle();
  assert.deepEqual(h2.jobCalls, ['job-retry']);
  h.unmount();
  h2.unmount();
});

test('抽屉保持打开时，seed 的旧任务 A 迟到不得替换已恢复的新任务 B', async () => {
  const h = harness();
  const news = deferred();
  const jobA = deferred();
  const jobB = deferred();
  h.setNews(async () => news.promise);
  h.setJob(async (id) => (id === 'job-B' ? jobB.promise : jobA.promise));
  h.render({
    newsId: '9600',
    seed: item({ analysisStatus: 'queued', analysisJobId: 'job-A' }),
  });
  await settle();
  assert.deepEqual(h.jobCalls, ['job-A']);
  news.resolve(item({ analysisStatus: 'in_progress', analysisJobId: 'job-B' }));
  await settle();
  assert.ok(h.jobCalls.includes('job-B'));
  jobB.resolve(job({ jobId: 'job-B', status: 'in_progress' }));
  await settle();
  await h.fireDue(2000);
  const afterB = h.jobCalls.filter((id) => id === 'job-B').length;
  jobA.resolve(job({ jobId: 'job-A', status: 'in_progress' }));
  await settle();
  await h.fireDue(2000);
  assert.equal(h.createCalls.length, 0);
  assert.ok(h.jobCalls.filter((id) => id === 'job-B').length >= afterB);
  assert.ok(!h.jobCalls.slice(h.jobCalls.lastIndexOf('job-B') + 1).includes('job-A'), '迟到的 A 不得开始轮询');
  h.unmount();
});

test('首次 analysisJob 503 对同一任务 ID 有界重试，不 POST 创建', async () => {
  const h = harness();
  let jobRound = 0;
  h.setNews(async () => item({ analysisStatus: 'queued', analysisJobId: 'job-1' }));
  h.setJob(async (id) => {
    jobRound += 1;
    if (jobRound === 1) {
      const error = new Error('busy');
      error.code = 503;
      error.retryable = true;
      throw error;
    }
    return job({ jobId: id, status: 'in_progress' });
  });
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  assert.equal(h.jobCalls.length, 1);
  assert.equal(h.createCalls.length, 0);
  await h.fireDue(1500);
  assert.deepEqual(h.jobCalls, ['job-1', 'job-1']);
  assert.equal(h.createCalls.length, 0);
  h.unmount();
});

test('恢复已 completed 后详情失败必须可见且可重试', async () => {
  const h = harness();
  let newsRound = 0;
  h.setNews(async () => {
    newsRound += 1;
    if (newsRound === 1) return item({ analysisStatus: 'queued', analysisJobId: 'job-1' });
    const error = new Error('gone');
    error.code = 503;
    error.retryable = true;
    throw error;
  });
  h.setJob(async () => job({ jobId: 'job-1', status: 'completed' }));
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  await h.fireDue(1500);
  await h.fireDue(3000);
  await settle();
  const text = collectText(h.tree()).join(' ');
  assert.match(text, /第9600条快讯/);
  assert.match(text, /详情更新失败/);
  assert.match(text, /重试/);
  h.unmount();
});

test('429 + Retry-After 30 秒超过自动等待上限：不静默等，立即给出详情更新失败与重试', async () => {
  const h = harness();
  let blocked = true;
  h.setNews(async () => {
    if (!blocked) return item();
    const error = new Error('slow');
    error.code = 429;
    error.retryAfter = 30;
    error.retryable = true;
    throw error;
  });
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h.newsCalls.length, 1);
  const text = collectText(h.tree()).join(' ');
  assert.match(text, /第9600条快讯/);
  assert.match(text, /详情更新失败/);
  const retry = findButton(h.tree(), '重试');
  assert.ok(retry, '超过上限必须立刻给手动重试');
  await h.fireDue(30_000);
  assert.equal(h.newsCalls.length, 1, '不得在 Retry-After 到期后自动重发');
  blocked = false;
  retry.props.onClick();
  await settle();
  assert.equal(h.newsCalls.length, 2);
  assert.doesNotMatch(collectText(h.tree()).join(' '), /详情更新失败/);
  h.unmount();
});

test('429 + Retry-After 5 秒在上限内：不在 1.5 秒重发，5 秒后自动重发详情', async () => {
  const h = harness();
  let round = 0;
  h.setNews(async () => {
    round += 1;
    if (round > 1) return item();
    const error = new Error('slow');
    error.code = 429;
    error.retryAfter = 5;
    error.retryable = true;
    throw error;
  });
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h.newsCalls.length, 1);
  await h.fireDue(1500);
  assert.equal(h.newsCalls.length, 1);
  await h.fireDue(5000);
  assert.equal(h.newsCalls.length, 2);
  assert.doesNotMatch(collectText(h.tree()).join(' '), /详情更新失败/);
  h.unmount();
});

test('无 seed 时详情失败显示不可用态并可手动重试', async () => {
  const h = harness();
  let blocked = true;
  h.setNews(async () => {
    if (!blocked) return item();
    throw Object.assign(new Error('blocked'), { code: 502, retryable: false });
  });
  h.render({ newsId: '9600' });
  await settle();
  assert.equal(h.newsCalls.length, 1);
  const text = collectText(h.tree()).join(' ');
  assert.match(text, /详情不可用/);
  const retry = findButton(h.tree(), '重试');
  assert.ok(retry, '无 seed 的错误态也必须有重试入口');
  blocked = false;
  retry.props.onClick();
  await settle();
  assert.equal(h.newsCalls.length, 2);
  const after = collectText(h.tree()).join(' ');
  assert.match(after, /第9600条快讯/);
  assert.doesNotMatch(after, /详情不可用/);
  h.unmount();
});

test('关抽屉滑出期间继续显示最后一条，换到别的新闻才回到骨架', async () => {
  const h = harness();
  const next = deferred();
  h.setNews(async (id) => (id === '9601' ? next.promise : item()));
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.match(collectText(h.tree()).join(' '), /第9600条快讯/);
  h.render({ newsId: null, seed: null });
  await settle();
  assert.match(collectText(h.tree()).join(' '), /第9600条快讯/, '滑出动画期间不得闪成骨架屏');
  h.render({ newsId: '9601', seed: null });
  await settle();
  assert.doesNotMatch(collectText(h.tree()).join(' '), /第9600条快讯/, '换条后不得沿用上一条');
  next.resolve(item({ newsId: '9601', titleZh: '第9601条快讯' }));
  await settle();
  assert.match(collectText(h.tree()).join(' '), /第9601条快讯/);
  h.unmount();
});

test('任务恢复读到 404 只提示记录不存在，不给重试钮也不再读', async () => {
  const h = harness();
  h.setJob(async () => { throw Object.assign(new Error('gone'), { code: 404 }); });
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  await h.fireDue(1500);
  await h.fireDue(3000);
  assert.equal(h.jobCalls.length, 1);
  const text = collectText(h.tree()).join(' ');
  assert.match(text, /任务记录已不存在/);
  assert.doesNotMatch(text, /任务状态暂时读不到/);
  assert.equal(findButton(h.tree(), '重试'), null, '404 不给必然再失败的重试');
  h.unmount();
});

for (const initialStatus of ['queued', 'in_progress']) {
  for (const path of ['recovery', 'poll']) {
    test(`${path} 读到任务 404（${initialStatus}）：撤掉运行态、补读详情、给出需确认的重新发起入口`, async () => {
      const h = harness();
      let jobRound = 0;
      h.setNews(async () => item({ analysisStatus: initialStatus, analysisJobId: 'job-1' }));
      h.setJob(async () => {
        jobRound += 1;
        if (path === 'poll' && jobRound === 1) return job({ status: initialStatus });
        throw Object.assign(new Error('gone'), { code: 404 });
      });
      h.render({ newsId: '9600', seed: item({ analysisStatus: initialStatus, analysisJobId: 'job-1' }) });
      await settle();
      if (path === 'poll') await h.fireDue(2000);
      const jobCalls = h.jobCalls.length;
      const newsCalls = h.newsCalls.length;
      await h.fireDue(10_000);
      await h.fireDue(10_000);
      assert.equal(h.jobCalls.length, jobCalls, '不再查询已确认缺失的任务 id');
      assert.ok(newsCalls >= 2, '确认缺失后补读新闻详情');
      const text = collectText(h.tree()).join(' ');
      assert.match(text, /任务记录已不存在/);
      assert.match(text, /任务记录缺失/);
      assert.doesNotMatch(text, /任务排队中|模型分析中/);
      assert.ok(findButton(h.tree(), '生成 AI 分析'), '重新发起仍需确认');
      assert.equal(h.createCalls.length, 0, '404 不得自动提交付费任务');
      h.unmount();
    });
  }
}

for (const [label, fresh, visible] of [
  ['已完成', () => completedItem(), /完整分析/],
  ['未分析', () => item({ analysisStatus: 'pending', analysisJobId: null }), /生成 AI 分析/],
]) {
  test(`任务 404 后补读详情为${label}：按新详情显示并撤掉缺失提示`, async () => {
    const h = harness();
    let newsRound = 0;
    h.setNews(async () => (++newsRound === 1 ? item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) : fresh()));
    h.setJob(async () => { throw Object.assign(new Error('gone'), { code: 404 }); });
    h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
    await settle();
    const text = collectText(h.tree()).join(' ');
    assert.match(text, visible);
    assert.doesNotMatch(text, /任务记录已不存在|任务记录缺失/);
    assert.equal(h.createCalls.length, 0);
    h.unmount();
  });
}

test('任务查询 503 仍是可重试的读取失败，不按记录缺失处理', async () => {
  const h = harness();
  h.setJob(async () => { throw Object.assign(new Error('busy'), { code: 503, retryable: true }); });
  h.render({ newsId: '9600', seed: item({ analysisStatus: 'queued', analysisJobId: 'job-1' }) });
  await settle();
  for (let round = 0; round < 6; round += 1) await h.fireDue(30_000);
  const text = collectText(h.tree()).join(' ');
  assert.match(text, /任务状态暂时读不到/);
  assert.doesNotMatch(text, /任务记录缺失/);
  const retry = findButton(h.tree(), '重试');
  assert.ok(retry);
  const before = h.jobCalls.length;
  retry.props.onClick();
  await settle();
  assert.equal(h.jobCalls.length, before + 1);
  assert.equal(h.createCalls.length, 0);
  h.unmount();
});

for (const [label, projected, expectedStatus] of [
  ['服务端投影还没带上新任务时保留提交态', item(), 'queued'],
  ['服务端投影已带上新任务时按详情为准', item({ analysisStatus: 'in_progress', analysisJobId: 'job-9' }), 'in_progress'],
]) {
  test(`在 seed 上提交分析后补读详情：${label}`, async () => {
    const h = harness();
    const initial = deferred();
    let round = 0;
    h.setNews(() => {
      round += 1;
      return round === 1 ? initial.promise : Promise.resolve(projected);
    });
    h.setJob(async (id) => job({ jobId: id, status: 'queued' }));
    h.setCreate(async () => job({ jobId: 'job-9', newsId: '9600', status: 'queued' }));
    h.render({ newsId: '9600', seed: item() });
    await settle();
    assert.equal(h.newsCalls.length, 1);
    const start = findButton(h.tree(), '生成 AI 分析');
    assert.ok(start, 'seed 已可见时就能提交');
    start.props.onClick();
    const confirm = findNode(h.tree(), (node) => node.props?.open && node.props?.confirmLabel === '生成分析');
    assert.ok(confirm);
    confirm.props.onConfirm();
    await settle();
    assert.deepEqual(h.createCalls, [['9600', false]]);
    assert.equal(h.newsCalls.length, 2, '提交后必须补读一次详情，而不是只作废初次读取');
    assert.equal(h.updates.at(-1).analysisJobId, 'job-9');
    assert.equal(h.updates.at(-1).analysisStatus, expectedStatus);
    initial.resolve(completedItem({ analysisJobId: 'job-0' }));
    await settle();
    assert.equal(h.updates.at(-1).analysisJobId, 'job-9', '被作废的初次读取不得写回');
    h.unmount();
  });
}

test('初始详情失败后在 seed 上提交分析，补读成功应清除旧错误提示', async () => {
  const h = harness();
  let round = 0;
  h.setNews(async () => {
    if (++round === 1) throw Object.assign(new Error('limited'), { code: 429, retryAfter: 30 });
    return item({ summaryZh: '补读成功的新摘要', analysisStatus: 'queued', analysisJobId: 'job-9' });
  });
  h.setCreate(async () => job({ jobId: 'job-9', newsId: '9600', status: 'queued' }));
  h.setJob(async (id) => job({ jobId: id, status: 'queued' }));
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.match(collectText(h.tree()).join(' '), /详情更新失败/);
  const start = findButton(h.tree(), '生成 AI 分析');
  assert.ok(start);
  start.props.onClick();
  const confirm = findNode(h.tree(), (node) => node.props?.open && node.props?.confirmLabel === '生成分析');
  assert.ok(confirm);
  confirm.props.onConfirm();
  await settle();
  assert.equal(h.newsCalls.length, 2);
  assert.equal(h.updates.at(-1).analysisJobId, 'job-9');
  const text = collectText(h.tree()).join(' ');
  assert.match(text, /补读成功的新摘要/);
  assert.doesNotMatch(text, /详情更新失败|详情不可用/);
  assert.equal(findButton(h.tree(), '重试'), null);
  h.unmount();
});

test('404 与 retryable=false 只打一次详情', async () => {
  for (const extras of [{ code: 404 }, { code: 502, retryable: false }]) {
    const h = harness();
    h.setNews(async () => {
      const error = new Error('blocked');
      Object.assign(error, extras);
      throw error;
    });
    h.render({ newsId: '9600', seed: item() });
    await settle();
    assert.equal(h.newsCalls.length, 1);
    await h.fireDue(1500);
    await h.fireDue(3000);
    assert.equal(h.newsCalls.length, 1, JSON.stringify(extras));
    assert.match(collectText(h.tree()).join(' '), /第9600条快讯/);
    h.unmount();
  }
});

test('睡眠期间关抽屉不再发详情请求', async () => {
  const h = harness();
  h.setNews(async () => {
    const error = new Error('busy');
    error.code = 503;
    error.retryable = true;
    throw error;
  });
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h.newsCalls.length, 1);
  h.render({ newsId: null, seed: item() });
  await settle();
  await h.fireDue(1500);
  await h.fireDue(3000);
  assert.equal(h.newsCalls.length, 1);
  h.unmount();
});

test('连续失败仍显示真实 seed 并给出错误/重试', async () => {
  const h = harness();
  h.setNews(async () => {
    const error = new Error('busy');
    error.code = 503;
    error.retryable = true;
    throw error;
  });
  h.render({ newsId: '9600', seed: item() });
  await settle();
  await h.fireDue(1500);
  await h.fireDue(3000);
  await settle();
  const text = collectText(h.tree()).join(' ');
  assert.match(text, /第9600条快讯/);
  assert.match(text, /摘要/);
  assert.match(text, /详情更新失败/);
  assert.match(text, /重试/);
  h.unmount();
});
