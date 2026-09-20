import type {
  SectorIvRankingEnvelope,
  SectorIvRefreshResponse,
  SectorIvRefreshState,
} from '../../api/modules/sectors.ts';

export interface SectorIvFlowError {
  code: number;
  message: string;
  retryAfter?: number;
  bizCode?: string;
}

export interface SectorIvRefreshApi {
  read: (sectorId: string) => Promise<SectorIvRankingEnvelope>;
  refresh: (sectorId: string) => Promise<SectorIvRefreshResponse>;
}

export interface SectorIvFlowSnapshot {
  sectorId: string | null;
  data: SectorIvRankingEnvelope | null;
  refresh: SectorIvRefreshState;
  loading: boolean;
  refreshing: boolean;
  submitting: boolean;
  readError: SectorIvFlowError | null;
  actionError: SectorIvFlowError | null;
}

export const IDLE_IV_REFRESH: SectorIvRefreshState = Object.freeze({
  status: 'idle',
  retryAfterSeconds: 0,
  requestedAt: null,
  startedAt: null,
  completedAt: null,
  nextRefreshAt: null,
  errorCode: null,
});

function flowError(error: unknown): SectorIvFlowError {
  if (error && typeof error === 'object') {
    const value = error as Record<string, unknown>;
    return {
      code: typeof value.code === 'number' ? value.code : 500,
      message: typeof value.message === 'string' && value.message
        ? value.message
        : 'Request failed',
      retryAfter: typeof value.retryAfter === 'number' ? value.retryAfter : undefined,
      bizCode: typeof value.bizCode === 'string' ? value.bizCode : undefined,
    };
  }
  return { code: 500, message: typeof error === 'string' ? error : 'Request failed' };
}

function activeRefresh(refresh: SectorIvRefreshState): boolean {
  return refresh.status === 'queued' || refresh.status === 'running';
}

type ReadSlot = {
  sectorId: string;
  generation: number;
  refreshRevision: number;
  promise: Promise<void>;
};

type SubmitSlot = {
  sectorId: string;
  generation: number;
  promise: Promise<void>;
};

/**
 * 板块 IV 的小型读取控制器。
 *
 * GET 与 POST 各自合流；板块世代负责隔离晚到响应。POST 接受后再补一次 GET，
 * 并用刷新修订号阻止 POST 前后的旧 GET 把 queued/running 状态倒退成 idle。
 */
export class SectorIvRefreshFlow {
  private readonly api: SectorIvRefreshApi;
  private readonly listeners = new Set<(snapshot: SectorIvFlowSnapshot) => void>();
  private snapshot: SectorIvFlowSnapshot = {
    sectorId: null,
    data: null,
    refresh: IDLE_IV_REFRESH,
    loading: false,
    refreshing: false,
    submitting: false,
    readError: null,
    actionError: null,
  };
  private generation = 0;
  private refreshRevision = 0;
  private readSlot: ReadSlot | null = null;
  private submitSlot: SubmitSlot | null = null;

  constructor(api: SectorIvRefreshApi) {
    this.api = api;
  }

  getSnapshot = (): SectorIvFlowSnapshot => this.snapshot;

