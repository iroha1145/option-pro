/** Drain one inflight persist job and settle the outbox. No React. */
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';
import {
  DrawingOutbox,
  jobBelongsToScope,
  jobIsCurrent,
  latestKnownRevision,
  resolveListApply,
  resolveSyncFailure,
  settleJob,
  releaseInflight,
  type PersistJob,
  type ScopeKey,
} from './sync.ts';

export type PersistApi = {
  create: (drawing: ChartDrawing, expected: number) => Promise<{ drawing: ChartDrawing; scopeRevision: number }>;
  update: (drawing: ChartDrawing, expected: number) => Promise<{ drawing: ChartDrawing; scopeRevision: number }>;
  remove: (
    drawingId: string,
    expected: number,
    ticker: string,
    range: ChartRange,
    adjustment: ChartAdjustment,
  ) => Promise<{ scopeRevision: number }>;
  clearScope: (
    ticker: string,
    range: ChartRange,
    expected: number,
    adjustment: ChartAdjustment,
  ) => Promise<{ scopeRevision: number }>;
  replaceScope: (
    ticker: string,
    range: ChartRange,
    drawings: ChartDrawing[],
    expected: number,
    adjustment: ChartAdjustment,
  ) => Promise<{ drawings: ChartDrawing[]; scopeRevision: number }>;
  list: (
    ticker: string,
    range: ChartRange,
    adjustment: ChartAdjustment,
  ) => Promise<{ drawings: ChartDrawing[]; scopeRevision: number }>;
};

export type PersistApply =
  | { action: 'none' }
  | { action: 'drawing'; drawing: ChartDrawing }
  | { action: 'replaceList'; drawings: ChartDrawing[] }
  | { action: 'rollback'; drawings: ChartDrawing[] }
  | { action: 'clearRevisions' }
  | { action: 'deleteRevision'; id: string };

export type DrainOutcome = {
  foreign: boolean;
  kind: 'success' | 'drop' | 'quota' | 'conflict' | 'retry' | 'superseded';
  apply: PersistApply;
  status: 'idle' | 'saving' | 'unsynced' | 'conflict' | 'write_failed' | null;
  hint: string | null;
  readyIds: string[];
  conflict: {
    scope: ScopeKey;
    scopeGeneration: number;
    scopeRevision: number;
    drawings: ChartDrawing[];
  } | null;
  lastServer?: ChartDrawing[];
  scopeRevision?: number;
  reconcile: boolean;
  /** 429 时服务器给的等待秒数；其余 retry 失败为 null/缺省。 */
  retryAfterSeconds?: number | null;
};

const FOREIGN: DrainOutcome = {
  foreign: true,
  kind: 'superseded',
  apply: { action: 'none' },
  status: null,
  hint: null,
  readyIds: [],
  conflict: null,
  reconcile: false,
};

function readError(
  error: unknown,
  errorInfo?: (value: unknown) => { code: string | null; status: number | null },
): { code: string | null; status: number | null } {
  if (errorInfo) return errorInfo(error);
  const row = error && typeof error === 'object' ? error as { code?: unknown; status?: unknown; bizCode?: unknown } : {};
  const code = typeof row.bizCode === 'string' ? row.bizCode : typeof row.code === 'string' ? row.code : null;
  const status = typeof row.status === 'number' ? row.status : null;
  return { code, status };
}

function upsertServerDrawing(
  drawings: ChartDrawing[],
  saved: ChartDrawing,
): ChartDrawing[] {
  const index = drawings.findIndex((item) => item.id === saved.id);
  if (index < 0) return [...drawings, saved];
  return drawings.map((item, itemIndex) => (itemIndex === index ? saved : item));
}

/**
 * Run one already-inflight job to completion.
 *
 * Same ticker/scope: always release the inflight slot so a later clear/replace
 * can run, even if enqueue(clear) bumped scopeGeneration. Different ticker:
 * no-op (do not complete/drop the current outbox).
 */
