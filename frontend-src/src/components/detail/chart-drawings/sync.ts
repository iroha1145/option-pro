/** Per-id serial outbox, scope tokens, and full-field undo diffs. */
import type { ChartAdjustment, ChartDrawing, ChartRange } from './types.ts';
import { parseDrawing } from './schema.ts';
import { outboxStorageKey, type StorageLike } from './storage.ts';

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
  /** 发出请求时冻结的范围版本；await 之后只认这一份。 */
  expectedScopeRevision?: number;
  /** 全局单调序号：范围级任务与单条任务靠它保持先后，不然清空会追上后画的线。 */
  seq?: number;
}

export function jobIsCurrent(
  job: PersistJob,
  currentScope: ScopeKey | null,
  currentScopeGeneration: number,
): boolean {
  return scopeEquals(job.scope, currentScope) && job.scopeGeneration === currentScopeGeneration;
}

/** Same ticker/range/adjustment — a later clear/replace may have bumped generation. */
export function jobBelongsToScope(job: PersistJob, currentScope: ScopeKey | null): boolean {
  return scopeEquals(job.scope, currentScope);
}

export function conflictSnapshotUsable(
  snapshot: { scope: ScopeKey; scopeGeneration: number } | null,
  currentScope: ScopeKey | null,
  currentGeneration: number,
): boolean {
  if (!snapshot) return true;
  return scopeEquals(snapshot.scope, currentScope) && snapshot.scopeGeneration === currentGeneration;
}

export type SettleKind = 'success' | 'drop' | 'quota' | 'conflict' | 'superseded' | 'retry';

export function releaseInflight(
  outbox: DrawingOutbox,
  job: PersistJob,
  mode: 'complete' | 'drop' | 'failKeep',
): boolean {
  if (!jobBelongsToScope(job, outbox.getScope())) return false;
  if (mode === 'complete') outbox.complete(job.drawingId, job.generation);
  else if (mode === 'drop') outbox.dropInflight(job.drawingId);
  else outbox.failKeep(job.drawingId);
  outbox.persist();
  return true;
}

export function settleJob(args: {
  outbox: DrawingOutbox;
  job: PersistJob;
  kind: SettleKind;
}): {
  status: 'idle' | 'saving' | 'unsynced' | 'conflict' | 'write_failed' | null;
  hint: string | null;
  readyIds: string[];
} {
  if (!jobBelongsToScope(args.job, args.outbox.getScope())) {
    return { status: null, hint: null, readyIds: [] };
  }
  args.outbox.persist();
  if (args.kind === 'quota') {
    return { status: 'unsynced', hint: 'quota', readyIds: args.outbox.readyIds() };
  }
  if (args.kind === 'conflict') {
    const readyIds = args.outbox.readyIds().filter((id) => id === SCOPE_JOB_ID);
    return { status: 'conflict', hint: 'conflict', readyIds };
  }
  if (args.kind === 'retry') {
    return { status: 'write_failed', hint: 'unsynced', readyIds: [] };
  }
  if (args.outbox.isEmpty()) {
    return { status: 'idle', hint: null, readyIds: [] };
  }
  return { status: 'saving', hint: null, readyIds: args.outbox.readyIds() };
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

export type SyncFailure = 'load_failed' | 'write_failed' | 'conflict' | null;

export function resolveRetryAction(
  failure: SyncFailure,
  outboxEmpty: boolean,
): 'reload' | 'replay' | 'idle' | 'conflict' {
  if (failure === 'conflict') return 'conflict';
  if (failure === 'load_failed') return 'reload';
  if (failure === 'write_failed') return outboxEmpty ? 'idle' : 'replay';
  return outboxEmpty ? 'idle' : 'replay';
}

export type RegeneratedOp =
  | { type: 'create'; drawingId: string; drawing: ChartDrawing }
  | { type: 'update'; drawingId: string; drawing: ChartDrawing }
  | { type: 'delete'; drawingId: string };

export function regeneratePersistOps(
  local: ChartDrawing[],
  server: ChartDrawing[],
): RegeneratedOp[] {
  const localMap = new Map(local.map((item) => [item.id, item]));
  const serverMap = new Map(server.map((item) => [item.id, item]));
  const ops: RegeneratedOp[] = [];
  for (const [id, drawing] of localMap) {
    const remote = serverMap.get(id);
    if (!remote) {
      ops.push({ type: 'create', drawingId: id, drawing: { ...drawing, revision: 1 } });
    } else {
      ops.push({ type: 'update', drawingId: id, drawing: { ...drawing, revision: remote.revision } });
    }
  }
  for (const [id] of serverMap) {
    if (!localMap.has(id)) ops.push({ type: 'delete', drawingId: id });
  }
  return ops;
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
  /** 本地还有没入队的编辑（防抖窗口里）：回声只能吃 revision，不能整条盖回。 */
  localDirty?: boolean;
}): ApplyAction {
  const { job } = args;
  if (!scopeEquals(job.scope, args.currentScope)) return { action: 'ignore' };
  if (job.scopeGeneration !== args.currentScopeGeneration) return { action: 'ignore' };
  if (!args.responseDrawing) return { action: 'ignore' };
  if (job.generation === args.latestGenerationForId && !args.localDirty) {
    return { action: 'replace', drawing: args.responseDrawing };
  }
  return { action: 'revision', id: args.responseDrawing.id, revision: args.responseDrawing.revision };
}

