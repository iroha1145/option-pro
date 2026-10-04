import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = fs.readFileSync(new URL('../src/pages/Breakouts.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('Breakouts.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const component = parsed.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === 'NextScanCountdown');
const page = parsed.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === 'Breakouts');

test('only the next-scan display owns the one-second clock', () => {
  assert.ok(component);
  assert.doesNotMatch(page.getText(parsed), /useNow\(/);
  let now = Date.parse('2026-10-02T18:00:00Z');
  const intervals = [];
  const module = { exports: {} };
  const code = ts.transpileModule(`${component.getText(parsed)}\nexport { NextScanCountdown };`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, Date,
    useNow: (interval) => { intervals.push(interval); return now; },
    __t: (value) => value,
    require: () => ({ jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }),
  });
  const read = (nextSessionAt) => module.exports.NextScanCountdown({ nextSessionAt }).props.children[2].props.children;
  assert.equal(read('2026-10-02T18:01:05Z'), '01:05');
  now += 1000;
  assert.equal(read('2026-10-02T18:01:05Z'), '01:04');
  assert.equal(read('2026-10-02T17:59:00Z'), '00:00');
  assert.deepEqual(intervals, [1000, 1000, 1000]);
});
