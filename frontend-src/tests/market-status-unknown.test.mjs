import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');
const nodeRequire = createRequire(import.meta.url);
const translate = (value) => value;

function load(relativePath, dependencies) {
  const source = readFileSync(path.join(root, relativePath), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require: (id) => dependencies[id] ?? nodeRequire(id),
  });
  return module.exports;
}

test('both market status maps preserve missing and unrecognized sessions as unknown', async () => {
  let body = {};
  const live = {
    asRec: (value) => value && typeof value === 'object' ? value : {},
    pickS: (record, ...keys) => keys.map((key) => record[key]).find((value) => typeof value === 'string' && value.length > 0) ?? null,
  };
  const globalGet = async () => body;
  const shared = load('api/modules/market.ts', {
    '../client': { mockOr: (_mock, read) => read() },
    '../marketRead': {},
    '../sharedRead': { sharedGlobalGet: globalGet },
    '../live': live,
    '@/mocks/fixtures': {},
    '@/mocks/fixtures2': {},
    '../../i18n/core.ts': { t: translate },
    '@/lib/quoteSymbol': {},
  });
  const detail = load('components/market/api.ts', {
    '@/api/client': { mockOr: (_mock, read) => read() },
    '@/api/sharedRead': { sharedGlobalGet: globalGet },
    '@/mocks/marketPulse': {},
  });
  for (const value of [undefined, '', 'surprise']) {
    body = value === undefined ? {} : { market: value };
    const [status, page] = await Promise.all([shared.marketApi.status(), detail.marketPulseApi.statusDetail()]);
    assert.equal(status.session, null);
    assert.equal(status.label, '时段未知');
    assert.equal(page.market, null);
  }
  body = { market: 'closed' };
  assert.equal((await shared.marketApi.status()).session, 'closed');
  assert.equal((await detail.marketPulseApi.statusDetail()).market, 'closed');
  body = { market: 'open' };
  assert.equal((await shared.marketApi.status()).session, 'regular');
  assert.equal((await detail.marketPulseApi.statusDetail()).market, 'open');
});

test('index cards and screener use the existing quote flash hook', () => {
  for (const relativePath of ['components/market/IndexCards.tsx', 'pages/Screener.tsx']) {
    const source = readFileSync(path.join(root, relativePath), 'utf8');
    assert.match(source, /useTickFlash\(/, relativePath);
    assert.doesNotMatch(source, /setTimeout\(\(\) => setFlashes\(\{\}\), 700\)/, relativePath);
  }
});
