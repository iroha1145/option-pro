export function snapshotFields(source, keys) {
  const snapshot = {};
  for (const key of keys) snapshot[key] = source?.[key] ?? '';
  return Object.freeze(snapshot);
}

export function snapshotsEqual(left, right, keys) {
  return keys.every((key) => (left?.[key] ?? '') === (right?.[key] ?? ''));
}

export const STRENGTH_FILTER_KEYS = Object.freeze([
  'timeframe',
  'profile',
  'top',
  'sectorId',
  'minPrice',
  'minAvgDollarVolume',
]);

export function strengthFilterSnapshot(source = {}) {
  const timeframe = ['all', 'short', 'mid', 'long'].includes(source.timeframe)
    ? source.timeframe
    : 'all';
  const profile = ['balanced', 'conservative', 'aggressive'].includes(source.profile)
    ? source.profile
    : 'balanced';
  const requestedTop = Number(source.top);
  const top = [20, 30, 50].includes(requestedTop) ? requestedTop : 20;
  const minPrice = Number(source.minPrice);
  const minAvgDollarVolume = Number(source.minAvgDollarVolume);
  return Object.freeze({
    timeframe,
    profile,
    top,
    sectorId: String(source.sectorId || ''),
    minPrice: Number.isFinite(minPrice) && minPrice >= 0 ? minPrice : 5,
    minAvgDollarVolume: Number.isFinite(minAvgDollarVolume) && minAvgDollarVolume >= 0
      ? minAvgDollarVolume
      : 10_000_000,
  });
}

export function strengthRequestParams(filters) {
  const snapshot = strengthFilterSnapshot(filters);
  return Object.freeze({
    timeframe: snapshot.timeframe,
    profile: snapshot.profile,
    top: snapshot.top,
    sector_id: snapshot.sectorId,
    min_price: snapshot.minPrice,
    min_avg_dollar_volume: snapshot.minAvgDollarVolume,
  });
}

export function updateStrengthDraftFilters(filters, patch = {}) {
  return strengthFilterSnapshot({ ...filters, ...patch });
}

export function beginStrengthFilterRequest(current = {}, options = {}) {
  const requestFilters = strengthFilterSnapshot(
    options.filters ?? current.appliedFilters ?? current.draftFilters,
  );
  return Object.freeze({
    appliedFilters: options.applyDraft
      ? requestFilters
      : strengthFilterSnapshot(current.appliedFilters ?? requestFilters),
    requestFilters,
    requestParams: strengthRequestParams(requestFilters),
  });
}

export function settleStrengthFilterRequest(current = {}, requestFilters = {}) {
  return Object.freeze({
    // A completed request describes its own result snapshot. It must never
    // replace a newer draft that the user edited while the request was active.
    draftFilters: strengthFilterSnapshot(current.draftFilters),
    resultFilters: strengthFilterSnapshot(requestFilters),
  });
}

export const BREAKOUT_FILTER_KEYS = Object.freeze([
  'date',
  'ticker',
  'setup_type',
  'lifecycle_state',
  'session',
  'min_priority',
]);

export function breakoutFilterSnapshot(source = {}) {
  return snapshotFields(source, BREAKOUT_FILTER_KEYS);
}

function focusDescriptorSnapshot(descriptor) {
  if (!descriptor?.key) return null;
  return Object.freeze({
    key: String(descriptor.key),
    start: Number.isInteger(descriptor.start) ? descriptor.start : null,
    end: Number.isInteger(descriptor.end) ? descriptor.end : null,
  });
}

export function beginBreakoutRefresh(current = {}, focusDescriptor = null) {
  return Object.freeze({
    requestFilters: breakoutFilterSnapshot(current.appliedFilters),
    focusDescriptor: focusDescriptorSnapshot(focusDescriptor),
  });
}

export function settleBreakoutRefresh(current = {}, refreshContext = {}) {
  return Object.freeze({
    // Keep live interaction state. Only the event payload belongs to the
    // completed request; draft filters and disclosure selection may have moved
    // on while it was in flight.
    draftFilters: breakoutFilterSnapshot(current.draftFilters),
    selectedEventId: String(current.selectedEventId || ''),
    selectedTicker: String(current.selectedTicker || ''),
    detailReturnFocusKey: String(current.detailReturnFocusKey || ''),
    focusDescriptor: focusDescriptorSnapshot(refreshContext.focusDescriptor),
  });
}

