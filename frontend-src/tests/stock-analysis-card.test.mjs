import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source = fs.readFileSync(new URL('../src/components/detail/AiAnalysisCard.tsx', import.meta.url), 'utf8');
const interpolate = (key, params = {}) => key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? `{${name}}`));
const done = { id: 'saved-report', kind: 'signal-analysis', status: 'succeeded', progress: null, createdAt: '', updatedAt: '2026-10-10T20:00:00Z', result: { summary: '公开报告原文' }, model: 'claude-sonnet-5-5', reasoning: 'xhigh', usage: { input_tokens: 4, cached_input_tokens: 2, output_tokens: null } };
const quota = { limit: 1, used: 1, remaining: 0, resetAt: '2026-10-12T20:00:00Z', periodStart: '2026-10-09T20:00:00Z' };
function harness({ access, response, read, originalGet } = {}) {
  let cursor = 0, tree, identity = access ?? { isOwner: false, isSignedIn: true, username: 'customer', loading: false }, hookJob = null;
  const slots = [], effects = []; let signedComponent; let creates = 0, reads = 0, cancels = 0;
  const original = { document: globalThis.document, window: globalThis.window, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
  globalThis.window = { location: { pathname: '/stock/AAPL', search: '' }, addEventListener() {}, removeEventListener() {} };
  globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};
  const changed = (prev, next) => !prev || prev.length !== next.length || next.some((value, i) => !Object.is(value, prev[i]));
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial; return [slots[i], v => { slots[i] = typeof v === 'function' ? v(slots[i]) : v; }]; },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useCallback(fn, deps) { const i = cursor++; if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { fn, deps }; return slots[i].fn; },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || changed(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps, cleanup: old?.cleanup }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); } },
  };
  const adopted = [];
  const adopt = value => { adopted.push(value); hookJob = value; };
  const clear = () => { hookJob = null; };
  const useAiJob = () => ({ job: hookJob, error: null, queryIssue: null, starting: false, start: async create => { hookJob = await create(); }, cancel: () => { cancels++; }, resume() {}, adopt, clear });
  const jsx = (type, props, key) => ({ type, props: props ?? {}, key });
  const component = label => props => jsx(label, props);
  class ApiError extends Error {}
  const imports = {
    react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx },
    'react-router': { Link: component('link') }, '@/components/shared/AnalysisSources': { default: component('sources') }, '@/components/shared/ThinkingLabel': { default: component('thinking') },
    '@/lib/aiModelLabel': { aiModelLabel: (model, reasoning) => `${model} · ${reasoning}` }, '@/hooks/useAccess': { useAccess: () => identity }, '@/components/icons': { default: component('icon') },
    '@/api/client': { ApiError }, '@/api/modules/ai-jobs': { aiJobsApi: { get: originalGet ?? (async () => done) } }, '@/api/aiJobNormalize': { aiJobDeferralMessage: () => null, aiJobErrorMessage: v => v, aiJobResultSummary: result => result?.summary ?? null }, '@/lib/quoteSymbol': { isIndexSymbol: () => false }, '@/lib/loginReturn': { loginReturnState: () => ({ from: '/stock/AAPL' }) },
    './api': { getStockAnalysisView: async () => { reads++; return read ? read() : response; }, createSignalAnalysisJob: async () => { creates++; return done; } },
    './useAiJob': { useAiJob }, './StockAnalysisReport': { default: component('report') }, '../../i18n/core.ts': { t: interpolate, localeTag: () => 'en-US' },
  };
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  new Function('module','exports','require',js)(module,module.exports,id => { if (!(id in imports)) throw new Error(id); return imports[id]; });
  function expand(node) {
    if (Array.isArray(node)) return node.map(expand);
    if (!node || typeof node !== 'object') return node;
    if (typeof node.type === 'function') return expand(node.type(node.props));
    return { ...node, props: { ...node.props, children: expand(node.props.children) } };
  }
  const render = () => {
    cursor = 0;
    const outer = module.exports.default({ ticker: 'AAPL' });
    if (typeof outer.type === 'function') signedComponent = outer;
    tree = expand(outer);
    while (effects.length) effects.shift()();
    return tree;
  };
  const nodes = () => {
    const result = []; const walk = node => { if (Array.isArray(node)) node.forEach(walk); else if (node && typeof node === 'object') { result.push(node); walk(node.props?.children); } }; walk(tree); return result;
  };
  const text = () => {
    const words = []; const walk = node => { if (Array.isArray(node)) node.forEach(walk); else if (node && typeof node === 'object') walk(node.props?.children); else if (typeof node === 'string' || typeof node === 'number') words.push(String(node)); }; walk(tree); return words.join(' ');
  };
  return { render, nodes, text, setAccess: value => { identity = value; }, get componentKey() { return signedComponent?.key; }, get creates() { return creates; }, get reads() { return reads; }, get cancels() { return cancels; }, get adopted() { return adopted; },
    async settle() { for (let i = 0; i < 4; i++) { await Promise.resolve(); render(); } },
    close() { for (const slot of slots) slot?.cleanup?.(); Object.assign(globalThis, original); },
  };
}
test('anonymous users get the login return link; confirmed ordinary customers get the report component', () => {
  const anon = harness({ access: { isOwner: false, isSignedIn: false, username: null, loading: false } });
  try { anon.render(); assert.match(anon.text(), /登录后可用模型分析/); assert.equal(anon.nodes().find(node => node.type === 'link').props.to, '/login'); assert.equal(anon.reads, 0); } finally { anon.close(); }
  const customer = harness({ response: { ticker: 'AAPL', enabled: true, canGenerate: false, quota, activeJob: null, latestJob: done } });
  try { customer.render(); assert.equal(customer.componentKey, 'customer:customer:AAPL'); assert.doesNotMatch(customer.text(), /去登录/); } finally { customer.close(); }
});
test('zero quota keeps shared report readable, disables creation, and reports real usage without invented zero', async () => {
  const h = harness({ response: { ticker: 'AAPL', enabled: true, canGenerate: false, quota, activeJob: null, latestJob: done } });
  try {
    h.render(); await h.settle();
    assert.match(h.text(), /今日剩余 0\/1 次/); assert.match(h.text(), /查看已有共享报告不扣次数/);
    const report = h.nodes().find(node => node.type === 'report'); assert.equal(report.props.result.summary, '公开报告原文');
    const button = h.nodes().find(node => node.type === 'button' && node.props.children === '重新分析'); assert.equal(button.props.disabled, true);
    assert.match(h.text(), /xhigh/); assert.match(h.text(), /缓存读取用量/); assert.doesNotMatch(h.text(), /输出用量/); assert.equal(h.creates, 0);
  } finally { h.close(); }
});
test('restored shared active task keeps last report and only owner sees cancellation', async () => {
  const response = { ticker: 'AAPL', enabled: true, canGenerate: false, quota, activeJob: { ...done, id: 'active', status: 'queued', result: null }, latestJob: done };
  for (const owner of [false, true]) {
    const h = harness({ access: { isOwner: owner, isSignedIn: true, username: owner ? null : 'customer', loading: false }, response });
    try {
      h.render(); await h.settle(); assert.match(h.text(), /排队中/); assert.match(h.text(), /保留上一份报告/);
      assert.equal(h.nodes().filter(node => node.type === 'button' && node.props.children === '取消分析').length, owner ? 1 : 0);
      assert.equal(h.creates, 0); assert.ok(h.nodes().some(node => node.type === 'report'));
    } finally { h.close(); }
  }
});
test('logout switches to anonymous markup immediately; pending old GET is ignored after unmount', async () => {
  let resolve; const pending = new Promise(r => { resolve = r; });
  const h = harness({ read: () => pending });
  try { h.render(); h.close(); resolve({ ticker: 'AAPL', enabled: true, canGenerate: false, quota, latestJob: done }); await Promise.resolve(); await Promise.resolve(); assert.equal(h.creates, 0); } finally { /* already unmounted */ }
  const anon = harness({ access: { isSignedIn: false, isOwner: false, username: null, loading: false } });
  try { anon.render(); assert.ok(!anon.nodes().some(node => node.type === 'report')); assert.match(anon.text(), /去登录/); } finally { anon.close(); }
});

