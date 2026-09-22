import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { formatWatchScore, mainBoardRows, normalizedHorizon, researchWatchView, visibleResearchWatch } from '../src/lib/researchWatchGroups.ts';

const watchPayload = {
  enabled: true,
  displaces_main_board: false,
  high_volatility_collapsed: true,
  qualification_note: '观察分组不进入主榜，不授予严格资格，也不表示可以买入。',
  source_date: '2024-03-28',
  profile: 'balanced',
  horizon: 'mid',
  protocol: 'research_watch_layers_v1',
  source: 'synthetic_watch_layers_v1',
  extension_total: 2,
  high_volatility_total: 1,
  display_limit: 1,
  filter_scope: 'global_reference',
  filter_scope_note: '全局参考，未按当前行业或最低价筛选。',
  extension_watch: [{
    security_id: 'ZZZ',
    score: 99.26,
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
};

test('unwired payloads do not create watch groups', () => {
  assert.equal(researchWatchView(undefined), null);
  assert.equal(researchWatchView({ enabled: false }), null);
  assert.equal(researchWatchView({ enabled: true, displaces_main_board: true }), null);
  assert.equal(researchWatchView({ ...watchPayload, profile: undefined }), null);
  assert.equal(researchWatchView({ ...watchPayload, protocol: 'other' }), null);
});

test('watch names stay off the main board and are not marked tradable', () => {
  const rows = [{ ticker: 'AAA', strengthScore: 10 }];
  const view = researchWatchView(watchPayload);
  assert.equal(mainBoardRows(rows, view), rows);
  assert.deepEqual(rows.map((row) => row.ticker), ['AAA']);
  assert.equal(view.extension[0].securityId, 'ZZZ');
  assert.equal(view.extension[0].qualified, false);
  assert.equal(view.extension[0].tradable, false);
  assert.equal(view.extension[0].score, 99.26);
  assert.equal(formatWatchScore(view.extension[0].score), '99.3');
  assert.equal(formatWatchScore(98), '98.0');
  assert.equal(formatWatchScore(null), '—');
  assert.deepEqual(view.extension[0].rejectionReasons, ['EXTENDED']);
  assert.equal(view.highVolatilityCollapsed, true);
  assert.equal(view.highVolatility[0].securityId, 'HHH');
  assert.equal(view.displacesMainBoard, false);
  assert.equal(view.profile, 'balanced');
  assert.equal(view.horizon, 'mid');
  assert.equal(view.extensionTotal, 2);
  assert.equal(view.filterScopeNote, '全局参考，未按当前行业或最低价筛选。');
  assert.equal(normalizedHorizon('all'), 'mid');
  assert.equal(normalizedHorizon(undefined), 'mid');
  assert.equal(researchWatchView(watchPayload, { profile: 'balanced', timeframe: 'all', sessionDate: '2024-03-28' }).horizon, 'mid');
  assert.equal(researchWatchView(watchPayload, { profile: 'aggressive', timeframe: 'mid' }), null);
  assert.equal(researchWatchView(watchPayload, { profile: 'balanced', timeframe: 'long' }), null);
  assert.equal(visibleResearchWatch(view, 'balanced', 'mid'), view);
  assert.equal(visibleResearchWatch(view, 'balanced', 'long'), null);
  assert.equal(visibleResearchWatch(view, 'aggressive', 'all'), null);
});

test('the screener renders watch groups from the optional view and keeps quote symbols on the main page', () => {
  const screener = fs.readFileSync(new URL('../src/pages/Screener.tsx', import.meta.url), 'utf8');
  const panel = fs.readFileSync(new URL('../src/components/screener/ResearchWatchGroups.tsx', import.meta.url), 'utf8');
  assert.match(screener, /const watchView = visibleResearchWatch\(scanMeta\?\.researchWatchGroups \?\? null, applied\.profile, applied\.timeframe\)/);
  assert.match(screener, /const boardRows = mainBoardRows\(pageRows, watchView\)/);
  assert.match(screener, /useQuoteSymbols\(pageRows\.map/);
  assert.match(screener, /\{watchView \? <ResearchWatchGroups view=\{watchView\} \/> : null\}/);
  assert.doesNotMatch(panel, /useQuoteSymbols|pageRows|setRows/);
  assert.match(panel, /highVolatilityCollapsed \? \{\} : \{ open: true \}/);
  assert.match(panel, /formatWatchScore\(row\.score\)/);
  assert.match(panel, /显示 \{shown\} \/ 共 \{total\}/);
});