export function transitionBreakoutHistory(current = {}, action = {}) {
  if (action.type === 'start') {
    return Object.freeze({
      tickerHistory: action.reset ? null : (current.tickerHistory ?? null),
      historyLoading: true,
      historyError: '',
    });
  }
  if (action.type === 'success') {
    return Object.freeze({
      tickerHistory: action.payload ?? null,
      historyLoading: false,
      historyError: '',
    });
  }
  if (action.type === 'failure') {
    return Object.freeze({
      tickerHistory: current.tickerHistory ?? null,
      historyLoading: false,
      historyError: String(action.message || '近期事件未能读取。'),
    });
  }
  return Object.freeze({
    tickerHistory: current.tickerHistory ?? null,
    historyLoading: Boolean(current.historyLoading),
    historyError: String(current.historyError || ''),
  });
}

export function refreshRemainingMs(lastStartedAt, now, intervalMs) {
  if (!Number.isFinite(lastStartedAt) || lastStartedAt <= 0) return 0;
  return Math.max(0, Math.ceil(lastStartedAt + intervalMs - now));
}

export function createWatchlistInitialization(existingTickers) {
  const phase = existingTickers === undefined
    ? 'uninitialized'
    : existingTickers === null
      ? 'initializing'
      : existingTickers.length === 0
        ? 'explicitly_empty'
        : 'initialized';
  return Object.freeze({
    phase,
    pending: Object.freeze([]),
  });
}

export function stageWatchlistTicker(initialization, ticker) {
  const normalized = String(ticker || '').trim().toUpperCase();
  const pending = [...(initialization?.pending || [])];
  if (normalized && !pending.includes(normalized)) pending.push(normalized);
  const phase = !initialization?.phase || initialization.phase === 'uninitialized'
    ? 'initializing'
    : initialization.phase;
  return Object.freeze({ phase, pending: Object.freeze(pending) });
}

export function unstageWatchlistTicker(initialization, ticker) {
  const normalized = String(ticker || '').trim().toUpperCase();
  return Object.freeze({
    phase: !initialization?.phase || initialization.phase === 'uninitialized'
      ? 'initializing'
      : initialization.phase,
    pending: Object.freeze((initialization?.pending || []).filter((item) => item !== normalized)),
  });
}

export function retainWatchlistInitialization(initialization) {
  return Object.freeze({
    phase: 'initializing',
    pending: Object.freeze([...(initialization?.pending || [])]),
  });
}

export function resolveWatchlistInitialization(initialization, backendTickers, limit = 100) {
  const merged = [];
  const seen = new Set();
  for (const ticker of [...(backendTickers || []), ...(initialization?.pending || [])]) {
    const normalized = String(ticker || '').trim().toUpperCase();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    merged.push(normalized);
    if (merged.length >= limit) break;
  }
  return Object.freeze({ phase: 'initialized', pending: Object.freeze([]), tickers: Object.freeze(merged) });
}

export function beginWatchlistDefaultReset() {
  // Reset means "ask the backend for its defaults again". It is not an
  // explicitly empty user list and must remain retryable until that read wins.
  return createWatchlistInitialization(null);
}

export function shouldRenderWatchlistRefresh(result, alreadyRendered) {
  return !alreadyRendered || Boolean(result?.changed) || Boolean(result?.retryable);
}

export function earningsCoverage(payload) {
  const attempted = Number(payload?.attempted);
  const succeeded = Number(payload?.succeeded);
  const hasCounts = Number.isFinite(attempted) && attempted >= 0
    && Number.isFinite(succeeded) && succeeded >= 0;
  const degraded = payload?.source_status === 'degraded'
    || Boolean(payload?.data_limited)
    || (hasCounts && succeeded < attempted);
  return Object.freeze({
    attempted: hasCounts ? Math.trunc(attempted) : null,
    succeeded: hasCounts ? Math.trunc(succeeded) : null,
    degraded,
  });
}

