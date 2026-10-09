import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { normalizeAiBudgetSnapshot, sharedAiBudgetText } from '../src/api/aiBudget.ts';
import { aiJobBlockedMessage } from '../src/api/aiJobNormalize.ts';
import { setLocale } from '../src/i18n/testing.ts';
import { createReactStub } from './helpers/react-hooks.mjs';

const snapshot = (partial = {}) => normalizeAiBudgetSnapshot({
  daily_budget_usd: 9.5, budget_used_usd: 0, budget_remaining_usd: 9.5,
  dollar_budget_available: true, budget_basis: 'shared_usd',
  daily_token_limit: 10000000, token_budget_used_tokens: 9483009,
  ...partial,
});

test('new accounting window uses reported zero and 9.5 instead of historical totals', () => {
  const data = snapshot({ historical_cost_usd: 7.307196 });
  const text = sharedAiBudgetText(data);
  assert.match(text.summary, /9.50/);
  assert.match(text.summary, /估算及预留 0.00/);
  assert.match(text.summary, /剩余 9.50/);
  assert.doesNotMatch(text.summary, /7.31|9483009|10000000/);
  assert.match(text.note, /所有模型共用.*未知任务预留.*东京 09:00/);
});

test('partial snapshots never infer missing monetary amounts or invent a shared panel', () => {
  const text = sharedAiBudgetText(normalizeAiBudgetSnapshot({ daily_budget_usd: 9.5 }));
  assert.match(text.summary, /估算及预留 —.*剩余 —/);
  assert.equal(sharedAiBudgetText(normalizeAiBudgetSnapshot({ daily_budget_usd: 0 })), null);
  assert.equal(normalizeAiBudgetSnapshot({}), null);
});

test('tracking mode shows spending beyond the shared reference without a false remaining allowance', () => {
  const data = snapshot({ daily_budget_usd: 10, budget_used_usd: 12.34,
    budget_remaining_usd: 0, budget_enforced: false, budget_mode: 'tracking' });
  const output = sharedAiBudgetText(data);
  assert.equal(data.budgetEnforced, false);
  assert.match(output.summary, /共享日预算参考 10.00.*估算及预留 12.34/);
  assert.doesNotMatch(output.summary, /剩余/);
  assert.match(output.note, /仅统计费用.*超过参考金额仍继续.*09:00/);
  assert.equal(snapshot().budgetEnforced, null);
});

test('tracking budget explanation is translated without changing reported spending', () => {
  try {
    for (const locale of ['en', 'ja']) {
      setLocale(locale);
      const output = sharedAiBudgetText(snapshot({ daily_budget_usd: 10,
        budget_used_usd: 12.34, budget_enforced: false }));
      assert.match(output.summary, /10.00.*12.34/);
      assert.match(output.note, /09:00/);
      assert.doesNotMatch(output.summary + output.note, /共享日预算|超过参考金额/);
    }
  } finally { setLocale('zh'); }
});

test('shared dollar exhaustion blocks paid retry and has translated reset guidance', () => {
  try {
    for (const locale of ['zh', 'en', 'ja']) {
      setLocale(locale);
      const message = aiJobBlockedMessage({ status: 'failed', error: 'daily_budget_usd_reached' });
      assert.ok(message);
      assert.match(message, /09:00/);
      if (locale !== 'zh') assert.doesNotMatch(message, /共享模型/);
    }
  } finally { setLocale('zh'); }
});

