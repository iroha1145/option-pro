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
  highVolatilityCollapsed: boolean;
  displacesMainBoard: false;
  note: string;
  sourceDate: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : null;
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

/** Absent or disabled groups stay unwired. Qualified flags from the payload are not shown as tradable. */
export function researchWatchView(raw: unknown): ResearchWatchView | null {
  const rec = asRecord(raw);
  if (!rec || rec.enabled !== true || rec.displaces_main_board !== false) return null;
  const sourceDate = typeof rec.source_date === 'string' ? rec.source_date : '';
  return {
    extension: rowsOf(rec.extension_watch, sourceDate),
    highVolatility: rowsOf(rec.high_volatility_watch, sourceDate),
    highVolatilityCollapsed: rec.high_volatility_collapsed !== false,
    displacesMainBoard: false,
    note: typeof rec.qualification_note === 'string' ? rec.qualification_note : '',
    sourceDate,
  };
}

export function mainBoardRows<T>(rows: T[], watch: ResearchWatchView | null): T[] {
  return watch?.displacesMainBoard ? rows : rows;
}
