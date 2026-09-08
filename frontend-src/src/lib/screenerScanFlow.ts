import { toQuery } from '../api/client.ts';
import type { ScanParams, StrengthScanEnvelope } from '../api/modules/strength.ts';
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
  // A trading-day label does not identify a scan completion instant.
  if (DATE_ONLY.test(trimmed)) return null;
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

/** An HTTP-cached body cannot remain fresh after its server-provided expiry. */
export function expireStrengthSnapshot(snapshot: StrengthScanEnvelope, nowMs = Date.now()): StrengthScanEnvelope {
  if (snapshot.stale || !snapshot.cacheExpiresAt) return snapshot;
  const expiresAt = Date.parse(snapshot.cacheExpiresAt);
  if (!Number.isFinite(expiresAt) || expiresAt > nowMs) return snapshot;
  return { ...snapshot, stale: true, sourceStatus: 'stale', staleReason: 'worker_snapshot_expired' };
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

export function shouldCommitScanGeneration(started: number, current: number): boolean {
  return started === current;
}

export function shouldLockScanTrigger(input: {
  scanning: boolean;
  draftMatchesInFlight: boolean;
}): boolean {
  return input.scanning && input.draftMatchesInFlight;
}

export function workerWaitDecision(status: string): 'done' | 'failed' | 'poll' {
  if (status === 'completed') return 'done';
  if (status === 'failed' || status === 'cancelled' || status === 'canceled') return 'failed';
  return 'poll';
}

export function workerWaitHasTimedOut(nowMs: number, deadlineMs: number): boolean {
  return nowMs >= deadlineMs;
}

export function refreshActionMatchesRequest(
  action: { details?: { parameters?: unknown; result?: unknown } },
  expected: StrengthRefreshParameters,
): boolean {
  if (!strengthParametersMatch(action.details?.parameters, expected)) return false;
  const result = action.details?.result;
  if (result && typeof result === 'object' && 'parameters' in result) {
    return strengthParametersMatch(result.parameters, expected);
  }
  return true;
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
  // Keep a trading-day label in its original calendar, including in Asia.
  if (value && DATE_ONLY.test(value.trim())) return value.trim();
  const clock = parseScanClock(value);
  if (clock == null) {
    if (value && DATE_ONLY.test(value.trim())) return value.trim();
    return null;
  }
  // Actual scanner timestamps identify a US trading session, regardless of
  // the browser's timezone. Keep the score date on New York's calendar.
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(clock));
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((value) => value.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

export interface PendingStrengthTask {
  requestId: string;
  parameters: StrengthRefreshParameters;
  storedAt: number;
  principal?: string;
}

export function readPendingStrengthTask(principal?: string): PendingStrengthTask | null {
  try {
    if (typeof sessionStorage === 'undefined') return null;
    const raw = sessionStorage.getItem(PENDING_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingStrengthTask;
    if (!parsed || typeof parsed.requestId !== 'string' || !parsed.requestId) return null;
    if (!parsed.parameters || typeof parsed.parameters !== 'object') return null;
    if (principal !== undefined && parsed.principal !== principal) return null;
    if (!Number.isFinite(parsed.storedAt) || Date.now() - parsed.storedAt > 86_400_000) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function writePendingStrengthTask(task: PendingStrengthTask): void {
  try {
    if (typeof sessionStorage === 'undefined') return;
    sessionStorage.setItem(PENDING_STORAGE_KEY, JSON.stringify(task));
  } catch {
    // Storage can be disabled or full; the accepted task is still valid.
  }
}

export function clearPendingStrengthTask(requestId?: string): void {
  try {
    if (typeof sessionStorage === 'undefined') return;
    if (requestId && readPendingStrengthTask()?.requestId !== requestId) return;
    sessionStorage.removeItem(PENDING_STORAGE_KEY);
  } catch {
    // Failure to save recovery state must not turn a successful scan into an error.
  }
}

/** A completed queue record alone is not proof that this publication is readable. */
export function strengthPublicationMatches(
  snapshot: Pick<StrengthScanEnvelope, 'snapshotSavedAt' | 'scoreVersion' | 'stale' | 'sourceStatus'>,
  action: WorkerAction,
): boolean {
  const result = action.details.result;
  if (!result || typeof result !== 'object') return false;
  const published = result as Record<string, unknown>;
  const completedAt = parseScanClock(typeof published.completed_at === 'string' ? published.completed_at : null);
  const savedAt = parseScanClock(snapshot.snapshotSavedAt);
  if (completedAt == null || savedAt == null || savedAt < completedAt) return false;
  if (snapshot.stale || snapshot.sourceStatus !== 'active') return false;
  if (savedAt > completedAt) return true; // A later valid publication may supersede this task.
  return typeof published.score_version === 'string' && snapshot.scoreVersion === published.score_version;
}
