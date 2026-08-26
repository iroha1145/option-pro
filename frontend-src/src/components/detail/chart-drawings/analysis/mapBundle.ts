/** Map and gate ChartAnalysisBundle. Algorithms never carry an ECharts option. */

import { layerIdForOverlay, type LayerSettings } from './settings.ts';
import { sha256Hex } from './sha256.ts';

export { sha256Hex };

export interface AnalysisAnchor {
  time: string;
  barKey: string;
  price: number;
}

export interface AnalysisOverlay {
  id: string;
  sourceId: string;
  algorithmVersion: string;
  group: string;
  kind: string;
  geometry: Record<string, unknown>;
  status: string;
  direction: string;
  shapeQuality: number;
  displayPriority: number;
  evidence: Record<string, unknown>;
  formationStart: string;
  formationEnd: string;
  dataThrough: string;
  label: string;
  detail: string;
}

export interface AnalysisPane {
  id: string;
  label: string;
  kind: string;
  values: Record<string, Array<number | null>>;
  /** 该副图每个取值对应的 bar 日期；后端只发一份共享 dates 时由 offset 展开 */
  dates: string[];
}

export interface ChartAnalysisBundle {
  ticker: string;
  range: string;
  adjustment: string;
  dataThrough: string;
  barFingerprint: string;
  barCount: number | null;
  lastClose: number | null;
  /** 分析序列的 bar 日期，整包只发一份；overlay/pane 用 offset 索引进来 */
  dates: string[];
  /** 指纹元数据：后端到底哈希了哪一段（首/末 bar 日期），mismatch 才可诊断可修复 */
  firstBarDate: string | null;
  lastBarDate: string | null;
  /** 指纹口径代号（后端 FINGERPRINT_ALGORITHM）；换代要两边一起换 */
  fingerprintAlgorithm: string | null;
  overlays: AnalysisOverlay[];
  indicatorPanes: AnalysisPane[];
  strengthContext: Record<string, unknown> | null;
}

/** 真正能被 autoPatternName 命名、可以进标签条的形态 kind。 */
export const PATTERN_KINDS = ['support_trend', 'resistance_trend', 'channel', 'triangle', 'wedge', 'box'] as const;
const PATTERN_KIND_SET: ReadonlySet<string> = new Set<string>(PATTERN_KINDS);

