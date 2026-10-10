import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function load(file) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(new URL(`../src/components/open-intelligent-ui/${file}.ts`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { exports });
  return exports;
}
const { normalizeOpenGenUIContent, completedContentSignature, clampReportedHeight, isAllowedSandboxMessage } = load('schema');
const { buildFinalFrameContent, CSP_META_TAG, MEASUREMENT_JS } = load('frame-content');
const completed = {
  generating: false, html: ['<p>已有研判</p>'], htmlComplete: true,
  css: 'p{color:red}', cssComplete: true,
  jsFunctions: 'function render() {}', jsFunctionsComplete: true,
  jsExpressions: ['render()'], jsExpressionsComplete: true,
};
test('completion rejects unfinished stages and malformed payloads', () => {
  assert.ok(normalizeOpenGenUIContent(completed));
  for (const flag of ['generating', 'htmlComplete', 'cssComplete', 'jsFunctionsComplete', 'jsExpressionsComplete']) {
    assert.equal(normalizeOpenGenUIContent({ ...completed, [flag]: flag === 'generating' }), null, flag);
  }
  for (const invalid of [null, '', [], { ...completed, html: [1] }, { ...completed, css: 2 },
    { ...completed, jsFunctions: {} }, { ...completed, jsExpressions: ['ok', 1] },
    { ...completed, html: [' '] }, { ...completed, initialHeight: Infinity }]) assert.equal(normalizeOpenGenUIContent(invalid), null);
  assert.ok(normalizeOpenGenUIContent({ generating: false, html: ['<p>文字</p>'], htmlComplete: true }));
});
test('polling identity and unknown fields do not change execution; content does', () => {
  const signature = completedContentSignature(completed);
  assert.equal(completedContentSignature({ ...completed, unknown: 'ignored' }), signature);
  assert.equal(completedContentSignature(JSON.parse(JSON.stringify(completed))), signature);
  assert.notEqual(completedContentSignature({ ...completed, jsExpressions: ['other()'] }), signature);
  assert.notEqual(completedContentSignature({ ...completed, html: ['新研判'] }), signature);
  assert.equal(normalizeOpenGenUIContent({ ...completed, html: ['x'.repeat(512_000)] }), null);
});
test('height stays finite and permits growing and shrinking; overflow remains scrollable', () => {
  for (const value of [null, '200', Infinity, NaN]) assert.equal(clampReportedHeight(value), null);
  assert.equal(clampReportedHeight(-1), 50);
  assert.equal(clampReportedHeight(120.3), 121);
  assert.equal(clampReportedHeight(99_999), 20_000);
  assert.equal(clampReportedHeight(90), 90);
  assert.match(MEASUREMENT_JS, /overflow:auto!important/);
  assert.match(MEASUREMENT_JS, /MutationObserver/);
  assert.doesNotMatch(MEASUREMENT_JS, /var h = document.body.scrollHeight/);
});
test('CSP precedes all generated content and CSS cannot close its style element', () => {
  const html = '<script>early()</script><html><head><script>later()</script></head><body>研判</body></html>';
  const frame = buildFinalFrameContent(html, 'p{color:red}', ':root{--report-paper:#fff}');
  assert.ok(frame.indexOf(CSP_META_TAG) < frame.indexOf(html));
  for (const directive of ["default-src 'none'", "connect-src 'none'", "base-uri 'none'", "form-action 'none'", "img-src data: blob:", "font-src data:"]) assert.ok(CSP_META_TAG.includes(directive));
  assert.doesNotMatch(CSP_META_TAG, /https?:|self/);
  assert.ok(frame.includes('--report-paper'));
  assert.doesNotMatch(buildFinalFrameContent('<p>ok</p>', '</style><script>escaped()</script>'), /<style><\/style><script>/);
});
test('transport rejects host calls, interface registration and prototype paths', () => {
  for (const allowed of [{ type: 'service-message', callId: '123', methodName: 'iframeInitialized', arguments: [] },
    { type: 'response', callId: '123', success: true }, { type: '__ogui_resize', height: 900 }, { type: '__ogui_report_error' }]) assert.equal(isAllowedSandboxMessage(allowed), true);
  for (const denied of [null, {}, { type: 'set-interface', apiMethods: ['__proto__.run'] },
    { type: 'message', callId: '123', methodName: 'constructor' },
    { type: 'service-message', callId: '123', methodName: 'iframeInitialized.constructor', arguments: [] },
    { type: 'response', callId: '__proto__', success: true }, { type: '__ogui_resize', height: Infinity }]) assert.equal(isAllowedSandboxMessage(denied), false);
});
