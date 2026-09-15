import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { assertEpsChartArtifact } from './helpers/eps-chart-artifact.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function loaderHarness() {
  const source = fs.readFileSync(path.join(root, 'src/components/earnings/epsChartLoader.ts'), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  const requests = [];
  const evaluated = new Map();
  let failures = 2;
  const success = { default: () => null };
  const require = (id) => {
    if (id === 'virtual:eps-chart-url') return { default: '/assets/eps-chart-hash.js' };
    // A rejected module URL remains rejected after the network recovers.
    if (!evaluated.has(id)) {
      requests.push(id);
      evaluated.set(id, failures-- > 0 ? new Error('module fetch failed') : success);
    }
    const value = evaluated.get(id);
    if (value instanceof Error) throw value;
    return value;
  };
  new Function('exports', 'module', 'require', 'window', code)(
    module.exports, module, require, { location: { href: 'https://example.test/stock/NVDA' } },
  );
  return { ...module.exports, requests, success };
}

test('EPS isolates every load from other routes and retries across remounts', async () => {
  const h = loaderHarness();
  await assert.rejects(h.importEpsChart(false), /module fetch failed/);
  await assert.rejects(h.importEpsChart(true), /module fetch failed/);
  // A new component must not reuse the previous instance's failed recover=1.
  await assert.rejects(h.importEpsChart(false), /module fetch failed/);
  assert.equal(await h.importEpsChart(true), h.success);
  // Successful recovery remains usable on all subsequent visits.
  assert.equal(await h.importEpsChart(false), h.success);
  assert.equal(await h.importEpsChart(true), h.success);
  assert.deepEqual(h.requests, [
    'https://example.test/assets/eps-chart-hash.js?eps=1',
    'https://example.test/assets/eps-chart-hash.js?eps=1&recover=1',
    'https://example.test/assets/eps-chart-hash.js?eps=1&recover=2',
  ]);
});

test('built recovery chunk has no unloaded dependency and stays off first paint', () => {
  assertEpsChartArtifact(path.join(root, 'dist'));
});