/** revision 是服务器记账，不是用户可撤销的状态：发出前一律换成最新已知值。 */
export function applyKnownRevisions(
  drawings: ChartDrawing[],
  revisions: Map<string, number>,
): ChartDrawing[] {
  return drawings.map((item) => {
    const known = revisions.get(item.id);
    return known == null || known === item.revision ? item : { ...item, revision: known };
  });
}

export function latestKnownRevision(
  revisions: Map<string, number>,
  id: string,
  fallback: number,
): number {
  return revisions.get(id) ?? fallback;
}

export type SyncFailureAction = 'conflict' | 'quota' | 'drop' | 'retry';

/**
 * 失败分诊按业务码走，不按 HTTP 状态：409 里只有 revision_conflict 是真冲突，
 * 配额满弹冲突框会让「保留本地」永远重放必败的创建；后端已幂等，重放同一条
 * 创建会成功，删除已经不存在的行也算成功，所以这两类直接丢任务。
 */
export function resolveSyncFailure(
  jobType: PersistOpType,
  code: string | null,
  status: number | null,
): SyncFailureAction {
  if (code === 'revision_conflict' || code === 'scope_revision_conflict' || code === 'drawing_id_conflict') {
    return 'conflict';
  }
  if (code === 'drawings_range_full' || code === 'drawings_full') return 'quota';
  if (code === 'drawing_exists') return jobType === 'create' ? 'drop' : 'retry';
  if (code === 'drawing_not_found' || (status === 404 && code === null)) {
    if (jobType === 'update') return 'conflict';
    return jobType === 'delete' ? 'drop' : 'retry';
  }
  // 400 是请求本身不合法（invalid_price / invalid_payload / scope_mismatch），
  // 重放多少次都是同一个 400，留在队列里只会把出口堵死。
  if (status === 400) return 'drop';
  return 'retry';
}

type Chain = {
  generation: number;
  pending: PersistJob[];
  inflight: PersistJob | null;
};

const JOB_TYPES = new Set<PersistOpType>(['create', 'update', 'delete', 'clear', 'replace']);

/**
 * localStorage 里的 outbox 同样是不可信输入：逐条按 schema 校验，坏行丢掉，
 * 免得把一条读不出来的负载重新 PUT 回服务器或写进本地状态。
 */