export function earningsRefreshNotice(payload) {
  const status = String(payload?.refresh_status || '').trim().toLowerCase();
  const retryAfterValue = Number(payload?.refresh_retry_after_seconds);
  const retryAfterSeconds = Number.isFinite(retryAfterValue) && retryAfterValue > 0
    ? Math.ceil(retryAfterValue)
    : null;
  if (status === 'failed_stale') {
    return Object.freeze({
      status,
      tone: 'error',
      retained: true,
      title: '更新失败，继续显示上次数据',
      message: retryAfterSeconds
        ? `数据来源刷新失败，约 ${retryAfterSeconds} 秒后可重试。`
        : '数据来源刷新失败，可稍后重新读取。',
      retryAfterSeconds,
    });
  }
  if (status === 'cooldown') {
    return Object.freeze({
      status,
      tone: 'warning',
      retained: true,
      title: '刷新仍在冷却，继续显示现有数据',
      message: retryAfterSeconds
        ? `服务端正在限制重复刷新，约 ${retryAfterSeconds} 秒后可重试。`
        : '服务端正在限制重复刷新，请稍后再试。',
      retryAfterSeconds,
    });
  }
  return null;
}

export function isRadarPaused(status, payload) {
  const runtimeReason = String(
    payload?.source_status?.runtime_reason
    || status?.source_status?.runtime_reason
    || status?.runtime_reason
    || '',
  ).toLowerCase();
  return status?.status === 'paused'
    || status?.worker?.status === 'paused'
    || payload?.status === 'paused'
    || ['market_closed', 'scan_paused', 'paused'].includes(runtimeReason);
}

export function breakoutDegradedView(failureDomain, hasSnapshot) {
  const providerFailure = String(failureDomain || '').toLowerCase() === 'provider';
  if (providerFailure) {
    return Object.freeze({
      title: hasSnapshot ? '部分数据源降级' : '数据源降级，尚无可靠快照',
      copy: hasSnapshot
        ? '可用事件继续展示，缺失字段保持为空。'
        : '工作进程已保留失败记录，但不会发布不完整结果。',
    });
  }
  return Object.freeze({
    title: hasSnapshot ? '雷达处理暂时降级' : '雷达处理降级，尚无可靠快照',
    copy: hasSnapshot
      ? '最近完整快照继续保留；当前处理环节缺失的字段保持为空。'
      : '后台处理暂时未能完成，雷达不会发布不完整结果。',
  });
}

export function breakoutFailureDomain(status, payload) {
  const explicit = String(
    status?.failure_domain
      || payload?.failure_domain
      || status?.worker?.details?.failure_domain
      || payload?.source_status?.freshness?.failure_domain
      || '',
  ).toLowerCase();
  if (explicit) return explicit;
  const runtimeReason = String(
    status?.runtime_reason || payload?.runtime_reason || payload?.source_status?.runtime_reason || '',
  ).toLowerCase();
  const providerStatus = String(payload?.source_status?.provider || '').toLowerCase();
  const providerHealth = Array.isArray(status?.provider_health)
    ? status.provider_health
    : [];
  if (
    runtimeReason.startsWith('provider_')
    || ['degraded', 'stale', 'unavailable'].includes(providerStatus)
    || providerHealth.some((item) => ['degraded', 'stale', 'unavailable'].includes(
      String(item?.status || '').toLowerCase(),
    ))
  ) return 'provider';
  return '';
}

export function isRadarAwaitingFirstSnapshot(status, payload) {
  if (!status?.enabled || status?.latest_completed_scan || payload?.scan_run_id) return false;
  const runtimeReason = String(
    status?.runtime_reason || payload?.runtime_reason || payload?.source_status?.runtime_reason || '',
  ).toLowerCase();
  const workerStatus = String(status?.worker?.status || '').toLowerCase();
  return runtimeReason === 'completed_snapshot_missing'
    && !breakoutFailureDomain(status, payload)
    && ['idle', 'running', 'publishing'].includes(workerStatus);
}

export function shouldAutoRefreshRadar({ hidden, loading, refreshing, loadingMore }) {
  return !hidden && !loading && !refreshing && !loadingMore;
}
