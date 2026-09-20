import type { SectorIvFlowSnapshot } from './ivRefreshFlow.ts';

export interface SectorIvSchedulerClock {
  now: () => number;
  setTimeout: (callback: () => void, delayMs: number) => number;
  clearTimeout: (handle: number) => void;
  setInterval: (callback: () => void, intervalMs: number) => number;
  clearInterval: (handle: number) => void;
}

interface RetrySource {
  identity: object;
  delayMs: number;
}

const NORMAL_READ_INTERVAL_MS = 600_000;
const ACTIVE_REFRESH_INTERVAL_MS = 3_000;

function retrySource(snapshot: SectorIvFlowSnapshot): RetrySource | null {
  const readRetry = snapshot.readError?.retryAfter ?? 0;
  if (snapshot.readError && readRetry > 0) {
    return {
      identity: snapshot.readError,
      delayMs: Math.max(1, Math.ceil(readRetry)) * 1_000,
    };
  }
  if (
    (snapshot.refresh.status === 'failed' || snapshot.refresh.status === 'cooldown')
    && snapshot.refresh.retryAfterSeconds > 0
  ) {
    return {
      identity: snapshot.refresh,
      delayMs: Math.max(1, Math.ceil(snapshot.refresh.retryAfterSeconds)) * 1_000,
    };
  }
  return null;
}

/**
 * 只调度 GET：任务排队/运行时三秒确认一次；失败或冷却按服务端倒计时到期
 * 只补读一次。退避期间切回标签不会提前请求，截止时间已过且标签曾隐藏时，
 * 恢复可见再补读。
 */
export class SectorIvReadScheduler {
  private readonly read: () => void | Promise<void>;
  private readonly visible: () => boolean;
  private readonly clock: SectorIvSchedulerClock;
  private timeoutHandle: number | null = null;
  private intervalHandle: number | null = null;
  private retryDeadline: number | null = null;
  private retryDue = false;
  private retryIdentity: object | null = null;
  private consumedRetryIdentity: object | null = null;
  private sectorId: string | null = null;

  constructor(
    read: () => void | Promise<void>,
    visible: () => boolean,
    clock: SectorIvSchedulerClock,
  ) {
    this.read = read;
    this.visible = visible;
    this.clock = clock;
  }

  private clearTimers(): void {
    if (this.timeoutHandle !== null) this.clock.clearTimeout(this.timeoutHandle);
    if (this.intervalHandle !== null) this.clock.clearInterval(this.intervalHandle);
    this.timeoutHandle = null;
    this.intervalHandle = null;
  }

  update(snapshot: SectorIvFlowSnapshot, sectorId: string | null): void {
    this.clearTimers();
    this.sectorId = sectorId;
    this.retryDeadline = null;
    this.retryDue = false;
    this.retryIdentity = null;
    if (!sectorId) return;

    const retry = retrySource(snapshot);
    if (retry && retry.identity !== this.consumedRetryIdentity) {
      this.retryIdentity = retry.identity;
      this.retryDeadline = this.clock.now() + retry.delayMs;
      this.timeoutHandle = this.clock.setTimeout(() => {
        this.timeoutHandle = null;
        this.retryDeadline = null;
        this.consumedRetryIdentity = this.retryIdentity;
        this.retryIdentity = null;
        if (this.visible()) {
          void this.read();
        } else {
          this.retryDue = true;
        }
      }, retry.delayMs);
      return;
    }

    // The worker checks its durable retry schedule separately. A GET can land
    // just after cooling ends but before the worker changes failed to queued.
    const active = snapshot.refresh.status === 'queued'
      || snapshot.refresh.status === 'running'
      || (snapshot.refresh.status === 'failed' && snapshot.refresh.retryAfterSeconds <= 0);
    const intervalMs = active ? ACTIVE_REFRESH_INTERVAL_MS : NORMAL_READ_INTERVAL_MS;
    this.intervalHandle = this.clock.setInterval(() => {
      if (this.visible()) void this.read();
    }, intervalMs);
  }

  onVisible(): void {
    if (!this.sectorId || !this.visible()) return;
    if (this.retryDeadline !== null && this.clock.now() < this.retryDeadline) return;
    if (this.retryDue) this.retryDue = false;
    void this.read();
  }

  dispose(): void {
    this.clearTimers();
    this.sectorId = null;
    this.retryDeadline = null;
    this.retryDue = false;
    this.retryIdentity = null;
  }
}
