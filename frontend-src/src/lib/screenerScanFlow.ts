import { toQuery } from '../api/client.ts';
import type { ScanParams } from '../api/modules/strength.ts';
import type { StrengthRefreshParameters, WorkerAction } from '../api/modules/runtime.ts';
import { fallbackQuoteLabel } from './liveQuotes.ts';

export { fallbackQuoteLabel };

export type StrengthScanPhase =
  | 'reading'
  | 'queued'
  | 'running'
  | 'verifying'
  | 'reused'
  | 'done'
  | 'failed';

export interface StrengthScanTimes {
  queryCheckedAt: number;
  scanCompletedAt: number | null;
  snapshotSavedAt: number | null;
  scoreDataThrough: string | null;
  reusedExisting: boolean;
}

export interface StrengthRefreshDecision {
  submit: boolean;
  reason: 'force' | 'missing' | 'stale' | 'unknown' | 'reuse';
}

const DATE_ONLY = /^(\d{4}-\d{2}-\d{2})$/;
const PENDING_STORAGE_KEY = 'optix:screener-pending-strength';

export function strengthScanPath(params: ScanParams): string {
  const qs = toQuery({
    universe: params.universe,
    timeframe: params.timeframe,
    profile: params.profile,
    top: params.top,
    sector_id: params.sector_id ?? params.sector,
    min_price: params.min_price,
    min_avg_dollar_volume: params.min_avg_dollar_volume,
    include_options: params.include_options,
  });
  return `/strength/scan${qs ? `?${qs}` : ''}`;
}

export function shouldSubmitStrengthRefresh(input: {
  isOwner: boolean;
  isMock: boolean;
  forceRefresh: boolean;
  snapshotMissing: boolean;
  snapshotStale: boolean;
  sourceStatus?: string | null;
}): StrengthRefreshDecision {
  if (!input.isOwner || input.isMock) {
    return { submit: false, reason: input.snapshotMissing ? 'missing' : 'reuse' };
  }
  if (input.forceRefresh) return { submit: true, reason: 'force' };
  if (input.snapshotMissing) return { submit: true, reason: 'missing' };
  if (input.snapshotStale) return { submit: true, reason: 'stale' };
  if (input.sourceStatus === 'unknown' || input.sourceStatus === 'historical') {
    return { submit: true, reason: 'unknown' };
  }
  return { submit: false, reason: 'reuse' };
}

export function parseScanClock(value: string | null | undefined): number | null {
  if (!value || typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const dateOnly = DATE_ONLY.exec(trimmed);
  if (dateOnly) {
    const parsed = Date.parse(`${dateOnly[1]}T20:00:00.000Z`);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }
  const parsed = Date.parse(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  if (parsed > Date.now() + 2 * 86_400_000) return null;
  return parsed;
}

export function scanDisplayTimes(input: {
  queryCheckedAt: number;
  snapshotSavedAt?: string | null;
  scanCompletedAt?: string | null;
  scoreDataThrough?: string | null;
  stale: boolean;
  submittedRefresh: boolean;
}): StrengthScanTimes {
  const snapshotSavedAt = parseScanClock(input.snapshotSavedAt);
  const scanCompletedAt = parseScanClock(input.scanCompletedAt) ?? snapshotSavedAt;
  return {
    queryCheckedAt: input.queryCheckedAt,
    scanCompletedAt,
    snapshotSavedAt,
    scoreDataThrough: input.scoreDataThrough ?? null,
    reusedExisting: !input.submittedRefresh && !input.stale,
  };
}

export function strengthParametersMatch(
  actual: unknown,
  expected: StrengthRefreshParameters,
): boolean {
  if (!actual || typeof actual !== 'object') return false;
  const value = actual as Record<string, unknown>;
  return (
    value.universe === expected.universe
    && value.timeframe === expected.timeframe
    && value.profile === expected.profile
    && value.top === expected.top
    && value.sector_id === expected.sector_id
    && value.min_price === expected.min_price
    && value.min_avg_dollar_volume === expected.min_avg_dollar_volume
    && value.include_options === expected.include_options
  );
}

export function shouldDiscoverPublishedScan(input: {
  scanState: string;
  visibilityState: string;
  isMock: boolean;
}): boolean {
  return input.scanState === 'done' && !input.isMock && input.visibilityState === 'visible';
}

export function workerActionPhase(action: Pick<WorkerAction, 'status'>): StrengthScanPhase {
  const status = action.status;
  if (status === 'queued' || status === 'accepted') return 'queued';
  if (status === 'running' || status === 'started') return 'running';
  if (status === 'completed') return 'verifying';
  if (status === 'failed' || status === 'cancelled' || status === 'canceled') return 'failed';
  return 'reading';
}

export function visibleScanDate(value: string | null | undefined): string | null {
  const clock = parseScanClock(value);
  if (clock == null) {
    if (value && DATE_ONLY.test(value.trim())) return value.trim();
    return null;
  }
  const date = new Date(clock);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

export interface PendingStrengthTask {
  requestId: string;
  parameters: StrengthRefreshParameters;
  storedAt: number;
}

export function readPendingStrengthTask(): PendingStrengthTask | null {
  if (typeof sessionStorage === 'undefined') return null;
  try {
    const raw = sessionStorage.getItem(PENDING_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingStrengthTask;
    if (!parsed || typeof parsed.requestId !== 'string' || !parsed.requestId) return null;
    if (!parsed.parameters || typeof parsed.parameters !== 'object') return null;
    return parsed;
  } catch {
    return null;
  }
}

export function writePendingStrengthTask(task: PendingStrengthTask): void {
  if (typeof sessionStorage === 'undefined') return;
  sessionStorage.setItem(PENDING_STORAGE_KEY, JSON.stringify(task));
}

export function clearPendingStrengthTask(): void {
  if (typeof sessionStorage === 'undefined') return;
  sessionStorage.removeItem(PENDING_STORAGE_KEY);
}
