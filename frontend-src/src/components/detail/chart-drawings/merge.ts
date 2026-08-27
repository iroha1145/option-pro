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
 * the target is gone, the frozen drawing revision is missing, or that
 * revision changed. Unrelated remote rows stay. Creates replay only when
 * the id is free.
 */

function frozenDrawingRevision(job: PersistJob): number | null {
  const tagged = job.expectedDrawingRevision;
  if (typeof tagged === 'number' && Number.isInteger(tagged) && tagged >= 1) return tagged;
  const fromDrawing = job.drawing?.revision;
  if (typeof fromDrawing === 'number' && Number.isInteger(fromDrawing) && fromDrawing >= 1) {
    return fromDrawing;
  }
  return null;
}

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
  const workingById = new Map(remoteById);
  const locallyCreated = new Set<string>();
  let unsafe = false;
  if (mismatch) {
    for (const job of jobs) {
      if (job.type === 'clear' || job.type === 'replace') {
        unsafe = true;
        break;
      }
      if (job.type === 'create') {
        if (!job.drawing || workingById.has(job.drawingId)) {
          unsafe = true;
          break;
        }
        workingById.set(job.drawingId, job.drawing);
        locallyCreated.add(job.drawingId);
        continue;
      }
      if (job.type === 'update' || job.type === 'delete') {
        const row = workingById.get(job.drawingId);
        if (!row) {
          unsafe = true;
          break;
        }
        // A target created earlier in this same local queue has no remote
        // revision yet; create→update/delete is nevertheless a safe replay.
        if (!locallyCreated.has(job.drawingId)) {
          const localRev = frozenDrawingRevision(job);
          if (localRev == null || row.revision !== localRev) {
            unsafe = true;
            break;
          }
        }
        if (job.type === 'update') {
          if (!job.drawing) {
            unsafe = true;
            break;
          }
          workingById.set(job.drawingId, {
            ...job.drawing,
            revision: row.revision,
          });
        } else {
          workingById.delete(job.drawingId);
          locallyCreated.delete(job.drawingId);
        }
      }
    }
  }
  if (unsafe) {
    return {
      kind: 'conflict',
      // The conflict view must represent the real local intent. Dropping a
      // replace/clear here made “keep local” silently lose imported edits.
      drawings: replayPendingOps(args.remoteDrawings, jobs),
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
