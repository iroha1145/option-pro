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
const { normalizeOpenGenUIContent, clampReportedHeight } = load('schema');
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
test('the snapshot is whitelisted: unknown fields are dropped and oversized content is rejected', () => {
  // 沙箱里造出的对象跨 realm，先转成普通 JSON 再比较。
  const plain = (value) => JSON.parse(JSON.stringify(value));
  assert.deepEqual(plain(normalizeOpenGenUIContent({ ...completed, unknown: 'ignored' })), plain(normalizeOpenGenUIContent(completed)));
  assert.equal('unknown' in normalizeOpenGenUIContent({ ...completed, unknown: 'ignored' }), false);
  assert.equal(normalizeOpenGenUIContent({ ...completed, html: ['x'.repeat(512_000)] }), null);
});
test('initial height stays finite and clamped', () => {
  for (const value of [null, '200', Infinity, NaN]) assert.equal(clampReportedHeight(value), null);
  assert.equal(clampReportedHeight(-1), 50);
  assert.equal(clampReportedHeight(120.3), 121);
  assert.equal(clampReportedHeight(99_999), 20_000);
  assert.equal(clampReportedHeight(90), 90);
});
