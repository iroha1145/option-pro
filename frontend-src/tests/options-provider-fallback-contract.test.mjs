import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

async function source(relativePath) {
  return readFile(path.join(src, relativePath), 'utf8');
}

function loadOptionsModule(marketGet) {
  const modulePath = path.join(src, 'api', 'modules', 'options.ts');
  const compiled = ts.transpileModule(fs.readFileSync(modulePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const asRec = (value) =>
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? value
      : {};
  const module = { exports: {} };
  const require = (id) => {
    if (id === '../client') {
      return {
        get: async () => ({}),
        mockOr: (_fixture, live) => live(),
        toQuery: () => '',
      };
    }
    if (id === '../marketRead') return { marketGet };
    if (id === '../live') {
      return {
        asRec,
        pickN: (row, ...keys) => {
          for (const key of keys) {
            const value = row[key];
            if (typeof value === 'number' && Number.isFinite(value)) return value;
          }
          return null;
        },
        pickS: (row, ...keys) => {
          for (const key of keys) {
            if (typeof row[key] === 'string' && row[key]) return row[key];
          }
          return null;
        },
        unwrap: (body, ...keys) => {
          if (Array.isArray(body)) return body;
          for (const key of keys) {
            if (Array.isArray(body?.[key])) return body[key];
          }
          return [];
        },
      };
    }
    if (id === '@/mocks/fixtures2') {
      return {
        getUnusualOptions: () => [],
        getOptionExpirations: () => [],
        getOptionChain: () => ({}),
      };
    }
    throw new Error(`unexpected import: ${id}`);
  };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require,
    URLSearchParams,
  });
  return module.exports.optionsApi;
}

test('期权链保留真实来源并按 Retry-After 冷却重试', async () => {
  const api = await source('api/modules/options.ts');
  const panel = await source('components/detail/OptionsPanel.tsx');

  assert.match(api, /provider:\s*pickS\(r,\s*'provider'\)/);
  assert.match(api, /asOf:\s*pickS\(r,\s*'as_of',\s*'asOf'\)/);
  assert.match(panel, /期权链来源：\$\{chain\.provider/);
  assert.match(panel, /providerError\?\.retryAfter/);
  assert.match(panel, /disabled=\{retrySeconds > 0 \|\| retrying\}/);
  assert.match(panel, /Yahoo\/yfinance 拉取/);
  assert.match(panel, /providerError\.code === 400[\s\S]*forceExpirationsRef\.current = true;[\s\S]*refreshExpirations\(\);[\s\S]*return;/);
  assert.match(api, /force:\s*readOptions\.force/);
});

test('旧到期日返回 400 后强制刷新新列表并恢复期权链', async () => {
  let cachedExpirations;
  let expirationProviderCalls = 0;
  const calls = [];
  const marketGet = async (url, options = {}) => {
    calls.push({ url, force: options.force === true });
    if (url.endsWith('/expirations')) {
      if (!options.force && cachedExpirations) return cachedExpirations;
      expirationProviderCalls += 1;
      cachedExpirations = {
        expirations:
          expirationProviderCalls === 1
            ? ['2030-08-16']
            : ['2030-08-23'],
      };
      return cachedExpirations;
    }
    if (url.includes('expiration=2030-08-16')) {
      const error = new Error('invalid expiration');
      error.code = 400;
      throw error;
    }
    return {
      ticker: 'AAOI',
      expiration: '2030-08-23',
      underlying_price: 25,
      calls: [{ strike: 25, bid: 1, ask: 1.2 }],
      puts: [{ strike: 25, bid: 0.9, ask: 1.1 }],
      provider: 'Yahoo/yfinance',
    };
  };
  const optionsApi = loadOptionsModule(marketGet);

  const oldList = await optionsApi.expirations('AAOI');
  assert.deepEqual(Array.from(oldList), ['2030-08-16']);
  await assert.rejects(
    optionsApi.chain('AAOI', oldList[0]),
    (error) => error.code === 400,
  );

  const newList = await optionsApi.expirations('AAOI', { force: true });
  assert.deepEqual(Array.from(newList), ['2030-08-23']);
  const recovered = await optionsApi.chain('AAOI', newList[0]);

  assert.equal(recovered.expiration, '2030-08-23');
  assert.equal(recovered.provider, 'Yahoo/yfinance');
  assert.equal(expirationProviderCalls, 2);
  assert.deepEqual(
    calls.filter((call) => call.url.endsWith('/expirations')),
    [
      { url: '/options/AAOI/expirations', force: false },
      { url: '/options/AAOI/expirations', force: true },
    ],
  );
});

test('详情错误只在明确缺失快照时显示手动拉取', async () => {
  const drawer = await source('components/StockDrawerBody.tsx');

  assert.match(drawer, /manualRecovery = publicSnapshotMissing \|\| \(!error && !detail\)/);
  assert.match(drawer, /loginExpired[\s\S]*重新登录/);
  assert.match(drawer, /rateLimited[\s\S]*请求较频繁/);
  assert.match(drawer, /manualRecovery \? \([\s\S]*<ManualStockPull/);
  assert.match(drawer, /onClick=\{refresh\}/);
});

test('手动拉取与手机工具栏保持可点击且不溢出', async () => {
  const manual = await source('components/detail/ManualStockPull.tsx');
  const watchlist = await source('pages/Watchlist.tsx');

  assert.match(manual, /min-h-11/);
  assert.match(manual, /w-full[\s\S]*sm:w-auto/);
  assert.match(watchlist, /min-h-11 flex-wrap/);
  assert.match(watchlist, /w-full text-right[\s\S]*sm:w-auto/);
});
