import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { overlaysConsistentWithBars } from '../src/components/detail/chart-drawings/analysis/mapBundle.ts';
import { lastBarText } from '../src/components/detail/chartTime.ts';

const root = new URL('../src/', import.meta.url);
function parse(file) {
  const source = readFileSync(new URL(file, root), 'utf8');
  return ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
}
function find(ast, predicate) {
  const found = [];
  const walk = (node) => {
    if (predicate(node)) found.push(node);
    ts.forEachChild(node, walk);
  };
  walk(ast);
  return found;
}
function calls(ast, name) {
  return find(ast, node => ts.isCallExpression(node)
    && ts.isIdentifier(node.expression) && node.expression.text === name);
}
function property(ast, name) {
  return find(ast, node => ts.isPropertyAssignment(node)
    && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name)) && node.name.text === name);
}
function literal(ast, value) {
  return find(ast, node => ts.isStringLiteralLike(node) && node.text === value);
}

const kline = parse('components/detail/KlineChart.tsx');
const panel = parse('components/detail/StructurePanel.tsx');
const detail = parse('components/detail/api.ts');

test('daily technical anchors reject old or absent chart versions, while allowing two regular bars of lag', () => {
  const bars = Array.from({ length: 5 }, (_, index) => ({ t: `2026-09-${String(index + 1).padStart(2, '0')}T20:00:00Z` }));
  assert.equal(overlaysConsistentWithBars(null, bars), false);
  assert.equal(overlaysConsistentWithBars({ last_bar: { trade_date: '2026-09-01' } }, bars), false);
  assert.equal(overlaysConsistentWithBars({ last_bar: { trade_date: '2026-09-03' } }, bars), true);
  assert.equal(overlaysConsistentWithBars({ data_through: '2026-09-04' }, bars), true);
  assert.equal(overlaysConsistentWithBars({ data_through: '2026-09-09' }, bars), false);
  assert.equal(overlaysConsistentWithBars({ data_through: null }, bars), true);
  assert.equal(overlaysConsistentWithBars({ data_through: '2026-09-02' }, [
    ...bars.slice(0, 2), { t: '2026-09-03T12:00:00Z', ext: true }, ...bars.slice(2, 4),
  ]), true);
});

test('chart overlay and snapping integration use the same displayed overlay collection', () => {
  // This is a wiring check; the browser indicator suite covers the actual chart and mismatch message.
  const snap = calls(kline, 'snapCandidatesFromOverlays');
  const marks = calls(kline, 'overlaysToMarks');
  assert.equal(snap.length, 1);
  assert.equal(marks.length, 1);
  assert.ok(ts.isIdentifier(snap[0].arguments[0]));
  assert.ok(ts.isIdentifier(marks[0].arguments[0]));
  assert.equal(snap[0].arguments[0].text, marks[0].arguments[0].text);
  assert.ok(calls(kline, 'overlaysConsistentWithBars').length >= 1);
});

test('chart footer uses the last bar timestamp without rewriting the source bars', () => {
  const data = { bars: [{ t: '2026-09-11T18:30:00Z' }], last_bar_at: '2026-09-12T01:00:00Z' };
  const before = structuredClone(data);
  assert.equal(lastBarText(data, '5m'), '2026-09-11 14:30 ET');
  assert.deepEqual(data, before);
  assert.ok(calls(kline, 'lastBarText').length >= 1);
});

test('AST wiring: structure risk fields retain missing-value guards and nullable mapping', () => {
  for (const field of ['breakout_quality_adjustment', 'false_breakout_risk']) {
    const mapped = property(detail, field);
    assert.ok(mapped.some(node => ts.isCallExpression(node.initializer)
      && ts.isIdentifier(node.initializer.expression) && node.initializer.expression.text === 'pickN'
      && node.initializer.arguments.some(arg => ts.isStringLiteral(arg) && arg.text === field)), field);
    const guarded = find(panel, node => ts.isConditionalExpression(node)
      && ts.isStringLiteral(node.whenFalse) && node.whenFalse.text === '—'
      && ts.isCallExpression(node.whenTrue)
      && node.whenTrue.arguments.some(arg => ts.isPropertyAccessExpression(arg) && arg.name.text === field));
    assert.ok(guarded.length > 0, field);
  }
  assert.ok(find(panel, node => ts.isVariableDeclaration(node)
    && ts.isIdentifier(node.name) && node.name.text === 'vpmMeasured'
    && ts.isBinaryExpression(node.initializer)
    && node.initializer.operatorToken.kind === ts.SyntaxKind.EqualsEqualsEqualsToken
    && ts.isStringLiteral(node.initializer.right) && node.initializer.right.text === 'active').length > 0);
  assert.ok(find(detail, node => ts.isShorthandPropertyAssignment(node) && node.name.text === 'base_state').length > 0);
  assert.ok(property(detail, 'closed').some(node => ts.isBinaryExpression(node.initializer)
    && node.initializer.operatorToken.kind === ts.SyntaxKind.EqualsEqualsEqualsToken
    && node.initializer.right.kind === ts.SyntaxKind.TrueKeyword));
  assert.ok(literal(panel, '（盘中暂定）').length > 0);
});
