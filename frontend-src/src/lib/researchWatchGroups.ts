/** Display-only watch groups. They never replace the main-board row list. */

export interface ResearchWatchRow {
  securityId: string;
  score: number | null;
  rejectionReasons: string[];
  qualified: false;
  tradable: false;
  sourceDate: string;
}

export interface ResearchWatchView {
  extension: ResearchWatchRow[];
  highVolatility: ResearchWatchRow[];
  extensionTotal: number;
  highVolatilityTotal: number;
  displayLimit: number | null;
  highVolatilityCollapsed: boolean;
  displacesMainBoard: false;
  note: string;
  sourceDate: string;
  profile: string;
  horizon: string;
  protocol: string;
  source: string;
  filterScope: string;
  filterScopeNote: string;
}

export interface ResearchWatchExpectation {
  profile?: string | null;
  timeframe?: string | null;
  sessionDate?: string | null;
}

const ALLOWED_PROTOCOLS = new Set(['research_watch_layers_v1']);
const ALLOWED_SOURCES = new Set(['synthetic_watch_layers_v1', 'published_watch_layers_v1']);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : null;
}

/** Omitted or `all` is the mid horizon. It is not a wildcard for every horizon. */
export function normalizedHorizon(timeframe: string | null | undefined): string | null {
  if (timeframe == null || timeframe === '' || timeframe === 'all') return 'mid';
  if (timeframe === 'short' || timeframe === 'mid' || timeframe === 'long') return timeframe;
  return null;
}

export function formatWatchScore(score: number | null): string {
  if (score === null || !Number.isFinite(score)) return '—';
  return score.toFixed(1);
}

function rowsOf(value: unknown, sourceDate: string): ResearchWatchRow[] {
  if (!Array.isArray(value)) return [];
  const rows: ResearchWatchRow[] = [];
  for (const item of value) {
    const rec = asRecord(item);
    const securityId = rec && typeof rec.security_id === 'string' ? rec.security_id : '';
    if (!securityId) continue;
    const score = typeof rec?.score === 'number' && Number.isFinite(rec.score) ? rec.score : null;
    const reasons = Array.isArray(rec?.rejection_reasons)
      ? rec.rejection_reasons.filter((reason): reason is string => typeof reason === 'string')
      : [];
    rows.push({
      securityId,
      score,
      rejectionReasons: reasons,
      qualified: false,
      tradable: false,
      sourceDate: typeof rec?.source_date === 'string' ? rec.source_date : sourceDate,
    });
  }
  return rows;
}

function totalOf(value: unknown, shown: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= shown ? value : shown;
}

function matchesExpectation(view: { profile: string; horizon: string; sourceDate: string }, expected: ResearchWatchExpectation): boolean {
  if ((expected.profile ?? '') !== view.profile) return false;
  if (normalizedHorizon(expected.timeframe) !== view.horizon) return false;
  if (expected.sessionDate && expected.sessionDate.slice(0, 10) !== view.sourceDate.slice(0, 10)) return false;
  return true;
}

/** Absent, disabled, or identity-mismatched groups stay unwired. Qualified flags are not shown as tradable. */
export function researchWatchView(raw: unknown, expected?: ResearchWatchExpectation): ResearchWatchView | null {
  const rec = asRecord(raw);
  if (!rec || rec.enabled !== true || rec.displaces_main_board !== false) return null;
  const sourceDate = typeof rec.source_date === 'string' ? rec.source_date : '';
  const profile = typeof rec.profile === 'string' ? rec.profile : '';
  const horizon = typeof rec.horizon === 'string' ? rec.horizon : '';
  const protocol = typeof rec.protocol === 'string' ? rec.protocol : '';
  const source = typeof rec.source === 'string' ? rec.source : '';
  if (!sourceDate || !profile || !horizon || !ALLOWED_PROTOCOLS.has(protocol) || !ALLOWED_SOURCES.has(source)) return null;
  if (expected && !matchesExpectation({ profile, horizon, sourceDate }, expected)) return null;
  const extension = rowsOf(rec.extension_watch, sourceDate);
  const highVolatility = rowsOf(rec.high_volatility_watch, sourceDate);
  return {
    extension,
    highVolatility,
    extensionTotal: totalOf(rec.extension_total, extension.length),
    highVolatilityTotal: totalOf(rec.high_volatility_total, highVolatility.length),
    displayLimit: typeof rec.display_limit === 'number' && Number.isFinite(rec.display_limit) ? rec.display_limit : null,
    highVolatilityCollapsed: rec.high_volatility_collapsed !== false,
    displacesMainBoard: false,
    note: typeof rec.qualification_note === 'string' ? rec.qualification_note : '',
    sourceDate,
    profile,
    horizon,
    protocol,
    source,
    filterScope: typeof rec.filter_scope === 'string' ? rec.filter_scope : '',
    filterScopeNote: typeof rec.filter_scope_note === 'string' ? rec.filter_scope_note : '',
  };
}

export function visibleResearchWatch(
  view: ResearchWatchView | null,
  profile: string | null | undefined,
  timeframe: string | null | undefined,
): ResearchWatchView | null {
  if (!view) return null;
  if (!matchesExpectation(view, { profile, timeframe })) return null;
  return view;
}

export function mainBoardRows<T>(rows: T[], watch: ResearchWatchView | null): T[] {
  return watch?.displacesMainBoard ? rows : rows;
}