export async function drainPersistJob(args: {
  outbox: DrawingOutbox;
  job: PersistJob;
  api: PersistApi;
  drawings: ChartDrawing[];
  lastServer: ChartDrawing[];
  revisions: Map<string, number>;
  localDirty?: boolean;
  errorInfo?: (error: unknown) => { code: string | null; status: number | null };
}): Promise<DrainOutcome> {
  const { outbox, job, api } = args;
  const expected = job.expectedScopeRevision ?? outbox.getScopeRevision();
  try {
    let apply: PersistApply = { action: 'none' };
    let scopeRevision: number | undefined;
    let lastServer: ChartDrawing[] | undefined;
    if (job.type === 'create' && job.drawing) {
      const saved = await api.create(job.drawing, expected);
      scopeRevision = saved.scopeRevision;
      apply = { action: 'drawing', drawing: saved.drawing };
      lastServer = upsertServerDrawing(args.lastServer, saved.drawing);
    } else if (job.type === 'update' && job.drawing) {
      const local = args.drawings.find((item) => item.id === job.drawing?.id) ?? job.drawing;
      const revision = latestKnownRevision(args.revisions, job.drawing.id, local.revision);
      const saved = await api.update({ ...job.drawing, revision }, expected);
      scopeRevision = saved.scopeRevision;
      apply = { action: 'drawing', drawing: saved.drawing };
      lastServer = upsertServerDrawing(args.lastServer, saved.drawing);
    } else if (job.type === 'delete') {
      const removed = await api.remove(
        job.drawingId,
        expected,
        job.scope.ticker,
        job.scope.range,
        job.scope.adjustment,
      );
      scopeRevision = removed.scopeRevision;
      apply = { action: 'deleteRevision', id: job.drawingId };
      lastServer = args.lastServer.filter((item) => item.id !== job.drawingId);
    } else if (job.type === 'clear') {
      const cleared = await api.clearScope(
        job.scope.ticker,
        job.scope.range,
        expected,
        job.scope.adjustment,
      );
      scopeRevision = cleared.scopeRevision;
      apply = { action: 'clearRevisions' };
      lastServer = [];
    } else if (job.type === 'replace' && job.drawings) {
      const listed = await api.replaceScope(
        job.scope.ticker,
        job.scope.range,
        job.drawings,
        expected,
        job.scope.adjustment,
      );
      scopeRevision = listed.scopeRevision;
      lastServer = listed.drawings;
      if (resolveListApply(
        outbox.isEmptyExcept(job.drawingId, job.generation),
        jobIsCurrent(job, outbox.getScope(), outbox.getScopeGeneration()),
      )) {
        apply = { action: 'replaceList', drawings: listed.drawings };
      }
    } else {
      releaseInflight(outbox, job, 'drop');
      const settled = settleJob({ outbox, job, kind: 'drop' });
      return { foreign: false, kind: 'drop', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
    }

    if (!jobBelongsToScope(job, outbox.getScope())) return FOREIGN;
    // A confirmed mutation is now the concurrency base for every job behind it.
    // This must happen for superseded jobs too: an update that finished while a
    // clear/replace was queued still advanced the server scope revision.
    if (scopeRevision != null) outbox.rebaseBase(scopeRevision);
    const applies = jobIsCurrent(job, outbox.getScope(), outbox.getScopeGeneration());
    releaseInflight(outbox, job, 'complete');
    if (!applies) {
      const settled = settleJob({ outbox, job, kind: 'superseded' });
      return { foreign: false, kind: 'superseded', apply: { action: 'none' }, conflict: null, lastServer, scopeRevision, reconcile: false, ...settled };
    }
    const settled = settleJob({ outbox, job, kind: 'success' });
    return {
      foreign: false,
      kind: 'success',
      apply,
      conflict: null,
      lastServer,
      scopeRevision,
      reconcile: settled.status === 'idle',
      ...settled,
    };
  } catch (error) {
    if (!jobBelongsToScope(job, outbox.getScope())) return FOREIGN;
    const { code, status } = readError(error, args.errorInfo);
    const applies = jobIsCurrent(job, outbox.getScope(), outbox.getScopeGeneration());
    if (job.type === 'update' && (code === 'drawing_not_found' || status === 404)) {
      if (!applies) {
        releaseInflight(outbox, job, 'drop');
        const settled = settleJob({ outbox, job, kind: 'superseded' });
        return { foreign: false, kind: 'superseded', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
      }
      releaseInflight(outbox, job, 'drop');
      const stillLocal = args.drawings.some((item) => item.id === job.drawingId);
      if (!stillLocal) {
        const settled = settleJob({ outbox, job, kind: 'drop' });
        return { foreign: false, kind: 'drop', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
      }
      try {
        const remote = await api.list(job.scope.ticker, job.scope.range, job.scope.adjustment);
        if (!jobBelongsToScope(job, outbox.getScope())) return FOREIGN;
        if (!jobIsCurrent(job, outbox.getScope(), outbox.getScopeGeneration())) {
          const settled = settleJob({ outbox, job, kind: 'superseded' });
          return { foreign: false, kind: 'superseded', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
        }
        const settled = settleJob({ outbox, job, kind: 'conflict' });
        return {
          foreign: false,
          kind: 'conflict',
          apply: { action: 'none' },
          conflict: {
            scope: job.scope,
            scopeGeneration: job.scopeGeneration,
            scopeRevision: remote.scopeRevision,
            drawings: remote.drawings,
          },
          lastServer: remote.drawings,
          scopeRevision: remote.scopeRevision,
          reconcile: false,
          ...settled,
        };
      } catch {
        if (!jobBelongsToScope(job, outbox.getScope())) return FOREIGN;
        const settled = settleJob({ outbox, job, kind: 'conflict' });
        return { foreign: false, kind: 'conflict', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
      }
    }
    const failure = resolveSyncFailure(job.type, code, status);
    if (!applies) {
      releaseInflight(outbox, job, 'drop');
      const settled = settleJob({ outbox, job, kind: 'superseded' });
      return { foreign: false, kind: 'superseded', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
    }
    if (failure === 'drop') {
      releaseInflight(outbox, job, 'drop');
      const settled = settleJob({ outbox, job, kind: 'drop' });
      return { foreign: false, kind: 'drop', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
    }
    if (failure === 'quota') {
      releaseInflight(outbox, job, 'drop');
      const settled = settleJob({ outbox, job, kind: 'quota' });
      return {
        foreign: false,
        kind: 'quota',
        apply: { action: 'rollback', drawings: args.lastServer },
        conflict: null,
        lastServer: args.lastServer,
        reconcile: false,
        ...settled,
      };
    }
    releaseInflight(outbox, job, 'failKeep');
    if (failure === 'conflict') {
      try {
        const remote = await api.list(job.scope.ticker, job.scope.range, job.scope.adjustment);
        if (!jobBelongsToScope(job, outbox.getScope())) return FOREIGN;
        const settled = settleJob({ outbox, job, kind: 'conflict' });
        return {
          foreign: false,
          kind: 'conflict',
          apply: { action: 'none' },
          conflict: {
            scope: job.scope,
            scopeGeneration: job.scopeGeneration,
            scopeRevision: remote.scopeRevision,
            drawings: remote.drawings,
          },
          lastServer: remote.drawings,
          scopeRevision: remote.scopeRevision,
          reconcile: false,
          ...settled,
        };
      } catch {
        if (!jobBelongsToScope(job, outbox.getScope())) return FOREIGN;
        const settled = settleJob({ outbox, job, kind: 'conflict' });
        return { foreign: false, kind: 'conflict', apply: { action: 'none' }, conflict: null, reconcile: false, ...settled };
      }
    }
    const settled = settleJob({ outbox, job, kind: 'retry' });
    // 429 的 Retry-After（秒）随 outcome 透出：这是服务器亲口说的「几秒后再来」，
    // 调用方按它排定时自动重放，而不是把任务搁成 unsynced 等人手点。
    const row = error && typeof error === 'object' ? error as { retryAfter?: unknown } : {};
    const retryAfterSeconds = typeof row.retryAfter === 'number' && Number.isFinite(row.retryAfter)
      ? row.retryAfter
      : null;
    return { foreign: false, kind: 'retry', apply: { action: 'none' }, conflict: null, reconcile: false, retryAfterSeconds, ...settled };
  }
}
