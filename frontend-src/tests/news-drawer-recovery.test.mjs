import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import {
  ReadAttemptAborted,
  boundedReadRetryDelayMs,
  createCancellableSleep,
  isAutoRetryableReadError,
  runBoundedRead,
  shouldApplyRecoveryJob,
} from '../src/lib/boundedReadRetry.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');
const source = fs.readFileSync(path.join(root, 'components/catalysts/NewsDrawer.tsx'), 'utf8');

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
async function settle() {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}

function createReactStub() {
  const slots = [];
  let cursor = 0;
  let renderFn = null;
  let renderArgs = [];
  let lastResult = null;
  let rendering = false;
  let renderQueued = false;
  const effects = [];

  function flushEffects() {
    for (const effect of effects) {
      if (!effect.dirty) continue;
      effect.dirty = false;
      if (effect.cleanup) effect.cleanup();
      const cleanup = effect.create();
      effect.cleanup = typeof cleanup === 'function' ? cleanup : null;
    }
  }

  function render() {
    if (rendering) {
      renderQueued = true;
      return;
    }
    rendering = true;
    let guard = 0;
    do {
      renderQueued = false;
      guard += 1;
      if (guard > 50) throw new Error('render loop runaway');
      cursor = 0;
      lastResult = renderFn(...renderArgs);
      flushEffects();
    } while (renderQueued);
    rendering = false;
  }

  const React = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) {
        slots[index] = { value: typeof initial === 'function' ? initial() : initial };
      }
      const slot = slots[index];
      if (!slot.set) {
        slot.set = (next) => {
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (Object.is(value, slot.value)) return;
          slot.value = value;
          render();
        };
      }
      return [slot.value, slot.set];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: { current: initial } };
      return slots[index].value;
    },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: fn, deps: undefined };
      const slot = slots[index];
      const changed = slot.deps === undefined || deps === undefined
        || deps.length !== slot.deps.length
        || deps.some((dep, i) => !Object.is(dep, slot.deps[i]));
      if (changed) {
        slot.value = fn;
        slot.deps = deps;
      }
      return slot.value;
    },
    useEffect(create, deps) {
      const index = cursor++;
      if (!(index in slots)) {
        const record = { deps: undefined, create, cleanup: null, dirty: true };
        slots[index] = { value: record };
        effects.push(record);
      }
      const record = slots[index].value;
      const changed = record.deps === undefined || deps === undefined
        || deps.length !== record.deps.length
        || deps.some((dep, i) => !Object.is(dep, record.deps[i]));
      record.deps = deps;
      record.create = create;
      if (changed) record.dirty = true;
    },
  };

  return {
    React,
    unmount() {
      for (const effect of effects) if (effect.cleanup) effect.cleanup();
    },
    mount(fn, ...args) {
      renderFn = fn;
      renderArgs = args;
      render();
      return () => lastResult;
    },
  };
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

function job(partial) {
  return {
    jobId: 'job-1',
    newsId: '9600',
    status: 'in_progress',
    progress: 10,
    submittedAt: '2026-09-13T00:00:00Z',
    updatedAt: '2026-09-13T00:00:01Z',
    error: null,
    cancellable: true,
    ...partial,
  };
}

function harness() {
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
  const env = {
    window: {
      setTimeout(fn, ms = 0) {
        const id = ++nextTimer;
        timers.set(id, { fn, ms });
        return id;
      },
      clearTimeout(id) { timers.delete(id); },
    },
    Date,
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
            throw new Error('recovery must not POST createAnalysisJob');
          },
        },
      };
      if (id === './bits') return {
        AnalysisStatusChip: passthrough, ClassificationChip: passthrough, ConfidenceLabel: passthrough,
        ImpactValue: passthrough, Led: passthrough, StaleChip: passthrough, TickerChip: passthrough,
      };
      if (id === './ConfirmDialog') return { default: passthrough };
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
  firstJob.resolve(job({ status: 'in_progress', progress: 20 }));
  await settle();
  assert.equal(h.jobCalls.length, 1, '恢复成功后等退避再打下一轮');
  await h.fireDue(2000);
  assert.equal(h.jobCalls.length, 2);
  pollJob.resolve(job({ status: 'completed', progress: 100 }));
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
  second.resolve(job({ jobId: 'job-2', newsId: '9600', status: 'in_progress' }));
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
  h2.setJob(async () => job({ status: 'queued', progress: null }));
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
  jobB.resolve(job({ jobId: 'job-B', status: 'in_progress', progress: 40 }));
  await settle();
  await h.fireDue(2000);
  const afterB = h.jobCalls.filter((id) => id === 'job-B').length;
  jobA.resolve(job({ jobId: 'job-A', status: 'in_progress', progress: 99 }));
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
  h.setJob(async () => job({ jobId: 'job-1', status: 'completed', progress: 100 }));
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

test('429 + Retry-After 30 秒不在 1.5/4.5 秒重发详情', async () => {
  const h = harness();
  h.setNews(async () => {
    const error = new Error('slow');
    error.code = 429;
    error.retryAfter = 30;
    error.retryable = true;
    throw error;
  });
  h.render({ newsId: '9600', seed: item() });
  await settle();
  assert.equal(h.newsCalls.length, 1);
  await h.fireDue(1500);
  await h.fireDue(4500);
  assert.equal(h.newsCalls.length, 1);
  await h.fireDue(30_000);
  assert.equal(h.newsCalls.length, 2);
  assert.match(collectText(h.tree()).join(' '), /第9600条快讯/);
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
