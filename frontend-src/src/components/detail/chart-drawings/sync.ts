/** Per-id serial outbox, scope tokens, and full-field undo diffs. */
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';

export const SCOPE_JOB_ID = '__scope__';

export interface ScopeKey {
  identity: string;
  ticker: string;
  range: ChartRange;
  adjustment: ChartAdjustment;
}

export type PersistOpType = 'create' | 'update' | 'delete' | 'clear' | 'replace';

export interface PersistJob {
  drawingId: string;
  generation: number;
  scopeGeneration: number;
  scope: ScopeKey;
  type: PersistOpType;
  drawing?: ChartDrawing;
  drawings?: ChartDrawing[];
}

export function scopeEquals(a: ScopeKey | null, b: ScopeKey | null): boolean {
  if (!a || !b) return false;
  return (
    a.identity === b.identity
    && a.ticker === b.ticker
    && a.range === b.range
    && a.adjustment === b.adjustment
  );
}

export function mutableFieldsOf(drawing: ChartDrawing) {
  return {
    anchors: drawing.anchors,
    color: drawing.style.color,
    width: drawing.style.width,
    dash: drawing.style.dash,
    fillOpacity: drawing.style.fillOpacity ?? null,
    text: drawing.text ?? '',
    locked: drawing.locked,
    hidden: drawing.hidden,
    zOrder: drawing.zOrder,
  };
}

export function mutableFieldsDiffer(a: ChartDrawing, b: ChartDrawing): boolean {
  return JSON.stringify(mutableFieldsOf(a)) !== JSON.stringify(mutableFieldsOf(b));
}

export type HistoryPersistOp =
  | { type: 'create'; drawing: ChartDrawing }
  | { type: 'update'; drawing: ChartDrawing }
  | { type: 'delete'; id: string };

export function diffPersistOps(prev: ChartDrawing[], next: ChartDrawing[]): HistoryPersistOp[] {
  const prevMap = new Map(prev.map((item) => [item.id, item]));
  const nextMap = new Map(next.map((item) => [item.id, item]));
  const ops: HistoryPersistOp[] = [];
  for (const [id] of prevMap) {
    if (!nextMap.has(id)) ops.push({ type: 'delete', id });
  }
  for (const [id, drawing] of nextMap) {
    const old = prevMap.get(id);
    if (!old) ops.push({ type: 'create', drawing });
    else if (mutableFieldsDiffer(old, drawing)) ops.push({ type: 'update', drawing });
  }
  return ops;
}

export function resolveListApply(outboxEmpty: boolean, tokenMatch: boolean): boolean {
  return outboxEmpty && tokenMatch;
}

export function resolveRetryAction(outboxEmpty: boolean): 'replay' | 'idle' {
  return outboxEmpty ? 'idle' : 'replay';
}

export type ApplyAction =
  | { action: 'replace'; drawing: ChartDrawing }
  | { action: 'revision'; id: string; revision: number }
  | { action: 'ignore' };

export function applyPersistResponse(args: {
  job: PersistJob;
  currentScope: ScopeKey | null;
  currentScopeGeneration: number;
  latestGenerationForId: number;
  responseDrawing: ChartDrawing | null;
}): ApplyAction {
  const { job } = args;
  if (!scopeEquals(job.scope, args.currentScope)) return { action: 'ignore' };
  if (job.scopeGeneration !== args.currentScopeGeneration) return { action: 'ignore' };
  if (!args.responseDrawing) return { action: 'ignore' };
  if (job.generation === args.latestGenerationForId) {
    return { action: 'replace', drawing: args.responseDrawing };
  }
  return { action: 'revision', id: args.responseDrawing.id, revision: args.responseDrawing.revision };
}

type Chain = {
  generation: number;
  pending: PersistJob[];
  inflight: PersistJob | null;
};

/** Keep local mutable fields and adopt the server revision so a retry can PUT. */
export function keepLocalWithServerRevisions(
  local: ChartDrawing[],
  server: ChartDrawing[],
): ChartDrawing[] {
  const remote = new Map(server.map((item) => [item.id, item]));
  return local.map((item) => {
    const other = remote.get(item.id);
    return other ? { ...item, revision: other.revision } : item;
  });
}

export class DrawingOutbox {
  private scopeGeneration = 0;
  private currentScope: ScopeKey | null = null;
  private chains = new Map<string, Chain>();

  getScope(): ScopeKey | null {
    return this.currentScope;
  }

  getScopeGeneration(): number {
    return this.scopeGeneration;
  }

  setScope(scope: ScopeKey): number {
    this.currentScope = scope;
    this.scopeGeneration += 1;
    this.chains.clear();
    return this.scopeGeneration;
  }

  isEmpty(): boolean {
    for (const chain of this.chains.values()) {
      if (chain.inflight || chain.pending.length) return false;
    }
    return true;
  }

  latestGeneration(drawingId: string): number {
    return this.chains.get(drawingId)?.generation ?? 0;
  }

