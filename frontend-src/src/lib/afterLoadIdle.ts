/**
 * 窗口 load 之后再等一段固定时间。
 *
 * 不能用 requestIdleCallback：首屏在等 feed 时主线程正好空闲，
 * idle 会立刻开火，行情探测 / 今日计数 / 其它页预取会和关键 JSON 抢
 * 同一条 HTTP/1.1 连接与 10Mbps 带宽。timeout 只是上限，不是下限。
 *
 * 没有 readyState（测试夹具）时当作已 load。
 */
export function afterLoadIdle(run: () => void, delayMs: number): () => void {
  let timerId: number | null = null;
  let armed = false;

  const schedule = () => {
    if (armed) return;
    armed = true;
    timerId = window.setTimeout(run, delayMs);
  };

  const ready = document.readyState;
  if (ready && ready !== 'complete') {
    window.addEventListener('load', schedule, { once: true });
  } else {
    schedule();
  }

  return () => {
    window.removeEventListener('load', schedule);
    if (timerId !== null) window.clearTimeout(timerId);
  };
}