  subscribe = (listener: (snapshot: SectorIvFlowSnapshot) => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private publish(next: SectorIvFlowSnapshot): void {
    this.snapshot = next;
    for (const listener of this.listeners) listener(next);
  }

  selectSector(sectorId: string | null): Promise<void> {
    if (sectorId === this.snapshot.sectorId) return Promise.resolve();
    this.generation += 1;
    this.refreshRevision += 1;
    this.publish({
      sectorId,
      data: null,
      refresh: IDLE_IV_REFRESH,
      loading: sectorId !== null,
      refreshing: false,
      submitting: false,
      readError: null,
      actionError: null,
    });
    return sectorId ? this.read() : Promise.resolve();
  }

  read(): Promise<void> {
    const sectorId = this.snapshot.sectorId;
    if (!sectorId) return Promise.resolve();
    const generation = this.generation;
    if (
      this.readSlot
      && this.readSlot.sectorId === sectorId
      && this.readSlot.generation === generation
    ) {
      return this.readSlot.promise;
    }

    const refreshRevision = this.refreshRevision;
    this.publish({
      ...this.snapshot,
      loading: this.snapshot.data === null,
      refreshing: this.snapshot.data !== null,
    });
    const promise = this.api.read(sectorId)
      .then((data) => {
        if (generation !== this.generation || sectorId !== this.snapshot.sectorId) return;
        // POST 响应比本次 GET 晚到时，GET 看到的是提交前状态；保留较新的任务状态。
        const refresh = refreshRevision < this.refreshRevision
          ? this.snapshot.refresh
          : data.refresh;
        this.publish({
          ...this.snapshot,
          data: { ...data, refresh },
          refresh,
          loading: false,
          refreshing: false,
          readError: null,
        });
      })
      .catch((error: unknown) => {
        if (generation !== this.generation || sectorId !== this.snapshot.sectorId) return;
        this.publish({
          ...this.snapshot,
          loading: false,
          refreshing: false,
          readError: flowError(error),
        });
      })
      .finally(() => {
        if (this.readSlot?.promise === promise) this.readSlot = null;
      });
    this.readSlot = { sectorId, generation, refreshRevision, promise };
    return promise;
  }

  private async readAfterRefresh(revision: number): Promise<void> {
    const slot = this.readSlot;
    if (slot && slot.generation === this.generation && slot.refreshRevision < revision) {
      await slot.promise;
    }
    if (this.snapshot.sectorId) await this.read();
  }

  submit(): Promise<void> {
    const sectorId = this.snapshot.sectorId;
    if (!sectorId || activeRefresh(this.snapshot.refresh)) return Promise.resolve();
    const generation = this.generation;
    if (
      this.submitSlot
      && this.submitSlot.sectorId === sectorId
      && this.submitSlot.generation === generation
    ) {
      return this.submitSlot.promise;
    }

    this.refreshRevision += 1;
    this.publish({
      ...this.snapshot,
      submitting: true,
      actionError: null,
    });
    const promise = this.api.refresh(sectorId)
      .then(async (response) => {
        if (generation !== this.generation || sectorId !== this.snapshot.sectorId) return;
        this.refreshRevision += 1;
        const revision = this.refreshRevision;
        this.publish({
          ...this.snapshot,
          refresh: response.refresh,
          submitting: false,
          actionError: null,
        });
        // cooldown/failed 是完整的服务端答复；立刻 GET 可能只读到任务表里的
        // idle 行并擦掉倒计时。排队、运行或已完成才需要补读结果。
        if (response.refresh.status !== 'cooldown' && response.refresh.status !== 'failed') {
          await this.readAfterRefresh(revision);
        }
      })
      .catch((error: unknown) => {
        if (generation !== this.generation || sectorId !== this.snapshot.sectorId) return;
        const normalized = flowError(error);
        if ((normalized.retryAfter ?? 0) > 0) {
          this.refreshRevision += 1;
          const refresh: SectorIvRefreshState = {
            ...this.snapshot.refresh,
            status: normalized.code === 429 ? 'cooldown' : 'failed',
            retryAfterSeconds: Math.max(1, Math.ceil(normalized.retryAfter ?? 0)),
            errorCode: normalized.bizCode ?? this.snapshot.refresh.errorCode,
          };
          this.publish({
            ...this.snapshot,
            refresh,
            submitting: false,
            actionError: null,
          });
          return;
        }
        this.publish({
          ...this.snapshot,
          submitting: false,
          actionError: normalized,
        });
      })
      .finally(() => {
        if (this.submitSlot?.promise === promise) this.submitSlot = null;
      });
    this.submitSlot = { sectorId, generation, promise };
    return promise;
  }
}
