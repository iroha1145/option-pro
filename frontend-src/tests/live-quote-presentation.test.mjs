import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { createRequire } from 'node:module';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const translate = { t: (text, values = {}) => text.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? '') };
function moduleAt(path, imports) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(source, { exports, require: key => imports[key] ?? require(key),
    Date: class extends Date { static now() { return Date.UTC(2026, 8, 8); } },
  });
  return exports;
}
const quotes = moduleAt('../src/lib/liveQuotes.ts', { '../i18n/core.ts': translate });
let quote;
const live = moduleAt('../src/components/shared/LiveQuote.tsx', {
  '../../i18n/core.ts': translate,
  '@/hooks/useLiveQuote': { useLiveQuote: () => quote, useQuoteStatus: () => ({ connected: true, market_session: 'regular' }) },
  '@/hooks/useTickFlash': { useTickFlash: () => ({}) },
  '@/lib/liveQuotes': quotes,
  '@/lib/screenerScanFlow': { visibleScanDate: value => value?.slice(0, 10) ?? null },
  '@/lib/format': { fmtPrice: value => value.toFixed(2) },
  '@/lib/utils': { cn: (...values) => values.filter(Boolean).join(' ') },
  './NumberTicker': { default: ({ text }) => createElement('span', { 'aria-label': text }, text) },
  './ChangeBadge': { default: ({ value }) => createElement('span', null, value == null ? '—' : `${value}%`) },
});
const render = (component, props) => renderToStaticMarkup(createElement(component, { symbol: 'AAPL', ...props }));
const snapshot = { symbol: 'AAPL', price: 90, previous_close: 100, change: -10, change_pct: -10,
  trade_at: '2026-09-03T20:00:00Z', received_at: '2026-09-03T20:00:00Z', session: 'regular',
  source: 'finnhub', freshness: 'snapshot', subscription_status: 'limited' };

test('invalid fallback prices display a missing value instead of zero or a negative stock price', () => {
  quote = undefined;
  for (const fallback of [0, -10, Number.NaN, Number.POSITIVE_INFINITY, null]) {
    const html = render(live.LivePrice, { fallback });
    assert.match(html, /aria-label="—"/);
    assert.doesNotMatch(html, /扫描价|实时/);
  }
});

test('only screener fallbacks say scan price and a trade date is never called a score date', () => {
  quote = undefined;
  const fallback = { fallback: 100, fallbackAt: '2026-09-04' };
  const general = render(live.LivePrice, fallback);
  assert.match(general, /参考价/);
  assert.doesNotMatch(general, /扫描价|评分依据|定时更新/);
  const scan = render(live.LivePrice, { ...fallback, fallbackKind: 'scan' });
  assert.match(scan, /扫描价 · 日线/);
  assert.match(scan, /2026-09-04/);
  quote = { ...snapshot, freshness: 'live', subscription_status: 'live' };
  const trade = render(live.LivePrice, {});
  assert.match(trade, /报价日期/);
  assert.doesNotMatch(trade, /评分依据|扫描价/);
});

test('a newer fallback price with missing change cannot borrow an older quote percentage', () => {
  quote = snapshot;
  const fallbackAt = '2026-09-04';
  assert.match(render(live.LivePrice, { fallback: 100, fallbackAt }), /aria-label="100.00"/);
  assert.equal(render(live.LiveChange, { fallback: null, fallbackPrice: 100, fallbackAt }), '<span>—</span>');
});

test('new quote price with missing or inconsistent previous-close change shows unknown', () => {
  for (const fields of [{ change_pct: null }, { previous_close: null }, { change_pct: 40 }]) {
    quote = { ...snapshot, freshness: 'live', subscription_status: 'live', ...fields };
    assert.match(render(live.LivePrice, { fallback: 85 }), /aria-label="90.00"/);
    assert.equal(render(live.LiveChange, { fallback: 3, fallbackPrice: 85 }), '<span>—</span>');
  }
});
