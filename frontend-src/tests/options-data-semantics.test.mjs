import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { t } from '../src/i18n/core.ts';
import { buildOptionAlertEvidence } from '../src/components/detail/optionAnalysis.ts';

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');

// Run the actual mapping and fixture generators; isolate transport so these
// checks never contact a provider or create a paid analysis task.
function optionsRuntime(body = {}, mock = false) {
  const modules = new Map();
  function load(relativePath) {
    if (modules.has(relativePath)) return modules.get(relativePath);
    const module = { exports: {} };
    modules.set(relativePath, module.exports);
    const compiled = ts.transpileModule(fs.readFileSync(path.join(src, relativePath), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const require = (id) => {
      if (id.endsWith('/i18n/core.ts')) return { t };
      if (id === '@/api/client' || id === '../client') return {
        ApiError: class ApiError extends Error {},
        get: async () => body,
        mockOr: (fixture, live) => mock ? fixture() : live(),
        toQuery: () => '',
      };
      if (id === '../marketRead') return { marketGet: async () => body };
      if (id === './fixtures') return {
        SIGNAL_LABELS: load('lib/signalLabels.ts').SIGNAL_LABELS,
        WATCHLIST_TICKERS: [],
      };
      const target = id.startsWith('@/')
        ? id.slice(2)
        : path.join(path.dirname(relativePath), id);
      return load(target.endsWith('.ts') ? target : `${target}.ts`);
    };
    vm.runInNewContext(compiled, { module, exports: module.exports, require });
    return module.exports;
  }
  return load('api/modules/options.ts').optionsApi;
}

test('真实链隐波保留小数单位，零值、缺失和超过100%的隐波不被混淆', async () => {
  const api = optionsRuntime({
    calls: [
      { strike: 100, implied_volatility: 0.425 },
      { strike: 105, implied_volatility: 1.5 },
      { strike: 110, implied_volatility: 0 },
      { strike: 115 },
      { strike: 120, implied_volatility: null },
    ],
    puts: [{ strike: 100, implied_volatility: 0.38 }],
  });
  const chain = await api.chain('NVDA', '2030-08-16');
  assert.deepEqual(Array.from(chain.rows, row => row.callIv), [0.425, 1.5, 0, null, null]);
  assert.equal(chain.rows[0].putIv, 0.38);
  assert.equal(chain.rows[1].putIv, null, '未提供的看跌合约不产生隐波数据');
});

test('演示链和真实链使用同一隐波单位，演示值不被放大100倍', async () => {
  const demoApi = optionsRuntime({}, true);
  for (const ticker of ['NVDA', 'TSLA', 'AAPL', 'AMD', 'AMZN', 'META', 'MSFT', 'SPY', 'QQQ', 'GOOGL']) {
    const chain = await demoApi.chain(ticker, '2030-08-16');
    assert.equal(chain.rows.length, 16);
    for (const row of chain.rows) {
      assert.ok(row.callIv > 0.2 && row.callIv < 1, `${ticker} call IV should be a decimal fraction`);
      assert.ok(row.putIv > 0.2 && row.putIv < 1, `${ticker} put IV should be a decimal fraction`);
    }
    const normalized = await optionsRuntime({
      underlying_price: chain.spot,
      calls: chain.rows.map(row => ({ strike: row.strike, implied_volatility: row.callIv })),
      puts: chain.rows.map(row => ({ strike: row.strike, implied_volatility: row.putIv })),
    }).chain(ticker, chain.expiration);
    assert.deepEqual(
      Array.from(normalized.rows, row => [row.callIv, row.putIv]),
      Array.from(chain.rows, row => [row.callIv, row.putIv]),
    );
  }
});

test('演示异动不从看涨或看跌合约类型推断交易方向', async () => {
  const rows = await optionsRuntime({}, true).unusual();
  assert.ok(rows.length > 0);
  assert.ok(rows.some(row => row.side === 'call'));
  assert.ok(rows.some(row => row.side === 'put'));
  assert.ok(rows.every(row => row.sentiment === 'neutral'));
});

test('3倍量持比进入观察证据，但不产生看涨买入或开仓结论', () => {
  const [alert] = buildOptionAlertEvidence({
    ticker: 'NVDA', expiration: '2030-08-16', spot: 102.5,
    rows: [{
      strike: 102.5,
      callVol: 300, callOi: 100, callIv: 0.42, callBid: 1, callAsk: 1.2,
      putVol: null, putOi: null, putIv: null, putBid: null, putAsk: null,
    }],
  }, '2030-08-16', 10);
  assert.ok(alert);
  assert.equal(alert.vol_oi_ratio, 3);
  assert.equal(alert.strike, 102.5);
  assert.equal(alert.implied_volatility, 0.42);
  assert.equal(alert.direction, 'unknown');
  assert.equal(alert.direction_confidence, 0);
  assert.equal(alert.signal, 'unknown');
  assert.doesNotMatch(alert.reasons.join(' '), /看涨买入|全部.*新开仓/);
});
