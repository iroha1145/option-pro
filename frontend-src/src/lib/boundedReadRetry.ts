/**
 * 新闻详情 / 任务恢复的有界重试。
 *
 * 429/503 等可重试错误听 max(本地退避, Retry-After)；
 * 401/403/404 与 retryable=false 立刻停。纯函数，计时器由调用方取消。
 */

export const NEWS_DETAIL_RETRY_WAITS_MS = [1_500, 3_000] as const;

export class ReadAttemptAborted extends Error {
  constructor() {
    super('read-attempt-aborted');
    this.name = 'ReadAttemptAborted';
  }
}

function errorCode(error: unknown): number | null {
  if (!error || typeof error !== 'object') return null;
  const code = (error as { code?: unknown }).code;
  return typeof code === 'number' && Number.isFinite(code) ? code : null;
}

export function isAutoRetryableReadError(error: unknown): boolean {
  if (error instanceof ReadAttemptAborted) return false;
  if (error instanceof TypeError) return true;
  if (!error || typeof error !== 'object') return false;
  const row = error as { name?: string; retryable?: unknown };
  if (row.retryable === false) return false;
  const code = errorCode(error);
  if (code === 401 || code === 403 || code === 404) return false;
  if (code === 0 || code === 408 || code === 429 || (code !== null && code >= 500)) return true;
  if (error instanceof TypeError || row.name === 'TypeError') return true;
  return code === null;
}

export function boundedReadRetryDelayMs(
  attempt: number,
  error: unknown,
  waitsMs: readonly number[] = NEWS_DETAIL_RETRY_WAITS_MS,
): number {
  const last = waitsMs[waitsMs.length - 1] ?? 1_500;
  const local = waitsMs[Math.min(Math.max(0, attempt), waitsMs.length - 1)] ?? last;
  const retryAfter = error && typeof error === 'object'
    ? (error as { retryAfter?: unknown }).retryAfter
    : undefined;
  const serverMs = typeof retryAfter === 'number' && Number.isFinite(retryAfter)
    ? Math.max(0, retryAfter) * 1000
    : 0;
  return Math.max(local, serverMs);
}

/**
 * B 已在跟的时候，迟到的 A 不得覆盖；同一条新闻换成当前任务 B 时可以换掉过期 A。
 */
export function shouldApplyRecoveryJob(
  current: { jobId: string } | null,
  incoming: { jobId: string },
  expectedJobId: string,
  activeJobId: string | null,
): boolean {
  if (incoming.jobId !== expectedJobId) return false;
  if (activeJobId && incoming.jobId !== activeJobId) return false;
  if (current && current.jobId !== incoming.jobId && current.jobId === activeJobId) return false;
  return true;
}

export function createCancellableSleep(options: {
  isAlive: () => boolean;
  setTimeoutFn?: (fn: () => void, ms: number) => number;
  clearTimeoutFn?: (id: number) => void;
}): { sleep: (ms: number) => Promise<void>; cancel: () => void } {
  const setTimeoutFn = options.setTimeoutFn
    ?? ((fn, ms) => (typeof window !== 'undefined' ? window.setTimeout(fn, ms) : setTimeout(fn, ms)) as number);
  const clearTimeoutFn = options.clearTimeoutFn
    ?? ((id) => {
      if (typeof window !== 'undefined') window.clearTimeout(id);
      else clearTimeout(id);
    });
  const timers = new Set<number>();
  const pending = new Set<(error: Error) => void>();
  return {
    async sleep(ms: number): Promise<void> {
      if (!options.isAlive()) throw new ReadAttemptAborted();
      await new Promise<void>((resolve, reject) => {
        const id = setTimeoutFn(() => {
          timers.delete(id);
          pending.delete(reject);
          resolve();
        }, ms);
        timers.add(id);
        pending.add(reject);
      });
      if (!options.isAlive()) throw new ReadAttemptAborted();
    },
    cancel() {
      for (const id of timers) clearTimeoutFn(id);
      timers.clear();
      const error = new ReadAttemptAborted();
      for (const reject of pending) reject(error);
      pending.clear();
    },
  };
}

export async function runBoundedRead<T>(options: {
  read: () => Promise<T>;
  isAlive: () => boolean;
  sleep: (ms: number) => Promise<void>;
  waitsMs?: readonly number[];
}): Promise<T> {
  const waits = options.waitsMs ?? NEWS_DETAIL_RETRY_WAITS_MS;
  for (let attempt = 0; ; attempt += 1) {
    if (!options.isAlive()) throw new ReadAttemptAborted();
    try {
      const value = await options.read();
      if (!options.isAlive()) throw new ReadAttemptAborted();
      return value;
    } catch (error) {
      if (error instanceof ReadAttemptAborted || !options.isAlive()) {
        throw new ReadAttemptAborted();
      }
      if (!isAutoRetryableReadError(error) || attempt >= waits.length) {
        throw error;
      }
      await options.sleep(boundedReadRetryDelayMs(attempt, error, waits));
    }
  }
}
