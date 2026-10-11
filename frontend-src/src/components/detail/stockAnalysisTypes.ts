import type { AiJob } from '../../api/types.ts';
import { normalizeAiJob } from '../../api/aiJobNormalize.ts';
export interface StockAnalysisQuota {
  limit: 1; used: 0 | 1; remaining: 0 | 1; resetAt: string; periodStart: string;
}
export interface StockAnalysisView {
  ticker: string; enabled: boolean; canGenerate: boolean;
  quota: StockAnalysisQuota | null; activeJob: AiJob | null; latestJob: AiJob | null;
}
export interface StockReportSection {
  id: string; title: string; summary: string; points: string[]; evidenceIds: string[];
}
export interface StockReport {
  headline: string; sections: StockReportSection[];
  watchItems: { what: string; why: string; reviseIf: string }[]; dataGaps: string[];
}
export interface StockReportChart {
  id: string; title: string; caption: string; kind: 'line' | 'bar';
  unit: 'usd' | 'percent' | 'score' | 'shares' | 'ratio' | 'count';
  rows: { label: string; value: number; evidenceId?: string }[];
}
export interface StockReportVisuals {
  asOf: string; charts: StockReportChart[];
  coverage: { key: string; label: string; status: string; asOf?: string }[]; notes: string[];
}

/** Fail closed on quota/version/ticker mismatches; persisted result compatibility stays separate. */
export function parseStockAnalysisView(raw: unknown, ticker: string): StockAnalysisView | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const body = raw as Record<string, unknown>;
  if (body.schema_version !== 'stock-analysis-view-v1' || body.ticker !== ticker) return null;
  const quota = body.quota && typeof body.quota === 'object' && !Array.isArray(body.quota) ? body.quota as Record<string, unknown> : {};
  const stamp = (value: unknown) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
  const validQuota = quota.limit === 1 && (quota.used === 0 || quota.used === 1)
    && (quota.remaining === 0 || quota.remaining === 1) && quota.used + quota.remaining === 1
    && stamp(quota.reset_at) && stamp(quota.period_start)
    && Date.parse(quota.reset_at as string) > Date.parse(quota.period_start as string);
  const job = (value: unknown, completed: boolean) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const normalized = normalizeAiJob(value);
    if (!normalized.id || normalized.kind !== 'signal-analysis') return null;
    if (completed ? normalized.status !== 'succeeded' : !['queued', 'in_progress', 'running'].includes(normalized.status)) return null;
    const asset = normalized.result && typeof normalized.result === 'object' ? normalized.result.asset : null;
    if (typeof asset === 'string' && asset.toUpperCase() !== ticker) return null;
    return normalized;
  };
  return { ticker, enabled: body.enabled === true, canGenerate: body.can_generate === true && validQuota && quota.remaining === 1,
    quota: validQuota ? { limit: 1, used: quota.used as 0 | 1, remaining: quota.remaining as 0 | 1,
      resetAt: quota.reset_at as string, periodStart: quota.period_start as string } : null,
    activeJob: job(body.active_job, false), latestJob: job(body.latest_job, true) };
}
