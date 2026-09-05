import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { createRequire } from 'node:module';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);
function moduleAt(path, imports = {}) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInNewContext(source, { exports, require: key => imports[key] ?? require(key) });
  return exports;
}
const glyphs = moduleAt('../src/lib/numberTicker.ts');
let reduced = false;
const component = moduleAt('../src/components/shared/NumberTicker.tsx', {
  '@/lib/numberTicker': glyphs,
  '@/lib/utils': { cn: (...values) => values.filter(Boolean).join(' ') },
  '@/hooks/usePrefersReducedMotion': { usePrefersReducedMotion: () => reduced },
}).default;

test('decimal and place-value identity survives sign and thousands-boundary changes', () => {
  const a = glyphs.numberGlyphs('$999.99'); const b = glyphs.numberGlyphs('$1,000.00');
  assert.equal(a.find(g => g.key === 'integer-0').char, '9'); assert.equal(b.find(g => g.key === 'integer-0').char, '0');
  assert.equal(a.find(g => g.key === 'decimal-2').char, '9'); assert.equal(b.find(g => g.key === 'decimal-2').char, '0');
  const negative = glyphs.numberGlyphs('−1,002.34%');
  assert.equal(negative.find(g => g.key === 'decimal-1').char, '3');
  assert.equal(new Set(negative.map(g => g.key)).size, negative.length);
});

test('first render displays exact decimal price without counting from zero', () => {
  const html = renderToStaticMarkup(component({ text: '$1,234.56' }));
  assert.match(html, /aria-label="\$1,234\.56"/);
  assert.match(html, /translateY\(-5\.5em\)/);
  assert.match(html, /translateY\(-6\.6[0-9]*em\)/);
  assert.match(html, /width:1ch/);
});

test('reduced motion removes rolling duration while unchanged place keys remain stable', () => {
  for (const reduce of [false, true]) {
    reduced = reduce;
    const tree = component({ text: '-12.34%' });
    const digit = tree.props.children.props.children.find(child => child.props.children?.props?.animate);
    assert.equal(digit.props.children.props.initial, false);
    assert.equal(digit.props.children.props.transition.duration, reduce ? 0 : 0.25);
  }
});
