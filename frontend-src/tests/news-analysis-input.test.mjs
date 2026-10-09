import * as aiBudget from '../src/api/aiBudget.ts';
import * as evidenceSources from '../src/api/evidenceSources.ts';
import * as aiModelLabels from '../src/lib/aiModelLabel.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { createReactStub } from './helpers/react-hooks.mjs';
import * as retry from '../src/lib/boundedReadRetry.ts';
import * as errorText from '../src/components/catalysts/analysisErrorText.ts';
import { SHARED_UI_STUBS } from './helpers/shared-ui-stubs.mjs';

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');
const read = (name) => fs.readFileSync(path.join(src, name), 'utf8');
const compile = (name, jsx = false) => ts.transpileModule(read(name), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: jsx ? ts.JsxEmit.ReactJSX : undefined },
}).outputText;
const asRec = (value) => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const pick = (kind, row, keys) => {
  for (const key of keys) {
    const value = row[key];
    if (kind === 'string' && typeof value === 'string' && value) return value;
    if (kind === 'boolean' && typeof value === 'boolean') return value;
    if (kind === 'number' && typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
};

function mappedNews(raw) {
  const module = { exports: {} };
  vm.runInNewContext(compile('components/catalysts/api.ts'), {
    module, exports: module.exports,
    require(id) {
      if (id === '@/api/client') return {
        ApiError: Error, get: async () => ({ item: raw }), mockOr: (_fixture, live) => live(),
        idFromLocation: () => null, invalidateBootPrefetch() {}, post() {}, postCreate() {}, toQuery() {},
      };
      if (id === '@/api/live') return {
        asRec,
        pickS: (row, ...keys) => pick('string', row, keys),
        pickB: (row, ...keys) => pick('boolean', row, keys),
        pickN: (row, ...keys) => pick('number', row, keys),
        unwrap: (row, ...keys) => keys.map((key) => row[key]).find(Array.isArray) ?? [],
      };
      if (id === '@/mocks/fixtures2') return {};
      if (id === '@/components/catalysts/focusCycleRequest') return {};
      if (id === '../../api/aiBudget.ts') return aiBudget;
    if (id === '../../api/evidenceSources.ts') return evidenceSources;
    if (id === '../../i18n/core.ts') return { t: (value) => value };
      if (id === './resourceSignals') return { notifyCatalystReadsInvalidated() {} };
      throw new Error(id);
    },
  });
  return module.exports.catalystsContract.news('42');
}

function renderDrawer(seed) {
  const runner = createReactStub();
  const module = { exports: {} };
  const passthrough = (type, props) => ({ type, props });
  const jsx = { jsx: passthrough, jsxs: passthrough, Fragment: 'fragment' };
  vm.runInNewContext(compile('components/catalysts/NewsDrawer.tsx', true), {
    module, exports: module.exports,
    window: { setTimeout: () => 1, clearTimeout() {} },
    require(id) {
      if (id === 'react') return runner.React;
      if (id === 'react/jsx-runtime') return jsx;
      if (id === 'framer-motion') return { motion: new Proxy({}, { get: () => passthrough }), AnimatePresence: passthrough };
      if (id === '@/components/Drawer' || id === '@/components/icons' || id === '@/components/shared/AnalysisIcon'
        || id === '@/components/shared/SoftBadge') return { default: passthrough };
      if (id === '@/components/shared/Skeleton') return { SkeletonBlock: passthrough, SkeletonText: passthrough };
      if (id === '@/hooks/useAccess') return { useAccess: () => ({ isOwner: false, loading: false }) };
      if (id === '@/hooks/useToast') return { useToast: () => ({ info() {}, success() {}, error() {} }) };
      if (id === '@/hooks/useShell') return { useShell: () => ({ openTicker() {} }) };
      if (id === '@/lib/aiModelLabel') return aiModelLabels;
      if (id === '@/lib/format') return { fmtLocaleDateTime: () => 'time', fmtLocaleTime: () => 'time' };
      if (id === '@/api/queryRegistry') return { getQueryPrincipalGeneration: () => 0 };
      if (id === '@/lib/boundedReadRetry') return retry;
      if (id === './api') return { catalystsContract: { news: () => new Promise(() => {}) } };
      if (id === './analysisErrorText') return errorText;
      if (id === './bits') return Object.fromEntries([
        'AnalysisStatusChip', 'ClassificationChip', 'ConfidenceLabel', 'ImpactValue', 'Led', 'StaleChip', 'TickerChip',
      ].map((name) => [name, passthrough]));
      if (id === './ConfirmDialog') return { default: passthrough };
      if (id === '../../api/aiBudget.ts') return aiBudget;
    if (id === '../../api/evidenceSources.ts') return evidenceSources;
    if (id === '../../i18n/core.ts') return { t: (value) => value };
      if (id in SHARED_UI_STUBS) return SHARED_UI_STUBS[id];
      throw new Error(id);
    },
  });
  const getTree = runner.mount(() => module.exports.default({ newsId: '42', seed, onClose() {}, onUpdate() {} }));
  return getTree();
}

function texts(node, output = []) {
  if (typeof node === 'string' || typeof node === 'number') output.push(String(node));
  else if (Array.isArray(node)) node.forEach((child) => texts(child, output));
  else if (node?.props) texts(node.props.children, output);
  return output.join(' ');
}

const base = {
  news_id: 42, source: 'seekingalpha/breaking', source_title: 'Example Corp declares dividend',
  title_zh: '公司宣布派息', summary_zh: '分红摘要', source_tickers: [], analysis_status: 'completed',
  analysis: { headline_summary: '分析', causal_summary: '原因', classification: 'neutral', confidence: 50 },
};

test('详情映射保留原始标题与绑定分析的输入依据，并容忍旧或无效元数据', async () => {
  const article = await mappedNews({ ...base, analysis_input: {
    basis: 'article_body', article_status: 'available', reason: null, body_characters: 1742, truncated: true,
  } });
  assert.equal(article.sourceTitle, base.source_title);
  assert.equal(article.analysisInput.basis, 'article_body');
  assert.equal(article.analysisInput.bodyCharacters, 1742);
  assert.equal(article.analysisInput.truncated, true);
  assert.equal((await mappedNews(base)).analysisInput, null);
  assert.equal((await mappedNews({ ...base, analysis_input: { basis: 'unknown', article_status: 'available' } })).analysisInput, null);
  assert.equal((await mappedNews({ ...base, analysis_input: {
    basis: 'article_body', article_status: 'unavailable', body_characters: 1742, truncated: false,
  } })).analysisInput, null);
});

test('抽屉只给已完成分析标注输入依据，保留原始公司名，隐藏空代码行', async () => {
  const article = await mappedNews({ ...base, analysis_input: {
    basis: 'article_body', article_status: 'available', reason: null, body_characters: 1742, truncated: true,
  } });
  const articleText = texts(renderDrawer(article));
  assert.match(articleText, /原文标题.*Example Corp declares dividend/);
  assert.match(articleText, /仅基于正文节选分析/);
  assert.doesNotMatch(articleText, /相关标的/);
  const fullText = texts(renderDrawer({ ...article, analysisInput: { ...article.analysisInput, truncated: false } }));
  assert.match(fullText, /基于新闻正文分析/);
  assert.doesNotMatch(fullText, /仅基于正文节选分析/);

  const unavailable = await mappedNews({ ...base, analysis_input: {
    basis: 'title_summary', article_status: 'unavailable', reason: 'http_403', body_characters: 0, truncated: false,
  } });
  const unavailableText = texts(renderDrawer(unavailable));
  assert.match(unavailableText, /仅基于标题和摘要分析.*未能取得正文/);
  assert.doesNotMatch(unavailableText, /http_403/);

  const webAnalysis = { ...unavailable.analysis, evidenceSources: [
    { title: '公司公告', url: 'https://www.nvidia.com/', type: 'web_search' },
  ] };
  const webText = texts(renderDrawer({ ...unavailable, analysis: webAnalysis }));
  assert.match(webText, /基于标题、摘要及联网来源分析.*未能取得正文/);
  assert.doesNotMatch(webText, /仅基于标题和摘要分析|未调用模型/);
  assert.match(texts(renderDrawer({ ...article, analysis: webAnalysis })), /基于正文节选及联网来源分析/);
  assert.match(texts(renderDrawer({ ...article, analysis: webAnalysis,
    analysisInput: { ...article.analysisInput, truncated: false },
  })), /基于新闻正文及联网来源分析/);

  const legacyText = texts(renderDrawer(await mappedNews(base)));
  assert.match(legacyText, /仅基于标题和摘要分析/);
  const pendingText = texts(renderDrawer({ ...article, analysisStatus: 'pending', analysis: null }));
  assert.doesNotMatch(pendingText, /基于新闻正文|基于正文节选/);
  assert.doesNotMatch(pendingText, /仅基于标题和摘要分析/);
  const failedText = texts(renderDrawer({ ...article, analysisStatus: 'failed', analysis: null }));
  assert.doesNotMatch(failedText, /基于新闻正文|基于正文节选/);
});
