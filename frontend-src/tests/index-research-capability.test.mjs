import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { isIndexSymbol, quoteSymbol } from '../src/lib/quoteSymbol.ts';

function panel(name) {
  const requests = [];
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(new URL(`../src/components/detail/${name}.tsx`, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const jsx = (type, props) => typeof type === 'function' ? type(props) : ({ type, props });
  vm.runInNewContext(code, { module, exports: module.exports, Date,
    require(id) {
      if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx };
      if (id === '@/lib/quoteSymbol') return { isIndexSymbol, quoteSymbol };
      if (id.includes('i18n/core')) return { t: value => value };
      if (id === '@/hooks/usePolling') return { usePolling: fetcher => { void fetcher(); return { data: [], loading: false, error: null }; } };
      if (id === '@/hooks/useAccess') return { useAccess: () => ({ isOwner: true }) };
      if (id === '@/hooks/useNow') return { useNow: () => Date.now() };
      if (id === '@/api/modules/signals') return { signalsApi: { stock: ticker => { requests.push(['signals', ticker]); return Promise.resolve([]); } } };
      if (id === '@/api/modules/breakouts') return { breakoutsApi: { byTicker: ticker => { requests.push(['breakouts', ticker]); return Promise.resolve([]); } } };
      if (id === '@/api/modules/catalysts') return { catalystsApi: { byTicker: ticker => { requests.push(['news', ticker]); return Promise.resolve([]); } } };
      if (id === '@/api/modules/earnings') return { earningsApi: { impact: ticker => { requests.push(['earnings', ticker]); return Promise.resolve(null); } } };
      return new Proxy({}, { get: () => (props => props) });
    } });
  return { render: ticker => module.exports.default({ ticker }), requests };
}

test('index aliases keep their true symbols and suppress only incompatible company panels before any request', () => {
  for (const ticker of ['^GSPC', 'spx', '^NDX', '^RUT', 'SSE', '000001.SS']) {
    assert.equal(isIndexSymbol(ticker), true);
    for (const name of ['SidebarEvents', 'NewsPanel']) {
      const h = panel(name); const output = h.render(ticker);
      assert.ok(output); assert.deepEqual(h.requests, [], `${name} must not issue company requests for ${ticker}`);
    }
  }
  assert.equal(quoteSymbol('spx'), '^GSPC'); assert.notEqual(quoteSymbol('spx'), 'SPY');
});

test('stocks and ETFs still request their own news, earnings and radar without changing the ticker', () => {
  for (const ticker of ['AAPL', 'SPY', 'BRK.B']) {
    assert.equal(isIndexSymbol(ticker), false);
    const radar = panel('SidebarEvents'); radar.render(ticker);
    assert.deepEqual(radar.requests, [['breakouts', ticker]]);
    const news = panel('NewsPanel'); news.render(ticker);
    assert.deepEqual(news.requests, [['news', ticker], ['earnings', ticker]]);
  }
});


test('index signal lists retain technical signal requests while suppressing the separate stock radar request', () => {
  for (const ticker of ['^GSPC', '^NDX', 'SSE']) {
    const h = panel('SignalList'); h.render(ticker);
    assert.deepEqual(h.requests, [['signals', ticker]]);
  }
  const h = panel('SignalList'); h.render('AAPL');
  assert.deepEqual(h.requests, [['signals', 'AAPL'], ['breakouts', 'AAPL']]);
});
