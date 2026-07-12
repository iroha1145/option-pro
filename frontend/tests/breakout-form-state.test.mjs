import assert from 'node:assert/strict';
import test from 'node:test';

import {
  BREAKOUT_FILTER_KEYS,
  beginBreakoutRefresh,
  breakoutDegradedView,
  breakoutFailureDomain,
  breakoutFilterSnapshot,
  isRadarPaused,
  isRadarAwaitingFirstSnapshot,
  settleBreakoutRefresh,
  shouldAutoRefreshRadar,
  snapshotsEqual,
  transitionBreakoutHistory,
} from '../static/js/utils/frontendState.js';

test('breakout filter snapshots preserve an unapplied draft without mutating applied values', () => {
  const applied = breakoutFilterSnapshot({ ticker: '', session: '' });
  const draft = breakoutFilterSnapshot({ ...applied, ticker: 'AAPL', session: 'regular' });
  assert.equal(snapshotsEqual(draft, applied, BREAKOUT_FILTER_KEYS), false);
  assert.equal(applied.ticker, '');
  assert.equal(draft.ticker, 'AAPL');
});

test('automatic refresh requests applied filters while retaining live draft, disclosure, and text selection', () => {
  const applied = breakoutFilterSnapshot({ ticker: '', session: 'regular' });
  let interaction = {
    draftFilters: breakoutFilterSnapshot({ ticker: 'AAPL', session: 'regular' }),
    appliedFilters: applied,
    selectedEventId: 'event-42',
    selectedTicker: 'NVDA',
    detailReturnFocusKey: 'event-event-42',
  };
  const refresh = beginBreakoutRefresh(interaction, {
    key: 'breakout-ticker-filter', start: 1, end: 3,
  });

  interaction = {
    ...interaction,
    draftFilters: breakoutFilterSnapshot({ ticker: 'MSFT', session: 'regular' }),
  };
  const settled = settleBreakoutRefresh(interaction, refresh);

  assert.equal(refresh.requestFilters.ticker, '');
  assert.equal(settled.draftFilters.ticker, 'MSFT');
  assert.equal(settled.selectedEventId, 'event-42');
  assert.equal(settled.selectedTicker, 'NVDA');
  assert.equal(settled.detailReturnFocusKey, 'event-event-42');
  assert.deepEqual(settled.focusDescriptor, {
    key: 'breakout-ticker-filter', start: 1, end: 3,
  });
});

test('ticker-history failure and retry do not erase an already loaded event detail', () => {
  const detail = { event: { event_id: 'event-42', ticker: 'NVDA' } };
  const state = {
    detail,
    tickerHistory: null,
    historyLoading: false,
    historyError: '',
  };

  Object.assign(state, transitionBreakoutHistory(state, { type: 'start', reset: true }));
  Object.assign(state, transitionBreakoutHistory(state, {
    type: 'failure', message: '近期事件未能读取。服务暂时不可用。',
  }));
  assert.equal(state.detail, detail);
  assert.equal(state.historyLoading, false);
  assert.match(state.historyError, /近期事件未能读取/);

  Object.assign(state, transitionBreakoutHistory(state, { type: 'start' }));
  assert.equal(state.detail, detail);
  assert.equal(state.historyError, '');
  Object.assign(state, transitionBreakoutHistory(state, {
    type: 'success', payload: { events: [{ event_id: 'event-41' }] },
  }));
  assert.equal(state.detail, detail);
  assert.deepEqual(state.tickerHistory.events, [{ event_id: 'event-41' }]);
});

test('radar pause and automatic refresh decisions are neutral and deterministic', () => {
  assert.equal(isRadarPaused({ worker: { status: 'paused' } }, {}), true);
  assert.equal(isRadarPaused({}, { source_status: { runtime_reason: 'market_closed' } }), true);
  assert.equal(isRadarPaused({ status: 'active' }, { status: 'active' }), false);
  assert.equal(shouldAutoRefreshRadar({ hidden: false, loading: false, refreshing: false, loadingMore: false }), true);
  assert.equal(shouldAutoRefreshRadar({ hidden: true, loading: false, refreshing: false, loadingMore: false }), false);
});

test('degraded radar copy attributes only real provider failures to the data source', () => {
  const provider = breakoutDegradedView('provider', true);
  assert.match(provider.title, /数据源/);

  const local = breakoutDegradedView('local_processing', true);
  assert.match(local.title, /雷达处理/);
  assert.doesNotMatch(local.title, /数据源/);
  assert.match(local.copy, /最近完整快照继续保留/);

  const localWithoutSnapshot = breakoutDegradedView('price_data', false);
  assert.match(localWithoutSnapshot.title, /尚无可靠快照/);
  assert.doesNotMatch(localWithoutSnapshot.title, /数据源/);

  assert.equal(
    breakoutFailureDomain({}, { source_status: { provider: 'degraded' } }),
    'provider',
  );
  assert.equal(
    breakoutFailureDomain({ failure_domain: 'local_processing' }, {
      source_status: { provider: 'active' },
    }),
    'local_processing',
  );

  assert.equal(isRadarAwaitingFirstSnapshot({
    enabled: true,
    runtime_reason: 'completed_snapshot_missing',
    latest_completed_scan: null,
    worker: { status: 'idle' },
  }, {}), true);
  assert.equal(isRadarAwaitingFirstSnapshot({
    enabled: true,
    runtime_reason: 'completed_snapshot_missing',
    latest_completed_scan: null,
    failure_domain: 'price_data',
    worker: { status: 'degraded' },
  }, {}), false);
});