  private chain(drawingId: string): Chain {
    let row = this.chains.get(drawingId);
    if (!row) {
      row = { generation: 0, pending: [], inflight: null };
      this.chains.set(drawingId, row);
    }
    return row;
  }

  enqueue(partial: Omit<PersistJob, 'generation' | 'scopeGeneration' | 'scope'>): PersistJob | null {
    if (!this.currentScope) return null;
    if (partial.type === 'replace' || partial.type === 'clear') {
      for (const [id, chain] of this.chains) {
        if (id === SCOPE_JOB_ID) continue;
        chain.pending = [];
      }
    }
    const id = partial.type === 'clear' || partial.type === 'replace' ? SCOPE_JOB_ID : partial.drawingId;
    const row = this.chain(id);
    if (partial.type === 'delete') {
      row.pending = row.pending.filter((job) => job.type === 'delete');
    }
    if (partial.type === 'update') {
      const last = row.pending[row.pending.length - 1];
      if (last?.type === 'update') {
        row.generation += 1;
        const job: PersistJob = {
          ...partial,
          drawingId: id,
          generation: row.generation,
          scopeGeneration: this.scopeGeneration,
          scope: this.currentScope,
        };
        row.pending[row.pending.length - 1] = job;
        return job;
      }
    }
    row.generation += 1;
    const job: PersistJob = {
      ...partial,
      drawingId: id,
      generation: row.generation,
      scopeGeneration: this.scopeGeneration,
      scope: this.currentScope,
    };
    row.pending.push(job);
    return job;
  }

  takeNext(drawingId: string): PersistJob | null {
    const row = this.chains.get(drawingId);
    if (!row || row.inflight || !row.pending.length) return null;
    const next = row.pending.shift();
    if (!next) return null;
    row.inflight = next;
    return row.inflight;
  }

  readyIds(): string[] {
    const ids: string[] = [];
    for (const [id, chain] of this.chains) {
      if (!chain.inflight && chain.pending.length) ids.push(id);
    }
    return ids;
  }

  complete(drawingId: string, generation: number): void {
    const row = this.chains.get(drawingId);
    if (row?.inflight?.generation === generation) {
      row.inflight = null;
    }
  }

  /**
   * Keep a failed inflight job so retry can replay it.
   * A later different-type op stays behind the failed one (create then update).
   * A later same-type update may replace a stale failed update.
   */
  failKeep(drawingId: string): void {
    const row = this.chains.get(drawingId);
    if (!row?.inflight) return;
    const failed = row.inflight;
    row.inflight = null;
    const newerSameUpdate = failed.type === 'update'
      && row.pending.some((job) => job.type === 'update' && job.generation > failed.generation);
    if (newerSameUpdate) return;
    row.pending.unshift(failed);
  }

  snapshot(): PersistJob[] {
    const jobs: PersistJob[] = [];
    for (const chain of this.chains.values()) {
      if (chain.inflight) jobs.push(chain.inflight);
      jobs.push(...chain.pending);
    }
    return jobs;
  }

  /** Re-queue snapshot jobs that are not currently in flight (retry). */
  restoreForRetry(jobs: PersistJob[]): void {
    const seen = new Map<string, Set<number>>();
    const mark = (id: string, generation: number) => {
      let set = seen.get(id);
      if (!set) {
        set = new Set();
        seen.set(id, set);
      }
      set.add(generation);
    };
    for (const [id, chain] of this.chains) {
      if (chain.inflight) mark(id, chain.inflight.generation);
      for (const job of chain.pending) mark(id, job.generation);
    }
    for (const job of jobs) {
      if (seen.get(job.drawingId)?.has(job.generation)) continue;
      const row = this.chain(job.drawingId);
      row.pending.push(job);
      row.generation = Math.max(row.generation, job.generation);
      mark(job.drawingId, job.generation);
    }
    for (const chain of this.chains.values()) {
      chain.pending.sort((a, b) => a.generation - b.generation);
    }
  }

  stampRevisions(server: ChartDrawing[]): void {
    const remote = new Map(server.map((item) => [item.id, item.revision]));
    const patch = (job: PersistJob): PersistJob => {
      if (!job.drawing) return job;
      const revision = remote.get(job.drawing.id);
      if (revision == null) return job;
      return { ...job, drawing: { ...job.drawing, revision } };
    };
    for (const chain of this.chains.values()) {
      if (chain.inflight) chain.inflight = patch(chain.inflight);
      chain.pending = chain.pending.map(patch);
    }
  }

  cancelAll(): void {
    this.chains.clear();
  }

  cancelId(drawingId: string): void {
    const row = this.chains.get(drawingId);
    if (!row) return;
    row.pending = [];
  }
}

export function patchRevision(drawings: ChartDrawing[], id: string, revision: number): ChartDrawing[] {
  return drawings.map((item) => (item.id === id ? { ...item, revision } : item));
}

export function replaceDrawing(drawings: ChartDrawing[], drawing: ChartDrawing): ChartDrawing[] {
  return drawings.map((item) => (item.id === drawing.id ? drawing : item));
}
