import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { mainBoardRows, researchWatchView } from '../src/lib/researchWatchGroups.ts';

test('unwired payloads do not create watch groups', () => {
  assert.equal(researchWatchView(undefined), null);
  assert.equal(researchWatchView({ enabled: false }), null);
  assert.equal(researchWatchView({ enabled: true, displaces_main_board: true }), null);
});

test('watch names stay off the main board and are not marked tradable', () => {
  const rows = [{ ticker: 'AAA', strengthScore: 10 }];
  const view = researchWatchView({
    enabled: true,
    displaces_main_board: false,
    high_volatility_collapsed: true,
    qualification_note: '观察分组不进入主榜，不授予严格资格，也不表示可以买入。',
    source_date: '2024-03-28',
    extension_watch: [{
      security_id: 'ZZZ',
      score: 99,
      rejection_reasons: ['EXTENDED'],
      qualified: true,
      tradable: true,
      source_date: '2024-03-28',
    }],
    high_volatility_watch: [{
      security_id: 'HHH',
      score: 98,
      rejection_reasons: ['HIGH_ATR'],
      source_date: '2024-03-28',
    }],
  });
  assert.equal(mainBoardRows(rows, view), rows);
  assert.deepEqual(rows.map((row) => row.ticker), ['AAA']);
  assert.equal(view.extension[0].securityId, 'ZZZ');
  assert.equal(view.extension[0].qualified, false);
  assert.equal(view.extension[0].tradable, false);
  assert.equal(view.extension[0].score, 99);
  assert.deepEqual(view.extension[0].rejectionReasons, ['EXTENDED']);
  assert.equal(view.highVolatilityCollapsed, true);
  assert.equal(view.highVolatility[0].securityId, 'HHH');
  assert.equal(view.displacesMainBoard, false);
});

test('the screener renders watch groups from the optional view and keeps quote symbols on the main page', () => {
  const screener = fs.readFileSync(new URL('../src/pages/Screener.tsx', import.meta.url), 'utf8');
  const panel = fs.readFileSync(new URL('../src/components/screener/ResearchWatchGroups.tsx', import.meta.url), 'utf8');
  assert.match(screener, /const boardRows = mainBoardRows\(pageRows, watchView\)/);
  assert.match(screener, /useQuoteSymbols\(pageRows\.map/);
  assert.match(screener, /\{watchView \? <ResearchWatchGroups view=\{watchView\} \/> : null\}/);
  assert.doesNotMatch(panel, /useQuoteSymbols|pageRows|setRows/);
  assert.match(panel, /highVolatilityCollapsed \? \{\} : \{ open: true \}/);
});
