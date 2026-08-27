/** Replay pending outbox jobs onto a server list. No React. */
import { scopeEquals, type PersistJob, type ScopeKey } from './sync.ts';
import type { ChartDrawing } from './types.ts';

export function replayPendingOps(
  remoteDrawings: ChartDrawing[],
  orderedJobs: PersistJob[],
): ChartDrawing[] {
  let list = remoteDrawings.map((row) => ({ ...row }));
  const jobs = [...orderedJobs].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  for (const job of jobs) {
    if (job.type === 'create' && job.drawing) {
      if (!list.some((row) => row.id === job.drawing?.id)) list = [...list, job.drawing];
    } else if (job.type === 'update' && job.drawing) {
      const next = job.drawing;
      list = list.map((row) => (
        row.id === next.id ? { ...next, revision: row.revision } : row
      ));
    } else if (job.type === 'delete') {
      list = list.filter((row) => row.id !== job.drawingId);
    } else if (job.type === 'clear') {
      list = [];
    } else if (job.type === 'replace' && job.drawings) {
      list = job.drawings.map((row) => ({ ...row }));
    }
  }
  return list;
}

export type PendingEval = {
  kind: 'merge' | 'conflict';
  drawings: ChartDrawing[];
  drain: boolean;
  adoptScopeRevision: boolean;
};

/**
 * Compare GET revision against the immutable outbox base.
 * Clear/replace on mismatch is always conflict. Update/delete conflict when
 * the target is gone or its drawing revision changed. Unrelated remote rows
 * stay. Creates replay only when the id is free.
 */
export function evaluateRemoteVsPending(args: {
  remoteDrawings: ChartDrawing[];
  remoteRevision: number;
  baseRevision: number | null;
  jobs: PersistJob[];
}): PendingEval {
  const jobs = [...args.jobs].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  if (!jobs.length) {
    return {
      kind: 'merge',
      drawings: args.remoteDrawings,
      drain: false,
      adoptScopeRevision: false,
    };
  }
  const mismatch = args.baseRevision != null && args.remoteRevision !== args.baseRevision;
  const remoteById = new Map(args.remoteDrawings.map((row) => [row.id, row]));
  let unsafe = false;
  if (mismatch) {
    for (const job of jobs) {
      if (job.type === 'clear' || job.type === 'replace') {
        unsafe = true;
        break;
      }
      if (job.type === 'create' && remoteById.has(job.drawingId)) {
        unsafe = true;
        break;
      }
      if (job.type === 'update' || job.type === 'delete') {
        const row = remoteById.get(job.drawingId);
        if (!row) {
          unsafe = true;
          break;
        }
        const localRev = job.drawing?.revision;
        if (localRev != null && row.revision !== localRev) {
          unsafe = true;
          break;
        }
      }
    }
  }
  if (unsafe) {
    const skipBarrier = jobs.filter((job) => job.type !== 'clear' && job.type !== 'replace');
    return {
      kind: 'conflict',
      drawings: replayPendingOps(args.remoteDrawings, skipBarrier),
      drain: false,
      adoptScopeRevision: false,
    };
  }
  return {
    kind: 'merge',
    drawings: replayPendingOps(args.remoteDrawings, jobs),
    drain: true,
    adoptScopeRevision: mismatch,
  };
}

export type ServerSnapshot = {
  scope: ScopeKey;
  scopeGeneration: number;
  drawings: ChartDrawing[];
};

/** Quota / take-server / conflict rollback may only use this scope+generation. */
export function quotaRollbackDrawings(
  snapshot: ServerSnapshot | null,
  currentScope: ScopeKey | null,
  generation: number,
): ChartDrawing[] {
  if (!snapshot) return [];
  if (!scopeEquals(snapshot.scope, currentScope)) return [];
  if (snapshot.scopeGeneration !== generation) return [];
  return snapshot.drawings;
}
