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
    rate_limited_n: rows.filter((sample) => (sample.rate_limited || 0) > 0).length,
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

export function interleavedExitCode(summary) {
  return summary.comparison_complete ? 0 : 1;
}
