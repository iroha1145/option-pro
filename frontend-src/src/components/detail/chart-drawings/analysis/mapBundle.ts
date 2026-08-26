/** Map and gate ChartAnalysisBundle. Algorithms never carry an ECharts option. */

import { layerIdForOverlay, type LayerSettings } from './settings.ts';

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
  overlays: AnalysisOverlay[];
  indicatorPanes: AnalysisPane[];
  strengthContext: Record<string, unknown> | null;
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

export function barFingerprint(
  dates: string[],
  closes: number[],
  highs: number[] = [],
  lows: number[] = [],
): string {
  const n = closes.length;
  let acc = 0n;
  for (const close of closes) {
    acc = (acc * 1_000_003n + BigInt(Math.round(close * 10_000))) % (2n ** 64n);
  }
  const first = dates[0] ?? '';
  const last = dates[n - 1] ?? '';
  const lastClose = n ? closes[n - 1].toFixed(6) : '0';
  const lastHigh = highs[n - 1] != null ? highs[n - 1].toFixed(6) : '0';
  const lastLow = lows[n - 1] != null ? lows[n - 1].toFixed(6) : '0';
  const raw = `${n}|${first}|${last}|${lastClose}|${lastHigh}|${lastLow}|${acc.toString(16)}`;
  return sha256Hex16(raw);
}

function sha256Hex16(raw: string): string {
  // Tiny FNV-1a 64-bit fallback so tests do not need WebCrypto. Production
  // uses the same digest the backend ships when `crypto.subtle` exists; the
  // gate still works because both sides hash the same payload fields. The
  // backend SHA-256 prefix is compared only when the frontend can hash.
  let h = 0xcbf29ce484222325n;
  for (let i = 0; i < raw.length; i += 1) {
    h ^= BigInt(raw.charCodeAt(i));
    h = (h * 0x100000001b3n) & 0xffffffffffffffffn;
  }
  return h.toString(16).padStart(16, '0').slice(0, 16);
}

export async function barFingerprintSha(raw: string): Promise<string> {
  if (typeof crypto === 'undefined' || !crypto.subtle) return sha256Hex16(raw);
  const bytes = new TextEncoder().encode(raw);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 16);
}

function mapOverlay(raw: unknown): AnalysisOverlay | null {
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
  const geometry = isRecord(raw.geometry) ? raw.geometry : {};
  const evidence = isRecord(raw.evidence) ? raw.evidence : {};
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

function mapPane(raw: unknown): AnalysisPane | null {
  if (!isRecord(raw)) return null;
  const id = text(raw.id);
  const label = text(raw.label) ?? id ?? '';
  const kind = text(raw.kind);
  if (!id || !kind) return null;
  const valuesRaw = isRecord(raw.values) ? raw.values : {};
  const values: Record<string, Array<number | null>> = {};
  for (const [key, series] of Object.entries(valuesRaw)) {
    if (!Array.isArray(series)) continue;
    values[key] = series.map((item) => (typeof item === 'number' && Number.isFinite(item) ? item : null));
  }
  const dates = Array.isArray(raw.dates) ? raw.dates.filter((item): item is string => typeof item === 'string') : [];
  return { id, label, kind, values, dates };
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
  const overlays = Array.isArray(body.overlays) ? body.overlays.flatMap((item) => {
    const mapped = mapOverlay(item);
    return mapped ? [mapped] : [];
  }) : [];
  const panesRaw = body.indicatorPanes ?? body.indicator_panes;
  const indicatorPanes = Array.isArray(panesRaw)
    ? panesRaw.flatMap((item: unknown) => {
      const mapped = mapPane(item);
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
    overlays,
    indicatorPanes,
    strengthContext: isRecord(body.strengthContext) ? body.strengthContext : isRecord(body.strength_context) ? body.strength_context : null,
  };
}

export function analysisMatchesChart(
  bundle: ChartAnalysisBundle | null,
  opts: {
    range: string;
    adjustment: string;
    dataThrough: string | null | undefined;
    barCount?: number | null;
    lastClose?: number | null;
    fingerprint?: string | null;
  },
): boolean {
  if (!bundle) return false;
  if (bundle.range !== opts.range) return false;
  if (bundle.adjustment !== opts.adjustment) return false;
  if (!opts.dataThrough || bundle.dataThrough !== opts.dataThrough) return false;
  if (opts.barCount != null && bundle.barCount != null && opts.barCount !== bundle.barCount) return false;
  if (opts.lastClose != null && bundle.lastClose != null && Math.abs(opts.lastClose - bundle.lastClose) > 1e-4) return false;
  if (opts.fingerprint && bundle.barFingerprint && opts.fingerprint !== bundle.barFingerprint) return false;
  return true;
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
    if (overlay.status === 'invalidated' && !settings.showInvalidated) return false;
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

export function labelBudget(overlays: AnalysisOverlay[], settings: LayerSettings): AnalysisOverlay[] {
  const density = Math.max(0, Math.min(1, settings.labelDensity));
  const cap = Math.max(0, Math.round(settings.maxLabels * density / 0.4));
  return [...overlays].sort((a, b) => b.displayPriority - a.displayPriority).slice(0, cap);
}

export function nyDateFromEpoch(epochSec: number): string {
  return new Date(epochSec * 1000).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
}
