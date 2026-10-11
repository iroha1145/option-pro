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

test('the first row does not depend on the name: one-line title, score block on its own row below sm', () => {
  const details = [
    undefined, // the loading skeleton only knows the ticker
    { ticker: 'NVDA', name: 'NVIDIA Corporation', strengthScore: 72.5 },
    { ticker: 'NVDA', name: 'Taiwan Semiconductor Manufacturing Company Limited' },
  ];
  for (const detail of details) {
    const tree = PriceHeader({ symbol: 'NVDA', detail });
    const h1 = find(tree, (node) => node.type === 'h1');
    const [symbolText, nameText] = h1.props.children;
    assert.doesNotMatch(h1.props.className, /\bflex-wrap\b/); // never a second title line
    assert.match(symbolText.props.className, /\bshrink-0\b/);
    assert.match(nameText.props.className, /\btruncate\b/); // a long name is cut, not wrapped
    const nameBlock = find(tree, (node) => node.type === 'div' && /\bflex-1\b/.test(node.props?.className ?? ''));
    assert.ok(nameBlock, 'the name block takes the remaining width, so its content never pushes the score block');
    const slot = slotOf({ symbol: 'NVDA', detail });
    assert.match(slot.props.className, /\bbasis-full\b/); // own row on phones
    assert.match(slot.props.className, /\bsm:basis-auto\b/); // same row from sm up
  }
});

test('the stats group joins the price row only from md, so the loaded header is never taller than the skeleton', () => {
  // The real price and change are wider than the skeleton's dashes. If the stats group could share the price row below md, it would fit
  // while loading and drop under the price once the data arrived (+63 px at 640-672 px). So below md it is always a full row of its own.
  const details = [
    undefined,
    { ticker: 'NVDA', name: 'NVIDIA Corporation', price: 188.12, change: 2.3, changePct: 1.2, volume: 1234567, marketCap: 4.5e12, strengthScore: 72.5 },
  ];
  for (const detail of details) {
    const tree = PriceHeader({ symbol: 'NVDA', detail });
    const stats = find(tree, (node) => node.props?.['data-price-header-stats'] !== undefined);
    assert.ok(stats, 'stats group missing');
    const classes = stats.props.className.split(/\s+/);
    assert.ok(classes.includes('w-full'), 'full width below md: a line of its own next to a price of any width');
    assert.ok(classes.includes('md:w-auto'), 'content width from md up, beside the price');
    assert.deepEqual(classes.filter((name) => /^(?:sm:)?w-/.test(name) && name !== 'w-full'), [], 'no sm-level width that lets it share the price row');
  }
});

test('a score of zero is still a score', () => {
  const slot = slotOf({ symbol: 'NVDA', detail: { ticker: 'NVDA', strengthScore: 0 } });
  assert.doesNotMatch(slot.props.className, /\binvisible\b/);
  assert.equal(slot.props['aria-hidden'], undefined);
  assert.equal(barOf(slot).props.score, 0);
});
