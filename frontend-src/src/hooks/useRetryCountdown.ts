import { useMemo, useSyncExternalStore } from 'react';

interface RetryCountdownStore {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => number;
}

function createRetryCountdownStore(
  initialSeconds: number,
  source: unknown,
): RetryCountdownStore {
  void source;
  let seconds = initialSeconds;
  let timer: ReturnType<typeof window.setInterval> | null = null;
  const listeners = new Set<() => void>();

  const stop = () => {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  };

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (seconds > 0 && timer === null) {
        timer = window.setInterval(() => {
          seconds = Math.max(0, seconds - 1);
          listeners.forEach((notify) => notify());
          if (seconds === 0) stop();
        }, 1_000);
      }
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) stop();
      };
    },
    getSnapshot: () => seconds,
  };
}

/**
 * Retry-After 倒计时。source 身份变化时创建新时钟，因此相同秒数的后续错误
 * 也会重新开始；时钟通过外部订阅更新，不在副作用中同步写 React 状态。
 */
export function useRetryCountdown(
  source: unknown,
  retryAfter: number | undefined,
): number {
  const initialSeconds = Math.max(0, Math.ceil(retryAfter ?? 0));
  const store = useMemo(
    () => createRetryCountdownStore(initialSeconds, source),
    [initialSeconds, source],
  );
  return useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
}
