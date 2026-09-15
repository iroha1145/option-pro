/**
 * Round 6 surfaces 汇总：只有 ready_class=content 算成功。
 * error / empty / idle / timeout / 未运行 分别记账，不得顶替 ready_n。
 */
export function classifyReadyRow(row, field = 'ready_ms') {
  if (row?.ready_class) return row.ready_class;
  if (row?.[field] == null) return 'timeout';
  return 'unknown';
}

export function summarizeReady(rows, field = 'ready_ms') {
  const classified = (rows || []).map((row) => ({
    ...row,
    ready_class: classifyReadyRow(row, field),
  }));
  const success = classified.filter((row) => row.ready_class === 'content' && row[field] != null);
  const values = success.map((row) => row[field]);
  const hasCount = (row, key) => row[key] != null && Number.isFinite(Number(row[key]));
  const percentile = (list, q) => {
    if (!list.length) return null;
    const ordered = [...list].sort((a, b) => a - b);
    return ordered[Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * q)))];
  };
  return {
    n: classified.length,
    ready_n: success.length,
    error_n: classified.filter((row) => row.ready_class === 'error').length,
    empty_n: classified.filter((row) => row.ready_class === 'empty').length,
    idle_n: classified.filter((row) => row.ready_class === 'idle').length,
    timeout_n: classified.filter((row) => row.ready_class === 'timeout').length,
    unknown_n: classified.filter((row) => row.ready_class === 'unknown').length,
    p50: percentile(values, 0.5),
    p75: percentile(values, 0.75),
    spread_iqr: values.length ? percentile(values, 0.75) - percentile(values, 0.25) : null,
    transfer_p50: percentile(classified.map((row) => row.transferSize || 0), 0.5),
    request_p50: percentile(classified.map((row) => row.request_count || 0), 0.5),
    chart_loaded_n: classified.filter((row) => row.chart_loaded).length,
    runtime_en_n: classified.filter((row) => row.runtime_en).length,
    runtime_ja_n: classified.filter((row) => row.runtime_ja).length,
    rate_limited_n: classified.filter((row) => (row.rate_limited || 0) > 0).length,
    http_error_n: classified.reduce((sum, row) => sum + Number(row.http_error_n || 0), 0),
    request_failed_n: classified.reduce((sum, row) => sum + Number(row.request_failed_n || 0), 0),
    http_telemetry_n: classified.filter((row) => hasCount(row, 'http_error_n')).length,
    request_failure_telemetry_n: classified.filter((row) => hasCount(row, 'request_failed_n')).length,
    samples: classified,
  };
}

export function readyGateFailures(summary, { expectedN, label, requireNetworkTelemetry = false }) {
  const failures = [];
  if (!summary) {
    failures.push(`${label}: missing summary`);
    return failures;
  }
  if (summary.n !== expectedN) failures.push(`${label}: n=${summary.n} expected=${expectedN}`);
  if (summary.ready_n !== expectedN) failures.push(`${label}: ready_n=${summary.ready_n} expected=${expectedN}`);
  if (summary.error_n) failures.push(`${label}: error_n=${summary.error_n}`);
  if (summary.empty_n) failures.push(`${label}: empty_n=${summary.empty_n}`);
  if (summary.idle_n) failures.push(`${label}: idle_n=${summary.idle_n}`);
  if (summary.timeout_n) failures.push(`${label}: timeout_n=${summary.timeout_n}`);
  if (summary.unknown_n) failures.push(`${label}: unknown_n=${summary.unknown_n}`);
  if (summary.rate_limited_n) failures.push(`${label}: rate_limited_n=${summary.rate_limited_n}`);
  if (summary.http_error_n) failures.push(`${label}: http_error_n=${summary.http_error_n}`);
  if (summary.request_failed_n) failures.push(`${label}: request_failed_n=${summary.request_failed_n}`);
  if (requireNetworkTelemetry && summary.http_telemetry_n !== expectedN) {
    failures.push(`${label}: http_telemetry_n=${summary.http_telemetry_n ?? 'missing'} expected=${expectedN}`);
  }
  if (requireNetworkTelemetry && summary.request_failure_telemetry_n !== expectedN) {
    failures.push(`${label}: request_failure_telemetry_n=${summary.request_failure_telemetry_n ?? 'missing'} expected=${expectedN}`);
  }
  return failures;
}
