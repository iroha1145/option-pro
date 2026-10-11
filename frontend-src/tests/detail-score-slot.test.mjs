// 个股页头的评分块：没有分数时也留在版面里（看不见、读屏跳过），分数晚到不会把页头撑高、整页下移。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { cn } from '../src/lib/utils.ts';

const file = fileURLToPath(new URL('../src/components/detail/PriceHeader.tsx', import.meta.url));
const jsx = (type, props, key) => ({ type, props, key });
const imports = {
  'react/jsx-runtime': { jsx, jsxs: jsx },
  '@/components/shared/SoftBadge': { default: 'SoftBadge' },
  '@/hooks/useLiveQuote': { useLiveQuote: () => null, useQuoteStatus: () => ({}) },
  '@/components/shared/LiveQuote': { LivePrice: 'LivePrice' },
  '@/lib/liveQuotes': { displayedQuoteLabel: () => null, preferLiveQuote: () => false },
  '@/api/modules/market': { marketApi: { status: () => Promise.resolve(null) } },
  '@/hooks/usePolling': { usePolling: () => ({ data: null }) },
  '@/lib/utils': { cn },
  '@/lib/format': { fmtCompact: String, fmtTimeHHMMSS: String },
  '@/components/shared/TickerLogo': { default: 'TickerLogo' },
  '@/components/shared/InsightCard': { InsightValue: 'InsightValue' },
  '@/components/shared/SessionLED': { default: 'SessionLED' },
  '@/components/shared/StrengthBar': { default: 'StrengthBar' },
  '@/components/shared/SameWidth': { default: 'SameWidth' },
  '@/components/shared/InfoHint': { default: 'InfoHint' },
  '@/lib/scoreHints': { SCORE_HINTS: { strengthComposite: 'hint' } },
  '../../i18n/core.ts': { t: (text) => text },
};

function loadPriceHeader() {
  const module = { exports: {} };
  const { outputText } = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  vm.runInNewContext(outputText, {
    module, exports: module.exports,
    require(id) {
      if (Object.hasOwn(imports, id)) return imports[id];
      throw new Error(`Unexpected import ${id}`);
    },
  });
  return module.exports.default;
}

function find(node, match) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) return node.map((child) => find(child, match)).find(Boolean) ?? null;
  if (match(node)) return node;
  return find(node.props?.children, match);
}

const PriceHeader = loadPriceHeader();
const slotOf = (props) => find(PriceHeader(props), (node) => node.type === 'div' && /\bml-auto\b/.test(node.props?.className ?? ''));
const barOf = (slot) => find(slot, (node) => node.type === 'StrengthBar');
const reserveOf = (slot) => find(slot, (node) => node.type === 'SameWidth');

test('the score block stays in the header layout when there is no score yet', () => {
  const withScore = slotOf({ symbol: 'NVDA', detail: { ticker: 'NVDA', strengthScore: 83.61 } });
  assert.ok(withScore);
  assert.doesNotMatch(withScore.props.className, /\binvisible\b/);
  assert.equal(withScore.props['aria-hidden'], undefined);
  assert.equal(barOf(withScore).props.score, 83.61);

  for (const detail of [undefined, null, { ticker: 'NVDA' }, { ticker: 'NVDA', strengthScore: null }, { ticker: 'NVDA', strengthScore: Number.NaN }]) {
    const hidden = slotOf({ symbol: 'NVDA', detail });
    assert.ok(hidden, `slot missing for ${JSON.stringify(detail)}`);
    assert.match(hidden.props.className, /\binvisible\b/);
    assert.equal(hidden.props['aria-hidden'], true);
    // Same box as the visible one: only visibility differs, so nothing moves when the score arrives.
    assert.equal(hidden.props.className, cn(withScore.props.className, 'invisible'));
  }
});

test('the score block is as wide as the widest score, so a one- or two-digit score never changes where the row wraps', () => {
  for (const detail of [undefined, { ticker: 'NVDA', strengthScore: 5 }, { ticker: 'NVDA', strengthScore: 72.5 }]) {
    const reserve = reserveOf(slotOf({ symbol: 'NVDA', detail }));
    assert.ok(reserve, `width reserve missing for ${JSON.stringify(detail)}`);
    assert.equal(reserve.props.samples.length, 1);
    assert.equal(reserve.props.samples[0].type, 'StrengthBar');
    assert.equal(reserve.props.samples[0].props.score, 100); // "100.0" is the longest score text
  }
});

test('a score of zero is still a score', () => {
  const slot = slotOf({ symbol: 'NVDA', detail: { ticker: 'NVDA', strengthScore: 0 } });
  assert.doesNotMatch(slot.props.className, /\binvisible\b/);
  assert.equal(slot.props['aria-hidden'], undefined);
  assert.equal(barOf(slot).props.score, 0);
});
