import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function load(relativePath, imports = {}) {
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(new URL(`../src/${relativePath}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { exports, require: key => {
    if (key in imports) return imports[key];
    throw new Error(`Unexpected dependency: ${key}`);
  }});
  return exports;
}
const live = load('api/live.ts', { '../i18n/core.ts': { t: text => text } });
const { mapIndices } = load('api/modules/market.ts', {
  '../client': {}, '../marketRead': {}, '../sharedRead': {}, '../live': live,
  '@/mocks/fixtures': {}, '@/mocks/fixtures2': {},
  '../../i18n/core.ts': { t: text => text },
  '@/lib/quoteSymbol': load('lib/quoteSymbol.ts'),
});

test('an index with valid price survives missing or invalid change without inventing zero', () => {
  for (const change of [null, undefined, NaN, Infinity, 'unknown']) {
    const rows = mapIndices({ indices: [{ symbol: '^GSPC', price: 6123.45, change_percent: change }] });
    assert.equal(rows.length, 1);
    assert.equal(rows[0].price, 6123.45);
    assert.equal(rows[0].changePct, null);
    assert.equal(rows[0].change, null);
  }
});

test('invalid index prices never produce visible rows', () => {
  for (const price of [null, undefined, 0, -1, NaN, Infinity, true, 'unknown']) {
    assert.equal(mapIndices({ indices: [{ symbol: '^GSPC', price, change_percent: 0 }] }).length, 0);
  }
});

test('known zero and directional index changes keep their values', () => {
  for (const pct of [0, 2, -2]) {
    const [row] = mapIndices({ indices: [{ symbol: '^GSPC', price: 100, change_percent: pct }] });
    assert.equal(row.changePct, pct);
    assert.equal(row.change, Math.round(100 * pct / (100 + pct) * 100) / 100);
  }
});
