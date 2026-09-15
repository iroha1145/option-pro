function percentile(values, q) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * q)))];
}

export function summarizeInterleavedRows(rows) {
  const readyRows = rows.filter((sample) => sample.news_content_ready_ms != null);
  const ready = readyRows.map((sample) => sample.news_content_ready_ms);
  const lcp = rows
    .filter((sample) => sample.lcp?.startTime != null)
    .map((sample) => sample.lcp.startTime);
  const timeoutN = rows.length - readyRows.length;
  const hasCount = (sample, key) => sample[key] != null && Number.isFinite(Number(sample[key]));
  const rateLimitedN = rows.filter((sample) => (sample.rate_limited || 0) > 0).length;
  const httpTelemetryN = rows.filter((sample) => hasCount(sample, 'http_error_n')).length;
  const requestFailureTelemetryN = rows.filter((sample) => hasCount(sample, 'request_failed_n')).length;
  const httpErrorN = rows.reduce((sum, sample) => (
    sum + (hasCount(sample, 'http_error_n') ? Number(sample.http_error_n) : 0)
  ), 0);
  const requestFailedN = rows.reduce((sum, sample) => (
    sum + (hasCount(sample, 'request_failed_n') ? Number(sample.request_failed_n) : 0)
  ), 0);
  return {
    n: rows.length,
    ready_n: readyRows.length,
    timeout_n: timeoutN,
    failure_rate: rows.length ? timeoutN / rows.length : 1,
    complete: rows.length > 0 && timeoutN === 0,
    news_content_ready_p50: percentile(ready, 0.5),
    news_content_ready_p75: percentile(ready, 0.75),
    lcp_p75: percentile(lcp, 0.75),
    titles: [...new Set(readyRows.map((sample) => sample.news_title).filter(Boolean))],
    rate_limited_n: rateLimitedN,
    http_error_n: httpErrorN,
    request_failed_n: requestFailedN,
    http_telemetry_n: httpTelemetryN,
    request_failure_telemetry_n: requestFailureTelemetryN,
    clean: timeoutN === 0 && rateLimitedN === 0 && httpErrorN === 0 && requestFailedN === 0
      && httpTelemetryN === rows.length && requestFailureTelemetryN === rows.length,
  };
}

function comparable(left, right) {
  return left.complete && right.complete && left.n === right.n;
}

function p75Delta(left, right) {
  if (!comparable(left, right)) return null;
  if (left.news_content_ready_p75 == null || right.news_content_ready_p75 == null) return null;
  return left.news_content_ready_p75 - right.news_content_ready_p75;
}

export function buildInterleavedSummary({ optCold, optWarm, unoptCold, unoptWarm }) {
  const opt = {
    cold: summarizeInterleavedRows(optCold),
    warm: summarizeInterleavedRows(optWarm),
  };
  const unopt = {
    cold: summarizeInterleavedRows(unoptCold),
    warm: summarizeInterleavedRows(unoptWarm),
  };
  const comparisonComplete = comparable(opt.cold, unopt.cold)
    && comparable(opt.warm, unopt.warm);
  return {
    opt,
    unopt,
    comparison_complete: comparisonComplete,
    comparison_status: comparisonComplete ? 'complete' : 'incomplete_samples',
    delta_cold_p75: p75Delta(opt.cold, unopt.cold),
    delta_warm_p75: p75Delta(opt.warm, unopt.warm),
  };
}

export function interleavedGateFailures(
  summary,
  { coldBudgetMs = 2500, warmBudgetMs = 1000, requireNetworkTelemetry = false } = {},
) {
  const failures = [];
  if (!summary?.comparison_complete) failures.push('interleaved: incomplete_samples');
  for (const side of ['opt', 'unopt']) {
    for (const cache of ['cold', 'warm']) {
      const block = summary?.[side]?.[cache];
      const label = `${side}_${cache}`;
      if (!block) {
        failures.push(`${label}: missing_summary`);
        continue;
      }
      if (block.rate_limited_n) failures.push(`${label}: rate_limited_n=${block.rate_limited_n}`);
      if (block.http_error_n) failures.push(`${label}: http_error_n=${block.http_error_n}`);
      if (block.request_failed_n) failures.push(`${label}: request_failed_n=${block.request_failed_n}`);
      if (requireNetworkTelemetry && block.http_telemetry_n !== block.n) {
        failures.push(`${label}: http_telemetry_n=${block.http_telemetry_n ?? 'missing'} expected=${block.n}`);
      }
      if (requireNetworkTelemetry && block.request_failure_telemetry_n !== block.n) {
        failures.push(`${label}: request_failure_telemetry_n=${block.request_failure_telemetry_n ?? 'missing'} expected=${block.n}`);
      }
    }
  }
  const optCold = summary?.opt?.cold?.news_content_ready_p75;
  const optWarm = summary?.opt?.warm?.news_content_ready_p75;
  if (optCold == null || optCold > coldBudgetMs) {
    failures.push(`opt_cold: p75=${optCold ?? 'missing'} budget=${coldBudgetMs}`);
  }
  if (optWarm == null || optWarm > warmBudgetMs) {
    failures.push(`opt_warm: p75=${optWarm ?? 'missing'} budget=${warmBudgetMs}`);
  }
  return failures;
}

export function interleavedExitCode(summary, options) {
  return interleavedGateFailures(summary, options).length ? 1 : 0;
}
