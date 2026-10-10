import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as evidence from '../src/api/evidenceSources.ts';
import { normalizeAiJob, aiJobBlockedMessage, aiJobKnownErrorMessage } from '../src/api/aiJobNormalize.ts';

const source = (url, title = '官方公告', type = 'web_search') => ({ title, url, type });

test('source normalization rejects non-web URLs, credentials, and invalid source shapes', () => {
  const safe = evidence.normalizeEvidenceSources([
    source('javascript:alert(1)'), source('data:text/html,bad'), source('//example.test'),
    source('https://user:password@example.test'), source('https://user%40name@example.test'),
    source('https://example.test/\npath'), source('https://example.test/a', 'title', 'code_execution'),
    { url: 'https://example.test/no-title', type: 'web_search' }, null, [],
    source('https://example.test/report', '公告', 'web_fetch'),
  ]);
  assert.deepEqual(safe, [source('https://example.test/report', '公告', 'web_fetch')]);
  assert.deepEqual(evidence.normalizeEvidenceSources({ evidence_sources: safe }), []);
});

test('sources are deduplicated, capped at ten, and labels have a bounded length', () => {
  const raw = [source('https://example.test'), source('https://example.test/'),
    ...Array.from({ length: 20 }, (_, i) => source(`https://example.test/${i}`, 'x'.repeat(1000)))];
  const safe = evidence.normalizeEvidenceSources(raw);
  assert.equal(safe.length, 10);
  assert.equal(new Set(safe.map((row) => row.url)).size, 10);
  assert.ok(safe.every((row) => row.title.length <= 240));
  assert.equal(evidence.normalizeEvidenceSources([source('http://example.test', '')])[0].title, 'example.test');
});

test('public AI jobs keep evidence and reported tool use without exposing extra fields', () => {
  const job = normalizeAiJob({
    model: 'claude-haiku-5-5', evidence_sources: [source('https://example.test/report')],
    usage: { web_search_requests: 1, web_fetch_requests: 1, code_execution_requests: 2 },
    thinking: 'private', provider_message_id: 'private-id', intermediate_tool_content: 'private-tool',
  });
  assert.deepEqual(job.evidenceSources, [source('https://example.test/report')]);
  assert.deepEqual(job.usage, { web_search_requests: 1, web_fetch_requests: 1, code_execution_requests: 2 });
  assert.ok(!JSON.stringify(job).includes('private'));
});

function renderSources(sources) {
  const path = new URL('../src/components/shared/AnalysisSources.tsx', import.meta.url);
  const code = ts.transpileModule(fs.readFileSync(path, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  // 组件内部拆出的 SourceLinkList 是函数组件，替身直接展开它，断言看到的仍是最终的 <a>。
  const element = (type, props) => (typeof type === 'function' ? type(props) : { type, props });
  const jsx = { jsx: element, jsxs: element };
  vm.runInNewContext(code, { module, exports: module.exports, require(id) {
    if (id === 'react/jsx-runtime') return jsx;
    if (id === '../../api/evidenceSources.ts') return evidence;
    if (id === '../../i18n/core.ts') return { t: (value) => value };
    throw new Error(id);
  } });
  return module.exports.default({ sources });
}

function nodes(tree, type) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap((child) => nodes(child, type));
  return [...(tree.type === type ? [tree] : []), ...nodes(tree.props?.children, type)];
}

test('source links render safe external links and allow narrow-screen wrapping', () => {
  const tree = renderSources([source('https://example.test/report', 'A'.repeat(1000)), source('javascript:bad')]);
  const links = nodes(tree, 'a');
  assert.equal(links.length, 1);
  assert.equal(links[0].props.href, 'https://example.test/report');
  assert.equal(links[0].props.target, '_blank');
  assert.equal(links[0].props.rel, 'noopener noreferrer');
  assert.match(links[0].props.className, /min-w-0/);
  assert.match(links[0].props.className, /overflow-wrap:anywhere/);
  assert.equal(links[0].props.children.length, 240);
  assert.equal(nodes(tree, 'p')[0].props.children, '信息来源');
});

test('missing or rejected sources do not draw an empty frame', () => {
  assert.equal(renderSources(undefined), null);
  assert.equal(renderSources([]), null);
  assert.equal(renderSources([source('file:///etc/hosts')]), null);
});

test('unknown submission explains the hold and blocks retry, and tool failures are readable', () => {
  const message = aiJobBlockedMessage({ status: 'failed', error: 'submission_outcome_unknown' });
  assert.match(message, /停止重复提交/);
  assert.doesNotMatch(message, /可以重试|请重试/);
  for (const code of ['provider_tool_result_invalid', 'provider_invalid_final_tool', 'provider_unknown_client_tool', 'provider_invalid_tool_response', 'provider_incomplete_tool_result', 'provider_unknown_server_tool']) {
    assert.equal(aiJobKnownErrorMessage(code), '模型返回的内容未通过检查，分析已停止');
  }
});
