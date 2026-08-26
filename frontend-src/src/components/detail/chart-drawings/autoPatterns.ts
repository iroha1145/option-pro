/** Strict mapper from /stocks/{ticker}/technical auto_patterns into chart marks. */
import type { AutoTechnicalPattern } from '@/api/types';

const KINDS = new Set<AutoTechnicalPattern['kind']>([
  'support_trend',
  'resistance_trend',
  'channel',
  'triangle',
  'wedge',
  'box',
]);
const DIRECTIONS = new Set<AutoTechnicalPattern['direction']>(['bullish', 'bearish', 'neutral']);
const STATUSES = new Set<AutoTechnicalPattern['status']>([
  'forming',
  'testing',
  'broken_up',
  'broken_down',
  'invalidated',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function optionalFinite(value: unknown): number | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return finiteNumber(value);
}

function parseAnchor(raw: unknown): AutoTechnicalPattern['anchors'][number] | null {
  if (!isRecord(raw)) return null;
  const time = nonEmptyString(raw.time);
  const barKey = nonEmptyString(raw.barKey);
  const price = finiteNumber(raw.price);
  if (!time || !barKey || price === null || price <= 0) return null;
  return { time, barKey, price };
}

export function mapAutoPatternItem(raw: unknown): AutoTechnicalPattern | null {
  if (!isRecord(raw)) return null;
  const id = nonEmptyString(raw.id);
  const algorithmVersion = nonEmptyString(raw.algorithmVersion ?? raw.algorithm_version);
  const kindRaw = nonEmptyString(raw.kind);
  const directionRaw = nonEmptyString(raw.direction);
  const statusRaw = nonEmptyString(raw.status);
  if (!id || !algorithmVersion || !kindRaw || !directionRaw || !statusRaw) return null;
  if (!KINDS.has(kindRaw as AutoTechnicalPattern['kind'])) return null;
  if (!DIRECTIONS.has(directionRaw as AutoTechnicalPattern['direction'])) return null;
  if (!STATUSES.has(statusRaw as AutoTechnicalPattern['status'])) return null;
  if (!Array.isArray(raw.anchors) || raw.anchors.length < 2) return null;
  const anchors = raw.anchors.map(parseAnchor);
  if (anchors.some((item) => item === null)) return null;
  const confidence = finiteNumber(raw.confidence);
  const touches = finiteNumber(raw.touches);
  if (confidence === null || confidence < 0 || confidence > 100) return null;
  if (touches === null || touches < 0 || !Number.isInteger(touches)) return null;
  const formationStart = nonEmptyString(raw.formationStart ?? raw.formation_start);
  const formationEnd = nonEmptyString(raw.formationEnd ?? raw.formation_end);
  const dataThrough = nonEmptyString(raw.dataThrough ?? raw.data_through);
  if (!formationStart || !formationEnd || !dataThrough) return null;
  const breakout = optionalFinite(raw.breakoutPrice ?? raw.breakout_price);
  const invalidation = optionalFinite(raw.invalidationPrice ?? raw.invalidation_price);
  const measured = optionalFinite(raw.measuredTarget ?? raw.measured_target);
  if (breakout === null || invalidation === null || measured === null) return null;
  const subtype = nonEmptyString(raw.subtype) ?? undefined;
  const rationale = Array.isArray(raw.rationaleCodes ?? raw.rationale_codes)
    ? (raw.rationaleCodes ?? raw.rationale_codes) as unknown[]
    : [];
  const rationaleCodes = rationale.flatMap((item) => (typeof item === 'string' ? [item] : []));
  const note = nonEmptyString(raw.measuredTargetNote ?? raw.measured_target_note);
  return {
    id,
    algorithmVersion,
    kind: kindRaw as AutoTechnicalPattern['kind'],
    subtype,
    direction: directionRaw as AutoTechnicalPattern['direction'],
    anchors: anchors as AutoTechnicalPattern['anchors'],
    confidence,
    touches,
    formationStart,
    formationEnd,
    dataThrough,
    status: statusRaw as AutoTechnicalPattern['status'],
    breakoutPrice: breakout,
    invalidationPrice: invalidation,
    measuredTarget: measured,
    measuredTargetNote: note ?? undefined,
    rationaleCodes,
  };
}

export function mapAutoPatterns(raw: unknown): AutoTechnicalPattern[] {
  if (!Array.isArray(raw)) return [];
  const mapped: AutoTechnicalPattern[] = [];
  for (const item of raw) {
    const parsed = mapAutoPatternItem(item);
    if (parsed) mapped.push(parsed);
  }
  return mapped;
}

/** Maps the live technical body fields the chart toggle actually reads. */
export function mapTechnicalAutoFields(body: unknown): {
  auto_patterns: AutoTechnicalPattern[];
  auto_patterns_version?: string;
  chart_analysis: Record<string, unknown> | null;
} {
  const row = isRecord(body) ? body : {};
  const version = nonEmptyString(row.auto_patterns_version ?? row.autoPatternsVersion) ?? undefined;
  const analysis = isRecord(row.chart_analysis)
    ? row.chart_analysis
    : isRecord(row.chartAnalysis)
      ? row.chartAnalysis
      : null;
  return {
    auto_patterns: mapAutoPatterns(row.auto_patterns ?? row.autoPatterns),
    auto_patterns_version: version,
    chart_analysis: analysis && analysis.option == null ? analysis : null,
  };
}
