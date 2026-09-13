/**
 * 等窗口 load 后再进入 idle。首屏新闻/图表仍在拉关键 JSON 时，
 * 不要用短 timeout 的 requestIdleCallback 去抢带宽。
 * 没有 readyState（测试夹具）时当作已 load，避免永远等不到。
 */
export function afterLoadIdle(run: () => void, timeoutMs: number): () => void {
  const win = window as Window & {
    requestIdleCallback?: (cb: IdleRequestCallback, opts?: IdleRequestOptions) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  let idleId: number | null = null;
  let timerId: number | null = null;
  let armed = false;

  const schedule = () => {
    if (armed) return;
    armed = true;
    if (typeof win.requestIdleCallback === 'function') {
      idleId = win.requestIdleCallback(() => run(), { timeout: timeoutMs });
      return;
    }
    timerId = window.setTimeout(run, Math.min(200, timeoutMs));
  };

  const ready = document.readyState;
  if (ready && ready !== 'complete') {
    window.addEventListener('load', schedule, { once: true });
  } else {
    schedule();
  }

  return () => {
    window.removeEventListener('load', schedule);
    if (idleId !== null) win.cancelIdleCallback?.(idleId);
    if (timerId !== null) window.clearTimeout(timerId);
  };
}
