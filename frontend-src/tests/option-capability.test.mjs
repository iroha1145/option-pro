import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { build } from 'esbuild';
import vm from 'node:vm';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const repo = path.resolve(here, '..', '..');
const src = path.join(root, 'src');

function extractQuotedSymbols(text, marker) {
  const start = text.indexOf(marker);
  assert.ok(start >= 0, `missing ${marker}`);
  const slice = text.slice(start, start + 600);
  return [...slice.matchAll(/['"](\^?[A-Z0-9.]+)['"]/g)]
    .map((match) => match[1])
    .filter((value) => value !== marker && !value.startsWith('yahoo'));
}

const capability = await build({
  stdin: {
    contents: `
      export {
        resolveOptionCapability,
        isDeclaredUnsupported,
        OPTION_CAPABILITY_VERSION,
        YAHOO_UNSUPPORTED_QUOTE_SYMBOLS,
      } from './src/lib/optionCapability.ts';
    `,
    resolveDir: root,
  },
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node',
  alias: { '@': `${root}/src` },
});
const api = await import(
  `data:text/javascript;base64,${Buffer.from(capability.outputFiles[0].text).toString('base64')}`
);

test('前后端期权能力表与版本保持同步', async () => {
  const backend = await readFile(
    path.join(repo, 'backend', 'app', 'services', 'option_capability.py'),
    'utf8',
  );
  const frontend = await readFile(path.join(src, 'lib', 'optionCapability.ts'), 'utf8');
  const publicHome = await readFile(
    path.join(repo, 'backend', 'app', 'public_home_snapshot.py'),
    'utf8',
  );
  const backendSymbols = new Set(extractQuotedSymbols(backend, 'YAHOO_UNSUPPORTED_QUOTE_SYMBOLS'));
  const frontendSymbols = new Set(api.YAHOO_UNSUPPORTED_QUOTE_SYMBOLS);
  const homeMatch = publicHome.match(/PUBLIC_HOME_INDEX_SYMBOLS = \(([^)]+)\)/s);
  assert.ok(homeMatch);
  const homeSymbols = new Set(
    [...homeMatch[1].matchAll(/"([^"]+)"/g)].map((match) => match[1]),
  );

  assert.deepEqual(
    [...backendSymbols].sort(),
    [...frontendSymbols].sort(),
  );
  assert.deepEqual([...homeSymbols].sort(), [...frontendSymbols].sort());
  assert.match(backend, new RegExp(`CAPABILITY_VERSION = "${api.OPTION_CAPABILITY_VERSION}"`));
  assert.match(frontend, /yahoo-index-quotes-v1/);
});

test('能力判断先归一代码，且不用 ^ 前缀一刀切', () => {
  for (const alias of ['SPX', ' spx ', '^SPX', 'GSPC', '^GSPC']) {
    const resolved = api.resolveOptionCapability(alias);
    assert.equal(resolved.ticker, '^GSPC');
    assert.equal(resolved.optionsStatus, 'unsupported_by_provider');
    assert.equal(resolved.retryable, false);
    assert.equal(api.isDeclaredUnsupported(alias), true);
  }
  for (const alias of ['IXIC', '^IXIC', 'DJI', '^DJI', 'N225', '^N225', 'SSE', '000001.SS']) {
    assert.equal(api.isDeclaredUnsupported(alias), true);
  }
  for (const symbol of ['^NDX', '^RUT', '^VIX', 'AAPL', 'SPY', 'QQQ']) {
    assert.equal(api.isDeclaredUnsupported(symbol), false);
    assert.equal(api.resolveOptionCapability(symbol).optionsStatus, 'unknown');
    assert.equal(api.resolveOptionCapability(symbol).retryable, true);
  }
});

test('期权面板在声明不支持时不挂载请求钩子，详情按标的绑定', async () => {
  const panel = await readFile(path.join(src, 'components', 'detail', 'OptionsPanel.tsx'), 'utf8');
  const detail = await readFile(path.join(src, 'pages', 'StockDetail.tsx'), 'utf8');
  const apiSource = await readFile(path.join(src, 'api', 'modules', 'options.ts'), 'utf8');
  const dict = await readFile(path.join(src, 'i18n', 'dict', 'detail.ts'), 'utf8');

  const defaultExport = panel.slice(panel.indexOf('export default function OptionsPanel'));
  const livePanel = defaultExport.slice(0, defaultExport.indexOf('function LiveOptionsPanel'));
  assert.match(livePanel, /isDeclaredUnsupported\(ticker\)/);
  assert.match(livePanel, /return <UnsupportedIndexOptions/);
  assert.doesNotMatch(livePanel, /usePolling/);
  assert.match(panel, /function LiveOptionsPanel/);
  assert.match(detail, /<OptionsPanel key=\{detail\.ticker\} ticker=\{detail\.ticker\} \/>/);
  assert.match(apiSource, /if \(isDeclaredUnsupported\(ticker\)\)/);
  assert.match(dict, /当前数据源未提供该指数的期权数据/);
  assert.doesNotMatch(panel, /yfinance/);
  assert.doesNotMatch(dict, /yfinance/);
});

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
        pickN: () => null,
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
      return { getUnusualOptions: () => [], getOptionExpirations: () => [], getOptionChain: () => ({}) };
    }
    if (id === '@/lib/optionCapability') {
      return {
        isDeclaredUnsupported: api.isDeclaredUnsupported,
      };
    }
    throw new Error(`unexpected import: ${id}`);
  };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require,
    URLSearchParams,
    Promise,
  });
  return module.exports.optionsApi;
}

test('声明不支持的指数在请求层不发起到期日或链读取', async () => {
  const calls = [];
  const marketGet = async (url) => {
    calls.push(url);
    throw new Error(`unexpected marketGet ${url}`);
  };
  const optionsApi = loadOptionsModule(marketGet);
  const expirations = await optionsApi.expirations('SPX');
  const chain = await optionsApi.chain('^GSPC', '2030-08-16');
  assert.deepEqual(expirations, {
    expirations: [],
    optionsStatus: 'unsupported_by_provider',
    retryable: false,
  });
  assert.equal(chain.rows.length, 0);
  assert.deepEqual(calls, []);
});
