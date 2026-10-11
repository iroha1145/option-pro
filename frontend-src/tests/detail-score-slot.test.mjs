// 个股页头：评分块没有分数时也留在版面里（看不见、读屏跳过），分数晚到不会把页头撑高、整页下移；
// 小指标组各项预留常见读数的宽度、时段指示灯等详情读到后再出现，数据到达时可见元素不横向挪位。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { cn } from '../src/lib/utils.ts';

const file = fileURLToPath(new URL('../src/components/detail/PriceHeader.tsx', import.meta.url));
const jsx = (type, props, key) => ({ type, props, key });
const market = { value: null }; // what the market-status poll returns; null until a test sets it
const imports = {
  'react/jsx-runtime': { jsx, jsxs: jsx },
  '@/components/shared/SoftBadge': { default: 'SoftBadge' },
  '@/components/shared/SameWidth': { default: 'SameWidth' },
  '@/hooks/useLiveQuote': { useLiveQuote: () => null, useQuoteStatus: () => ({}) },
  '@/components/shared/LiveQuote': { LivePrice: 'LivePrice' },
  '@/lib/liveQuotes': { displayedQuoteLabel: () => null, preferLiveQuote: () => false, delayedSessionLabel: (session) => `${session} · delayed 15 min` },
  '@/api/modules/market': { marketApi: { status: () => Promise.resolve(null) } },
  '@/hooks/usePolling': { usePolling: () => ({ data: market.value }) },
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

test('the stats group joins the price row only from lg, so the loaded header is never taller than the skeleton', () => {
  // The real price and change are wider than the skeleton's dashes. If the stats group could share the price row below lg, it would fit
  // while loading and drop under the price once the data arrived (+63 px at 640-672 px, and wider with English strings or big prices). So below lg it is always a full row of its own.
  const details = [
    undefined,
    { ticker: 'NVDA', name: 'NVIDIA Corporation', price: 188.12, change: 2.3, changePct: 1.2, volume: 1234567, marketCap: 4.5e12, strengthScore: 72.5 },
  ];
  for (const detail of details) {
    const tree = PriceHeader({ symbol: 'NVDA', detail });
    const stats = find(tree, (node) => node.props?.['data-price-header-stats'] !== undefined);
    assert.ok(stats, 'stats group missing');
    const classes = stats.props.className.split(/\s+/);
    assert.ok(classes.includes('w-full'), 'full width below lg: a line of its own next to a price of any width');
    assert.ok(classes.includes('lg:w-auto'), 'content width from lg up, beside the price');
    assert.deepEqual(classes.filter((name) => /^(?:sm:)?w-/.test(name) && name !== 'w-full'), [], 'no sm-level width that lets it share the price row');
  }
});

const statsOf = (detail) => find(PriceHeader({ symbol: 'NVDA', detail }), (node) => node.props?.['data-price-header-stats'] !== undefined);
const valueCell = (item) => find(item, (node) => node.type === 'dd').props.children;

test('each stats item reserves the width of its widest common reading, so data arriving neither widens an item nor moves the group', () => {
  // Measured in zh/en/ja on the live preview: "—" -> "19.0M" / "$2.10B" / "19:59:00 · 休市" made the group 73 px wider (the right-aligned
  // group's left edge moved left by that much) and pushed the quote-time item 30 px right at 660 px.
  for (const detail of [undefined, { ticker: 'NVDA', price: 188.12, volume: 1234567, marketCap: 4.5e12 }]) {
    const [volume, cap, quote] = statsOf(detail).props.children;
    for (const [item, widest] of [[volume, '888.8M'], [cap, '$888.88B']]) {
      const cell = valueCell(item);
      assert.equal(cell.type, 'SameWidth');
      assert.deepEqual(JSON.parse(JSON.stringify(cell.props.samples)), [widest]); // digits are tabular, so all eights is the widest reading of that length
      assert.equal(cell.props.sampleClassName, 'max-sm:hidden'); // phones: a two-column grid sets the width, nothing to reserve
    }
    const line = valueCell(quote);
    assert.equal(line.type, 'SameWidth');
    assert.equal(line.props.sampleClassName, 'max-sm:hidden');
    // the delayed wordings visitors read most: pre-market, regular hours, after-hours, each behind the widest time
    const wordings = line.props.samples.map((sample) => sample.props.children[1]);
    assert.deepEqual([...wordings].sort(), [' · postmarket · delayed 15 min', ' · premarket · delayed 15 min', ' · regular · delayed 15 min']);
    for (const sample of line.props.samples) assert.equal(sample.props.children[0].props.children, '00:00:00');
    // The time has its own cell on every width, so "—" turning into 19:59:00 never pushes the wording after it.
    const [timeCell, wording] = line.props.children;
    assert.equal(timeCell.type, 'SameWidth');
    assert.equal(timeCell.props.sampleClassName, undefined);
    assert.equal(timeCell.props.samples[0].props.children, '00:00:00');
    assert.match(wording.props.className, /\btext-ink-500\b/);
  }
});

test('the session LED waits for the detail from sm up: the sector badge changes width when the detail arrives and would push it', () => {
  market.value = { session: 'regular', label: '盘中' };
  try {
    const ledOf = (detail) => find(PriceHeader({ symbol: 'NVDA', detail }), (node) => node.type === 'SessionLED');
    const waiting = ledOf(undefined).props.className;
    assert.match(waiting, /(^|\s)sm:invisible(\s|$)/); // keeps its place, not painted
    assert.doesNotMatch(waiting, /(^|\s)invisible/); // phones: the LED sits on its own row below the badge and is shown as before
    for (const detail of [{ ticker: 'NVDA', sector: 'Semiconductors' }, { ticker: 'NVDA' }]) {
      assert.doesNotMatch(ledOf(detail).props.className ?? '', /invisible/);
    }
  } finally {
    market.value = null;
  }
});

test('a score of zero is still a score', () => {
  const slot = slotOf({ symbol: 'NVDA', detail: { ticker: 'NVDA', strengthScore: 0 } });
  assert.doesNotMatch(slot.props.className, /\binvisible\b/);
  assert.equal(slot.props['aria-hidden'], undefined);
  assert.equal(barOf(slot).props.score, 0);
});