function hero({ owner = true, budget = snapshot(), reason = 'available', available = true, statusState = 'ready' } = {}) {
  const runner = createReactStub();
  const jsx = { jsx: (type, props) => typeof type === 'function' ? type(props) : ({ type, props }), jsxs: (type, props) => typeof type === 'function' ? type(props) : ({ type, props }) };
  const raw = fs.readFileSync(new URL('../src/components/catalysts/StatusHero.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(raw, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  let calls = 0;
  vm.runInNewContext(code, { module, exports: module.exports, require(id) {
    if (id === 'react') return runner.React;
    if (id === 'react/jsx-runtime') return jsx;
    if (id === 'framer-motion') return { motion: new Proxy({}, { get: (_obj, name) => `motion.${String(name)}` }) };
    if (id === '@/hooks/useAccess') return { useAccess: () => ({ isOwner: owner }) };
    if (id === '@/hooks/usePolling') return { usePolling: () => {
      const data = calls++ % 3 === 0 ? { analysisAvailable: available, analysisReason: reason, analysisModel: 'claude-haiku-5-5', analysisReasoning: 'xhigh', analysisBudget: budget, collecting: true }
        : calls % 3 === 2 ? { scanning: false, groupCount: 0, updatedAt: '' } : { count: 0 };
      return { data, loading: false, error: null };
    } };
    if (id === '@/hooks/remoteState') return { remoteState: () => statusState };
    if (id === '../../api/aiBudget.ts') return { sharedAiBudgetText };
    if (id === './api') return { catalystsContract: {} };
    if (id === './bits') return { Led: 'Led' };
    if (id === '@/components/shared/SoftBadge' || id === '@/components/shared/AnalysisIcon') return { default: 'Badge' };
    if (id === '@/components/shared/Skeleton') return { SkeletonBlock: 'Skeleton' };
    if (id === '@/lib/format') return { fmtRelative: () => '' };
    if (id === '@/lib/aiModelLabel') return { aiModelLabel: (model, effort) => `${model} ${effort}` };
    if (id === '@/lib/afterLoadIdle') return { afterLoadIdle: () => () => {} };
    if (id === '@/lib/motion') return {};
    if (id === '@/lib/utils') return { cn: (...items) => items.join(' ') };
    if (id === '../../i18n/core.ts') return { t: (value) => value };
    throw new Error(id);
  } });
  const read = runner.mount(() => module.exports.default({}));
  const tree = read();
  runner.unmount();
  return tree;
}

function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (Array.isArray(node)) return node.map(text).join(' ');
  if (typeof node !== 'object') return String(node);
  return text(node.props?.children);
}

test('existing owner availability cell shows estimates and shared reset, visitors never see amounts', () => {
  const owner = text(hero());
  assert.match(owner, /共享日预算 9.50.*估算及预留 0.00.*剩余 9.50/);
  assert.match(owner, /东京 09:00/);
  const visitor = text(hero({ owner: false }));
  assert.doesNotMatch(visitor, /9.50|共享日预算|估算及预留/);
});

test('owner tracking display stays available above the reference; visitor amounts remain private', () => {
  const budget = snapshot({ daily_budget_usd: 10, budget_used_usd: 12.34,
    budget_remaining_usd: 0, budget_enforced: false });
  const output = text(hero({ budget }));
  assert.match(output, /共享日预算参考 10.00.*估算及预留 12.34/);
  assert.doesNotMatch(output, /共享日预算不足|剩余 0.00/);
  assert.doesNotMatch(text(hero({ budget, owner: false })), /10.00|12.34|预算参考/);
});

test('positive shared USD never displays token statistics as the active gate', () => {
  const blocked = text(hero({ budget: snapshot({ dollar_budget_available: false }), reason: 'daily_token_limit', available: false }));
  assert.match(blocked, /共享日预算不足/);
  assert.doesNotMatch(blocked, /今日模型用量已达上限|10000000|9483009/);
  const fundsAvailable = text(hero({ reason: 'daily_token_limit', available: false }));
  assert.doesNotMatch(fundsAvailable, /今日模型用量已达上限|共享日预算不足/);
});


test('a stale availability snapshot does not present an old remaining balance', () => {
  const stale = text(hero({ statusState: 'stale' }));
  assert.match(stale, /状态读取失败/);
  assert.doesNotMatch(stale, /共享日预算|估算及预留|9.50/);
});


test('unavailable shared accounting never presents fallback zero as verified spending', () => {
  const output = text(hero({ reason: 'shared_budget_unavailable', available: false }));
  assert.match(output, /共享预算暂时无法核对/);
  assert.doesNotMatch(output, /估算及预留 0.00|剩余 9.50/);
});