function parseStoredScope(raw: unknown): ScopeKey | null {
  if (!raw || typeof raw !== 'object') return null;
  const row = raw as Record<string, unknown>;
  if (typeof row.identity !== 'string' || typeof row.ticker !== 'string') return null;
  if (typeof row.range !== 'string' || typeof row.adjustment !== 'string') return null;
  return {
    identity: row.identity,
    ticker: row.ticker,
    range: row.range as ChartRange,
    adjustment: row.adjustment as ChartAdjustment,
  };
}

export function parsePersistJobs(
  raw: unknown,
  scope: ScopeKey,
  scopeGeneration: number,
): PersistJob[] {
  const row = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const list = Array.isArray(row.jobs) ? row.jobs : [];
  const jobs: PersistJob[] = [];
  for (const item of list) {
    if (!item || typeof item !== 'object') continue;
    const job = item as Record<string, unknown>;
    const type = job.type as PersistOpType;
    if (!JOB_TYPES.has(type)) continue;
    const drawingId = typeof job.drawingId === 'string' ? job.drawingId : '';
    if (!drawingId) continue;
    const generation = Number(job.generation);
    if (!Number.isInteger(generation) || generation < 1) continue;
    const storedScope = job.scope === undefined ? scope : parseStoredScope(job.scope);
    if (!storedScope || !scopeEquals(storedScope, scope)) continue;
    const drawing = job.drawing === undefined ? undefined : parseDrawing(job.drawing);
    if ((type === 'create' || type === 'update') && !drawing) continue;
    if (drawing && (drawing.ticker !== scope.ticker || drawing.range !== scope.range || drawing.adjustment !== scope.adjustment)) {
      continue;
    }
    if (drawing && drawing.id !== drawingId) continue;
    let drawings: ChartDrawing[] | undefined;
    if (type === 'replace') {
      if (!Array.isArray(job.drawings)) continue;
      const parsed = job.drawings.map(parseDrawing);
      if (parsed.some((entry) => entry === null)) continue;
      drawings = parsed as ChartDrawing[];
      if (drawings.some((entry) => entry.ticker !== scope.ticker || entry.range !== scope.range)) continue;
    }
    const seq = Number(job.seq);
    const expected = Number(job.expectedScopeRevision);
    jobs.push({
      drawingId,
      generation,
      scopeGeneration,
      scope,
      type,
      ...(drawing ? { drawing } : {}),
      ...(drawings ? { drawings } : {}),
      ...(Number.isInteger(expected) && expected >= 0 ? { expectedScopeRevision: expected } : {}),
      seq: Number.isFinite(seq) ? seq : generation,
    });
  }
  return jobs.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
}

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
  private knownScopeRevision = 0;
  /** Frozen when the queue first becomes non-empty. Retry must not rewrite it. */
  private baseScopeRevision: number | null = null;
  /** After a safe GET rebase, takeNext may stamp unset expected from this once. */
  private sendScopeRevision: number | null = null;
  private baselineReady = false;
  private chains = new Map<string, Chain>();
  private seqCounter = 0;
  private readonly store: StorageLike | null;

  // 参数属性在 erasableSyntaxOnly 下不允许，字段要显式赋值。
  constructor(store?: StorageLike | null) {
    this.store = store === undefined
      ? (typeof localStorage === 'undefined' ? null : localStorage)
      : store;
  }

  getScope(): ScopeKey | null {
    return this.currentScope;
  }

  getScopeGeneration(): number {
    return this.scopeGeneration;
  }

  getScopeRevision(): number {
    return this.knownScopeRevision;
  }

  getBaseScopeRevision(): number | null {
    return this.baseScopeRevision;
  }

  isBaselineReady(): boolean {
    return this.baselineReady;
  }

  markBaselineReady(): void {
    this.baselineReady = true;
  }

  setSendScopeRevision(revision: number): void {
    if (!Number.isInteger(revision) || revision < 0) return;
    this.sendScopeRevision = revision;
  }

  rebaseBase(revision: number): void {
    if (!Number.isInteger(revision) || revision < 0) return;
    this.knownScopeRevision = revision;
    this.baseScopeRevision = revision;
    this.sendScopeRevision = revision;
    this.baselineReady = true;
    this.persistCurrent();
  }

  clearBase(): void {
    this.baseScopeRevision = null;
    this.sendScopeRevision = null;
    this.persistCurrent();
  }

  setScopeRevision(revision: number): void {
    if (!Number.isInteger(revision) || revision < 0) return;
    this.knownScopeRevision = revision;
  }

  setScope(scope: ScopeKey): number {
    if (this.currentScope) this.persistCurrent();
    this.currentScope = scope;
    this.scopeGeneration += 1;
    this.knownScopeRevision = 0;
    this.sendScopeRevision = null;
    this.baselineReady = false;
    this.baseScopeRevision = null;
    this.chains.clear();
    this.hydrateCurrent();
    return this.scopeGeneration;
  }

  isEmpty(): boolean {
    for (const chain of this.chains.values()) {
      if (chain.inflight || chain.pending.length) return false;
    }
    return true;
  }

  /** 除了这一条（通常是正在飞的自己）以外还有没有别的活；replace 回包要按它决定能否覆盖本地。 */
  isEmptyExcept(drawingId: string, generation: number): boolean {
    for (const [id, chain] of this.chains) {
      const skipInflight = id === drawingId && chain.inflight?.generation === generation;
      if (chain.inflight && !skipInflight) return false;
      if (chain.pending.length) return false;
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
    const wasEmpty = this.isEmpty();
    if (partial.type === 'replace' || partial.type === 'clear') {
      this.scopeGeneration += 1;
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
        this.seqCounter += 1;
        const job: PersistJob = {
          ...partial,
          drawingId: id,
          generation: row.generation,
          scopeGeneration: this.scopeGeneration,
          scope: this.currentScope,
          seq: this.seqCounter,
        };
        row.pending[row.pending.length - 1] = job;
        this.captureBaseIfNeeded(wasEmpty);
        this.persistCurrent();
        return job;
      }
    }
    row.generation += 1;
    this.seqCounter += 1;
    const job: PersistJob = {
      ...partial,
      drawingId: id,
      generation: row.generation,
      scopeGeneration: this.scopeGeneration,
      scope: this.currentScope,
      seq: this.seqCounter,
    };
    row.pending.push(job);
    this.captureBaseIfNeeded(wasEmpty);
    this.persistCurrent();
    return job;
  }

  private captureBaseIfNeeded(wasEmpty: boolean): void {
    if (!wasEmpty) return;
    if (this.baseScopeRevision != null) return;
    this.baseScopeRevision = this.knownScopeRevision;
  }

  private clearBaseIfEmpty(): void {
    if (!this.isEmpty()) return;
    this.baseScopeRevision = null;
    this.sendScopeRevision = null;
  }

  hasInflight(): boolean {
    for (const chain of this.chains.values()) {
      if (chain.inflight) return true;
    }
    return false;
  }

  /**
   * 范围级任务（clear / replace）与单条任务分属两侧，跨侧必须按入队顺序串行：
   * 对侧有在飞的就等，对侧排在更前面的也要等。同侧互不阻塞，所以不会死锁。
   */
  private blockedHead(drawingId: string, head: PersistJob): boolean {
    const scopeSide = drawingId === SCOPE_JOB_ID;
    for (const [id, chain] of this.chains) {
      if (chain.inflight) return true;
      if ((id === SCOPE_JOB_ID) === scopeSide) continue;
      const first = chain.pending[0];
      if (first && (first.seq ?? 0) < (head.seq ?? 0)) return true;
    }
    return false;
  }

  takeNext(drawingId: string): PersistJob | null {
    const row = this.chains.get(drawingId);
    if (!row || row.inflight || !row.pending.length) return null;
    if (this.blockedHead(drawingId, row.pending[0])) return null;
    const next = row.pending.shift();
    if (!next) return null;
    if (next.expectedScopeRevision == null) {
      next.expectedScopeRevision = this.sendScopeRevision
        ?? this.baseScopeRevision
        ?? this.knownScopeRevision;
    }
    row.inflight = next;
    this.persistCurrent();
    return row.inflight;
  }

  readyIds(): string[] {
    const ids: string[] = [];
    for (const [id, chain] of this.chains) {
      if (chain.inflight || !chain.pending.length) continue;
      if (this.blockedHead(id, chain.pending[0])) continue;
      ids.push(id);
    }
    return ids;
  }

  /** 丢掉在飞任务而不重排（配额满 / 400 这类重放必败的错）。 */
  dropInflight(drawingId: string): void {
    const row = this.chains.get(drawingId);
    if (!row?.inflight) return;
    row.inflight = null;
    this.clearBaseIfEmpty();
    this.persistCurrent();
  }

  complete(drawingId: string, generation: number): void {
    const row = this.chains.get(drawingId);
    if (row?.inflight?.generation === generation) {
      row.inflight = null;
    }
    this.clearBaseIfEmpty();
    this.persistCurrent();
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
    if (!newerSameUpdate) row.pending.unshift(failed);
    this.persistCurrent();
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
      // 恢复回来的 seq 要顶住全局计数器，之后新入队的任务才排在它们后面。
      this.seqCounter = Math.max(this.seqCounter, job.seq ?? 0);
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
    this.persistCurrent();
  }

  replacePending(ops: RegeneratedOp[]): void {
    for (const [id, chain] of this.chains) {
      if (id === SCOPE_JOB_ID) continue;
      chain.pending = [];
    }
    for (const op of ops) {
      if (op.type === 'delete') this.enqueue({ drawingId: op.drawingId, type: 'delete' });
      else this.enqueue({ drawingId: op.drawingId, type: op.type, drawing: op.drawing });
    }
  }

  persist(): void {
    this.persistCurrent();
  }

  private persistKey(): string | null {
    if (!this.currentScope) return null;
    return outboxStorageKey(
      this.currentScope.identity,
      this.currentScope.ticker,
      this.currentScope.range,
      this.currentScope.adjustment,
    );
  }

  private persistCurrent(): void {
    const key = this.persistKey();
    if (!this.store || !key) return;
    try {
      this.store.setItem(key, JSON.stringify({
        jobs: this.snapshot(),
        baseScopeRevision: this.baseScopeRevision,
      }));
    } catch {
      /* private mode / quota */
    }
  }

  private hydrateCurrent(): void {
    const key = this.persistKey();
    if (!this.store || !key || !this.currentScope) return;
    let raw: string | null;
    try {
      raw = this.store.getItem(key);
    } catch {
      return;
    }
    if (!raw) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return; /* corrupt outbox payload */
    }
    const restored = parsePersistJobs(parsed, this.currentScope, this.scopeGeneration);
    if (restored.length) this.restoreForRetry(restored);
    const row = parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : {};
    const base = Number(row.baseScopeRevision);
    if (Number.isInteger(base) && base >= 0) this.baseScopeRevision = base;
    else if (restored.length) {
      const expected = restored
        .map((job) => job.expectedScopeRevision)
        .filter((value): value is number => value != null);
      this.baseScopeRevision = expected.length ? Math.min(...expected) : 0;
    }
  }

  cancelAll(): void {
    this.chains.clear();
    this.baseScopeRevision = null;
    this.sendScopeRevision = null;
    this.persistCurrent();
  }

  cancelId(drawingId: string): void {
    const row = this.chains.get(drawingId);
    if (!row) return;
    row.pending = [];
    this.persistCurrent();
  }
}

export function patchRevision(drawings: ChartDrawing[], id: string, revision: number): ChartDrawing[] {
  return drawings.map((item) => (item.id === id ? { ...item, revision } : item));
}

export function replaceDrawing(drawings: ChartDrawing[], drawing: ChartDrawing): ChartDrawing[] {
  return drawings.map((item) => (item.id === drawing.id ? drawing : item));
}