test('disabled stock analysis uses the stock-specific label and keeps the saved report readable', async () => {
  const h = harness({ response: { ticker: 'AAPL', enabled: false, canGenerate: false, quota, activeJob: null, latestJob: done } });
  try {
    h.render(); await h.settle();
    assert.match(h.text(), /个股分析已关闭/); assert.doesNotMatch(h.text(), /手动分析已关闭/);
    assert.ok(h.nodes().some(node => node.type === 'report'));
    assert.equal(h.nodes().find(node => node.type === 'button' && node.props.children === '重新分析').props.disabled, true);
  } finally { h.close(); }
});

test('report display prefers actual completion time and labels the old update-time fallback honestly', async () => {
  const completedAt = '2026-10-09T20:00:00Z';
  for (const saved of [{ ...done, completedAt }, done]) {
    const h = harness({ response: { ticker: 'AAPL', enabled: true, canGenerate: false, quota, activeJob: null, latestJob: saved } });
    try {
      h.render(); await h.settle();
      const stamp = saved.completedAt || saved.updatedAt;
      const expected = new Date(stamp).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
      assert.ok(h.text().includes(`${saved.completedAt ? '报告完成时间' : '报告更新时间'} ${expected}`));
      assert.doesNotMatch(h.text(), saved.completedAt ? /报告更新时间/ : /报告完成时间/);
    } finally { h.close(); }
  }
});

test('unknown submission rereads the same task without creating or spending again and does not pretend a failed reread succeeded', async () => {
  const unknown = { ...done, id: 'unknown-job', status: 'failed', error: 'submission_outcome_unknown', result: undefined };
  const ids = [];
  const h = harness({ response: { ticker: 'AAPL', enabled: true, canGenerate: false, quota, activeJob: unknown, latestJob: null }, originalGet: async id => { ids.push(id); return unknown; } });
  try {
    h.render(); await h.settle();
    const retry = h.nodes().find(node => node.type === 'button' && node.props.children === '继续查询原任务');
    assert.ok(retry); await retry.props.onClick(); await h.settle();
    assert.deepEqual(ids, ['unknown-job']); assert.equal(h.creates, 0);
    assert.ok(h.nodes().find(node => node.type === 'button' && node.props.children === '生成分析').props.disabled);
    assert.ok(!h.nodes().some(node => node.type === 'report'));
    assert.doesNotMatch(h.text(), /模型分析中/);
  } finally { h.close(); }
});
test('a late unknown-task reread cannot adopt its result after the account component unmounts', async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  const unknown = { ...done, id: 'unknown-job', status: 'failed', error: 'submission_outcome_unknown', result: undefined };
  const h = harness({ response: { ticker: 'AAPL', enabled: true, canGenerate: false, quota, activeJob: unknown, latestJob: null }, originalGet: () => pending });
  h.render(); await h.settle();
  const request = h.nodes().find(node => node.type === 'button' && node.props.children === '继续查询原任务').props.onClick();
  const before = h.adopted.length;
  h.close(); resolve({ ...done, id: 'unknown-job' }); await request; await Promise.resolve();
  assert.equal(h.adopted.length, before, 'the old account must not adopt the late completed report');
  assert.equal(h.creates, 0); assert.ok(!h.nodes().some(node => node.type === 'report'));
});
