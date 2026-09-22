import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { refreshFeedSnapshot } from '../src/components/catalysts/feedSnapshot.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');

test('empty feed keeps news count unknown when its fallback count request fails', async () => {
  const source = readFileSync(path.join(root, 'components/catalysts/useFeedResource.ts'), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  const empty = { items: [], nextCursor: null, total: 0, hiddenUnanalyzed: 0 };
  let countFails = true;
  let previous = null;
  const imports = {
    './api': { catalystsContract: {
      feed: async () => empty,
      newsToday: async () => {
        if (countFails) throw new Error('count unavailable');
        return { pending: 3 };
      },
    } },
    './filters': { toFeedQuery: () => ({}) },
    './feedSnapshot': { refreshFeedSnapshot },
    './useCatalystResource': { useCatalystResource: (_key, _policy, read) => read(previous) },
  };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require: (id) => {
      if (!Object.hasOwn(imports, id)) throw new Error(`Unexpected import ${id}`);
      return imports[id];
    },
  });
  const result = await module.exports.useFeedResource({});
  assert.deepEqual(result.items, []);
  assert.equal(result.hiddenCountUnknown, true);
  previous = result;
  countFails = false;
  const recovered = await module.exports.useFeedResource({});
  assert.equal(recovered.hiddenUnanalyzed, 3);
  assert.equal(recovered.hiddenCountUnknown, undefined, 'a successful refresh must clear the old unknown flag');
});
