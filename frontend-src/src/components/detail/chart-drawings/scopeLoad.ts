/** Scope switch / conflict apply / area-mode layer gates. No React. */
import { evaluateRemoteVsPending } from './merge.ts';
import { drawingsFromCache, type LoadResult } from './storage.ts';
import {
  conflictSnapshotUsable,
  resolveListApply,
  type DrawingOutbox,
  type ScopeKey,
} from './sync.ts';
import type { ChartDrawing } from './types.ts';

export type ChartRenderMode = 'candle' | 'area';

export type ScopePreview = {
  drawings: ChartDrawing[];
  persist: 'now' | 'skip';
  status: 'idle' | 'load_failed';
  hint: string | null;
};

/**
 * Cache-only view of a newly entered scope. `ok` with `drawings=[]` is
 * authoritative: never fall back to the previous ticker's in-memory list.
 */
export function previewScopeLoad(cached: LoadResult): ScopePreview {
  const drawings = drawingsFromCache(cached);
  if (cached.ok) {
    return {
      drawings,
      persist: drawings.length ? 'now' : 'skip',
      status: 'idle',
      hint: null,
    };
  }
  if (cached.missing) {
    return { drawings: [], persist: 'skip', status: 'idle', hint: null };
  }
  return {
    drawings,
    persist: drawings.length ? 'now' : 'skip',
    status: 'load_failed',
    hint: cached.recoverable ? null : 'local_corrupt',
  };
}

export type ScopeLoadComplete = {
  foreign: boolean;
  apply: 'remote' | 'cache' | 'replay' | 'none';
  drawings: ChartDrawing[];
  persist: 'now' | 'skip';
  status: 'idle' | 'saving' | 'load_failed' | 'write_failed' | 'guest' | 'conflict';
  hint: string | null;
  lastServer?: ChartDrawing[];
  scopeRevision?: number;
  drain: boolean;
  /** 列表被 429/断网挡下时的等待秒数（服务器给了就带）；成功路径缺省。 */
  retryAfterSeconds?: number | null;
  conflict: boolean;
  baselineReady: boolean;
};

const FOREIGN: ScopeLoadComplete = {
  foreign: true,
  apply: 'none',
  drawings: [],
  persist: 'skip',
  status: 'idle',
  hint: null,
  drain: false,
  conflict: false,
  baselineReady: false,
};

function isAuthStatus(
  error: unknown,
  errorInfo?: (value: unknown) => { code: string | null; status: number | null },
): boolean {
  if (errorInfo) return errorInfo(error).status === 401;
  const row = error && typeof error === 'object' ? error as { status?: unknown; code?: unknown } : {};
  return row.status === 401 || row.code === 401;
}

/**
 * Await the GET, then apply only if generation still matches.
 * Pending jobs replay onto remote.drawings; they never inherit the GET
 * revision unless evaluateRemoteVsPending says the send is safe.
 */
export async function completeScopeLoad(args: {
  generation: number;
  outbox: DrawingOutbox;
  cached: LoadResult;
  list: () => Promise<{ drawings: ChartDrawing[]; scopeRevision: number }>;
  errorInfo?: (error: unknown) => { code: string | null; status: number | null };
}): Promise<ScopeLoadComplete> {
  const { outbox, cached } = args;
  try {
    const remote = await args.list();
    if (outbox.getScopeGeneration() !== args.generation) return FOREIGN;
    if (outbox.isEmpty()) {
      outbox.setScopeRevision(remote.scopeRevision);
      outbox.clearBase();
      outbox.markBaselineReady();
      if (!resolveListApply(true, true)) {
        return {
          foreign: false,
          apply: 'none',
          drawings: drawingsFromCache(cached),
          persist: 'skip',
          status: 'saving',
          hint: null,
          lastServer: remote.drawings,
          scopeRevision: remote.scopeRevision,
          drain: false,
          conflict: false,
          baselineReady: true,
        };
      }
      return {
        foreign: false,
        apply: 'remote',
        drawings: remote.drawings,
        persist: 'now',
        status: 'idle',
        hint: null,
        lastServer: remote.drawings,
        scopeRevision: remote.scopeRevision,
        drain: false,
        conflict: false,
        baselineReady: true,
      };
    }
    const evaluation = evaluateRemoteVsPending({
      remoteDrawings: remote.drawings,
      remoteRevision: remote.scopeRevision,
      baseRevision: outbox.getBaseScopeRevision(),
      jobs: outbox.snapshot(),
    });
    outbox.markBaselineReady();
    if (evaluation.adoptScopeRevision) outbox.setSendScopeRevision(remote.scopeRevision);
    if (evaluation.kind === 'conflict') {
      return {
        foreign: false,
        apply: 'replay',
        drawings: evaluation.drawings,
        persist: 'now',
        status: 'conflict',
        hint: 'conflict',
        lastServer: remote.drawings,
        scopeRevision: remote.scopeRevision,
        drain: false,
        conflict: true,
        baselineReady: true,
      };
    }
    return {
      foreign: false,
      apply: 'replay',
      drawings: evaluation.drawings,
      persist: 'now',
      status: 'saving',
      hint: null,
      lastServer: remote.drawings,
      scopeRevision: remote.scopeRevision,
      drain: evaluation.drain,
      conflict: false,
      baselineReady: true,
    };
  } catch (error) {
    if (outbox.getScopeGeneration() !== args.generation) return FOREIGN;
    const drawings = drawingsFromCache(cached);
    const persist: 'now' | 'skip' = cached.ok || drawings.length ? 'now' : 'skip';
    if (isAuthStatus(error, args.errorInfo)) {
      return {
        foreign: false,
        apply: 'cache',
        drawings,
        persist,
        status: 'guest',
        hint: null,
        drain: false,
        conflict: false,
        baselineReady: false,
      };
    }
    const row = error && typeof error === 'object' ? error as { retryAfter?: unknown } : {};
    const retryAfterSeconds = typeof row.retryAfter === 'number' && Number.isFinite(row.retryAfter)
      ? row.retryAfter
      : null;
    return {
      foreign: false,
      apply: 'cache',
      drawings,
      persist,
      status: outbox.isEmpty() ? 'load_failed' : 'write_failed',
      hint: 'unsynced',
      retryAfterSeconds,
      drain: false,
      conflict: false,
      baselineReady: false,
    };
  }
}

export type ConflictSnapshot = {
  scope: ScopeKey;
  scopeGeneration: number;
  scopeRevision: number;
  drawings: ChartDrawing[];
};

/**
 * Keep/take a conflict snapshot. Foreign scope or generation is a no-op:
 * the caller must not cancelAll or apply the list.
 */
export function applyConflictDecision(args: {
  snapshot: ConflictSnapshot | null;
  currentScope: ScopeKey | null;
  generation: number;
  intent: 'keep' | 'take';
}): { action: 'ignore' | 'keep' | 'take'; snapshot: ConflictSnapshot | null } {
  if (!conflictSnapshotUsable(args.snapshot, args.currentScope, args.generation)) {
    return { action: 'ignore', snapshot: args.snapshot };
  }
  return { action: args.intent, snapshot: args.snapshot };
}

/** Area mode cannot paint panes or MA; the checkbox must be disabled, not only captioned. */
export function layerInputEnabled(
  layer: { group: string; kind: string },
  mode: ChartRenderMode,
): { enabled: boolean; reason: string | null } {
  if (mode === 'area' && (layer.group === 'pane' || layer.kind === 'ma')) {
    return { enabled: false, reason: 'area_no_panes_or_ma' };
  }
  return { enabled: true, reason: null };
}