export function isPatternKind(kind: string): boolean {
  return PATTERN_KIND_SET.has(kind);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  return null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

export interface CanonicalBar {
  t: string | number;
  o: number;
  h: number;
  l: number;
  c: number;
  v?: number;
  ext?: boolean;
  quote_only?: boolean;
}

function epochOfStamp(value: string): number {
  if (/^\d+$/.test(value)) {
    const n = Number(value);
    return n > 100_000_000_000 ? Math.floor(n / 1000) : n;
  }
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : 0;
}

/** 后端 chart_analysis._fmt6 的逐字镜像：先放大取整（半数远离零）再格式化。
 *  两边必须落在同一个字节上——闸门 fail-closed，差一位就是整块分析静默消失。 */
function fmt6(value: number): string {
  const scaled = Math.trunc(value * 1_000_000 + (value >= 0 ? 0.5 : -0.5));
  return (scaled / 1_000_000).toFixed(6);
}

/** 后端 chart_analysis.FINGERPRINT_ALGORITHM 的镜像；口径换代要两边一起换。 */
export const FINGERPRINT_ALGORITHM = 'sha256-bar-ohlcv-v1';

export function canonicalBarPayload(bars: CanonicalBar[]): string {
  return bars.map((bar) => {
    const timestamp = epochOfStamp(String(bar.t));
    const volume = typeof bar.v === 'number' && Number.isFinite(bar.v) ? bar.v : 0;
    return [
      String(timestamp),
      fmt6(bar.o),
      fmt6(bar.h),
      fmt6(bar.l),
      fmt6(bar.c),
      fmt6(volume),
      bar.ext === true ? '1' : '0',
      bar.quote_only === true ? '1' : '0',
    ].join('|');
  }).join('\n');
}

export function barFingerprint(bars: CanonicalBar[]): string {
  return sha256Hex(canonicalBarPayload(bars));
}

function mapOverlay(raw: unknown, sharedDates: string[]): AnalysisOverlay | null {
  if (!isRecord(raw)) return null;
  const id = text(raw.id);
  const sourceId = text(raw.sourceId ?? raw.source_id);
  const algorithmVersion = text(raw.algorithmVersion ?? raw.algorithm_version);
  const group = text(raw.group);
  const kind = text(raw.kind);
  const status = text(raw.status);
  const direction = text(raw.direction);
  const label = text(raw.label) ?? '';
  const detail = text(raw.detail) ?? '';
  const formationStart = text(raw.formationStart ?? raw.formation_start);
  const formationEnd = text(raw.formationEnd ?? raw.formation_end);
  const dataThrough = text(raw.dataThrough ?? raw.data_through);
  const shapeQuality = finiteNumber(raw.shapeQuality ?? raw.shape_quality);
  const displayPriority = finiteNumber(raw.displayPriority ?? raw.display_priority);
  if (!id || !sourceId || !algorithmVersion || !group || !kind || !status || !direction) return null;
  if (!formationStart || !formationEnd || !dataThrough) return null;
  if (shapeQuality === null || displayPriority === null) return null;
  if (raw.option != null || raw.graphic != null) return null;
  const rawGeometry = isRecord(raw.geometry) ? raw.geometry : {};
  const values = Array.isArray(rawGeometry.values) ? rawGeometry.values : null;
  const geometry = values
    ? { ...rawGeometry, dates: datesForSeries(rawGeometry, sharedDates, values.length) }
    : rawGeometry;
  const evidence: Record<string, unknown> = isRecord(raw.evidence) ? { ...raw.evidence } : {};
  // 触碰次数只在 evidence 里对前端可见；老负载把它留在 pattern 顶层，
  // 退到 geometry.touchAnchors 的条数，chip 才不会空着。
  if (finiteNumber(evidence.touches) === null) {
    const fallback = finiteNumber(raw.touches)
      ?? (Array.isArray(rawGeometry.touchAnchors) ? rawGeometry.touchAnchors.length : null);
    if (fallback !== null) evidence.touches = fallback;
  }
  return {
    id,
    sourceId,
    algorithmVersion,
    group,
    kind,
    geometry,
    status,
    direction,
    shapeQuality,
    displayPriority,
    evidence,
    formationStart,
    formationEnd,
    dataThrough,
    label,
    detail,
  };
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

/**
 * 共享 dates + startIndex → 本序列自己的日期数组。
 *
 * 后端只发一份 bundle.dates（原来每个 overlay/pane 各带一份，~10 倍冗余），
 * 序列自己声明从第几根 bar 起（暖机段的 null 已被切掉）。这里就地展开成
 * 既有的 dates 语义，下游 alignSeriesToBars 不用知道两种形状的区别。
 */
function datesForSeries(raw: Record<string, unknown>, sharedDates: string[], length: number): string[] {
  const own = stringList(raw.dates);
  if (own.length) return own;
  if (!sharedDates.length || length <= 0) return [];
  const offset = finiteNumber(raw.startIndex ?? raw.start_index ?? raw.offset) ?? 0;
  const start = Math.max(0, Math.min(sharedDates.length, Math.round(offset)));
  return sharedDates.slice(start, start + length);
}

function mapPane(raw: unknown, sharedDates: string[]): AnalysisPane | null {
  if (!isRecord(raw)) return null;
  const id = text(raw.id);
  const label = text(raw.label) ?? id ?? '';
  const kind = text(raw.kind);
  if (!id || !kind) return null;
  const valuesRaw = isRecord(raw.values) ? raw.values : {};
  const values: Record<string, Array<number | null>> = {};
  let span = 0;
  for (const [key, series] of Object.entries(valuesRaw)) {
    if (!Array.isArray(series)) continue;
    values[key] = series.map((item) => (typeof item === 'number' && Number.isFinite(item) ? item : null));
    span = Math.max(span, values[key].length);
  }
  return { id, label, kind, values, dates: datesForSeries(raw, sharedDates, span) };
}

export function mapChartAnalysis(raw: unknown): ChartAnalysisBundle | null {
  const row = isRecord(raw) ? raw : {};
  const body = isRecord(row.chart_analysis) ? row.chart_analysis : isRecord(row.chartAnalysis) ? row.chartAnalysis : row;
  if (!isRecord(body)) return null;
  if (body.option != null || body.graphic != null) return null;
  const ticker = text(body.ticker) ?? '';
  const range = text(body.range) ?? '1d';
  const adjustment = text(body.adjustment) ?? 'raw';
  const dataThrough = text(body.dataThrough ?? body.data_through);
  const barFp = text(body.barFingerprint ?? body.bar_fingerprint);
  if (!dataThrough || !barFp) return null;
  const dates = stringList(body.dates);
  const overlays = Array.isArray(body.overlays) ? body.overlays.flatMap((item) => {
    const mapped = mapOverlay(item, dates);
    return mapped ? [mapped] : [];
  }) : [];
  const panesRaw = body.indicatorPanes ?? body.indicator_panes;
  const indicatorPanes = Array.isArray(panesRaw)
    ? panesRaw.flatMap((item: unknown) => {
      const mapped = mapPane(item, dates);
      return mapped ? [mapped] : [];
    })
    : [];
  return {
    ticker,
    range,
    adjustment,
    dataThrough,
    barFingerprint: barFp,
    barCount: finiteNumber(body.barCount ?? body.bar_count),
    lastClose: finiteNumber(body.lastClose ?? body.last_close),
    dates,
    firstBarDate: text(body.firstBarDate ?? body.first_bar_date) ?? dates[0] ?? null,
    lastBarDate: text(body.lastBarDate ?? body.last_bar_date) ?? dates[dates.length - 1] ?? dataThrough,
    fingerprintAlgorithm: text(body.fingerprintAlgorithm ?? body.fingerprint_algorithm),
    overlays,
    indicatorPanes,
    strengthContext: isRecord(body.strengthContext) ? body.strengthContext : isRecord(body.strength_context) ? body.strength_context : null,
  };
}

export type AnalysisGateReason =
  | 'ok'
  | 'no_bundle'
  | 'ticker'
  | 'range'
  | 'adjustment'
  | 'data_through'
  | 'bar_count'
  | 'last_close'
  | 'fingerprint';

export interface AnalysisGateOpts {
  range: string;
  adjustment: string;
  dataThrough: string | null | undefined;
  ticker?: string | null;
  barCount?: number | null;
  lastClose?: number | null;
  fingerprint?: string | null;
}

/**
 * 闸门是 fail-closed 的：不匹配就整套分析图层不画。所以「为什么不匹配」必须
 * 说得出来——只返回 false 时，跨语言指纹漂移会静默吞掉整个功能而 CI 看不见。
 */
export function analysisGate(bundle: ChartAnalysisBundle | null, opts: AnalysisGateOpts): AnalysisGateReason {
  if (!bundle) return 'no_bundle';
  if (opts.ticker != null && opts.ticker !== '' && bundle.ticker !== opts.ticker) return 'ticker';
  if (bundle.range !== opts.range) return 'range';
  if (bundle.adjustment !== opts.adjustment) return 'adjustment';
  if (!opts.dataThrough || bundle.dataThrough !== opts.dataThrough) return 'data_through';
  if (opts.barCount != null && bundle.barCount != null && opts.barCount !== bundle.barCount) return 'bar_count';
  if (opts.lastClose != null && bundle.lastClose != null && Math.abs(opts.lastClose - bundle.lastClose) > 1e-4) return 'last_close';
  // 后端换了指纹口径而前端还没跟上：当成指纹不匹配报出来，别静默空白。
  if (bundle.fingerprintAlgorithm && bundle.fingerprintAlgorithm !== FINGERPRINT_ALGORITHM) return 'fingerprint';
  if (!opts.fingerprint || opts.fingerprint !== bundle.barFingerprint) return 'fingerprint';
  return 'ok';
}

export function analysisMatchesChart(bundle: ChartAnalysisBundle | null, opts: AnalysisGateOpts): boolean {
  return analysisGate(bundle, opts) === 'ok';
}

/** 后端 series_from_chart_bars 的 dates 口径：日/周是纽约交易日，分钟是 epoch 秒。 */
export function barStampForRange(t: string, range: string): string {
  const epoch = epochOfStamp(t);
  return range === '5m' || range === '15m' || range === '1h' ? String(epoch) : nyDateFromEpoch(epoch);
}

export function closedBarsForFingerprint<T extends CanonicalBar & { t: string }>(
  bars: T[],
  range: string,
  opts?: { dropLast?: boolean; fromDate?: string | null; throughDate?: string | null },
): T[] {
  let rows = bars.filter((bar) => bar.ext !== true && bar.quote_only !== true);
  if (opts?.fromDate || opts?.throughDate) {
    // 日期字符串只算一遍：nyDateFromEpoch 走 Intl，500 根算两遍就是几十毫秒。
    let stamps = rows.map((bar) => barStampForRange(bar.t, range));
    if (opts.fromDate) {
      const index = stamps.indexOf(opts.fromDate);
      if (index > 0) {
        rows = rows.slice(index);
        stamps = stamps.slice(index);
      }
    }
    // throughDate 已经把「后端没吃到的末根」切掉了，再叠 dropLast 会多砍一根。
    if (opts.throughDate) {
      const last = stamps.lastIndexOf(opts.throughDate);
      if (last >= 0) return rows.slice(0, last + 1);
    }
  }
  if (opts?.dropLast && rows.length) rows = rows.slice(0, -1);
  return rows;
}

export function barFingerprintFromBars<T extends CanonicalBar & { t: string }>(
  bars: T[],
  range: string,
  opts?: { dropLast?: boolean; fromDate?: string | null; throughDate?: string | null },
): string {
  return barFingerprint(closedBarsForFingerprint(bars, range, opts));
}

/**
 * 用 bundle 自带的指纹元数据（首/末 bar 日期）去切当前图表序列，而不是靠
 * technical.series_break_at 猜。后端声明它哈希了哪一段，前端就哈希同一段——
 * series_break_at 之类的口径差不再表现为「整块分析静默消失」。
 */
export function fingerprintForBundle<T extends CanonicalBar & { t: string }>(
  bundle: ChartAnalysisBundle | null,
  bars: T[],
  range: string,
  opts?: { dropLast?: boolean; fromDate?: string | null },
): string | null {
  if (!bundle || !bars.length) return null;
  return barFingerprintFromBars(bars, range, {
    dropLast: opts?.dropLast,
    fromDate: bundle.firstBarDate ?? opts?.fromDate ?? null,
    throughDate: bundle.lastBarDate,
  });
}

/** 指纹对不上时，元数据能说清是「根数不同」还是「同一段但数值有出入」。 */
export function fingerprintDiagnosis<T extends CanonicalBar & { t: string }>(
  bundle: ChartAnalysisBundle | null,
  bars: T[],
  range: string,
  opts?: { dropLast?: boolean; fromDate?: string | null },
): { bars: number; expected: number | null; sameWindow: boolean } | null {
  if (!bundle) return null;
  const rows = closedBarsForFingerprint(bars, range, {
    dropLast: opts?.dropLast,
    fromDate: bundle.firstBarDate ?? opts?.fromDate ?? null,
    throughDate: bundle.lastBarDate,
  });
  const first = rows.length ? barStampForRange(rows[0].t, range) : null;
  const last = rows.length ? barStampForRange(rows[rows.length - 1].t, range) : null;
  return {
    bars: rows.length,
    expected: bundle.barCount,
    sameWindow: first === bundle.firstBarDate && last === bundle.lastBarDate,
  };
}

const ACTIVE_STATUSES = new Set(['forming', 'testing', 'triggered', 'confirmed', 'retest']);

export function filterOverlays(
  overlays: AnalysisOverlay[],
  settings: LayerSettings,
): AnalysisOverlay[] {
  const enabled = new Set(settings.enabled);
  const kept = overlays.filter((overlay) => {
    const layerId = layerIdForOverlay(overlay.kind, overlay.id);
    if (!layerId || !enabled.has(layerId)) return false;
    if (overlay.shapeQuality < settings.minShapeQuality && (overlay.kind === 'support_trend' || overlay.kind === 'resistance_trend' || overlay.kind === 'channel' || overlay.kind === 'triangle' || overlay.kind === 'wedge')) {
      return false;
    }
    if (overlay.status === 'invalidated') {
      return settings.showInvalidated;
    }
    if (settings.onlyActive && !ACTIVE_STATUSES.has(overlay.status) && overlay.status !== 'broken_up' && overlay.status !== 'broken_down') {
      return false;
    }
    return true;
  });
  const patterns = kept.filter((overlay) =>
    ['support_trend', 'resistance_trend', 'channel', 'triangle', 'wedge'].includes(overlay.kind),
  );
  const others = kept.filter((overlay) => !patterns.includes(overlay));
  patterns.sort((a, b) => b.displayPriority - a.displayPriority);
  return [...patterns.slice(0, settings.maxPatterns), ...others];
}

export function filterPanes(panes: AnalysisPane[], settings: LayerSettings): AnalysisPane[] {
  const enabled = new Set(settings.enabled);
  return panes.filter((pane) => enabled.has(pane.id));
}

/**
 * 形态标签条的预算。
 *
 * 只有真正的形态 kind 能进：以前把全部可见 overlay 都灌进来，默认 minimal
 * 预设（ma20 + auto_patterns）于是在每只票上打出「形态 · ma · 置信度 100」，
 * 分钟图打出「形态 · breakout · 置信度 50」——那不是形态，也不是置信度。
 */
export function labelBudget(overlays: AnalysisOverlay[], settings: LayerSettings): AnalysisOverlay[] {
  const density = Math.max(0, Math.min(1, settings.labelDensity));
  const cap = Math.min(
    settings.maxLabels,
    Math.max(0, Math.round(settings.maxLabels * density)),
  );
  return overlays
    .filter((overlay) => isPatternKind(overlay.kind))
    .sort((a, b) => b.displayPriority - a.displayPriority)
    .slice(0, cap);
}

// 复用一个 formatter：toLocaleDateString 每次都重建 Intl，逐根 bar 调用会很贵。
const NY_DAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

export function nyDateFromEpoch(epochSec: number): string {
  return NY_DAY.format(new Date(epochSec * 1000));
}
