/**
 * 重试等待时长（毫秒）——全站唯一的一把尺。
 *
 * 服务器给了 Retry-After 就听它的（诚实值：滚动限流桶的真实等待），钳在
 * [1, capSeconds] 秒；没给（断网、5xx）按 ladderMs 逐级退避，走完封顶 tailMs。
 * 纯函数，不读时钟——排定时机由调用方的 setTimeout 承担。
 *
 * 各调用方只差常数：身份探测短梯（2/5/15→30s，钳 60s）、绘图 outbox 长梯
 * （5/15/45→60s，钳 120s）。此前两份同构实现各写一遍钳制与索引逻辑，
 * 「≤0 的 Retry-After 算不算」这种口径差异就悄悄漂了——收口成参数。
 */
export interface RetryLadder {
  ladderMs: readonly number[];
  tailMs: number;
  capSeconds: number;
  /** Retry-After ≤ 0 时：'clamp' 视为 1s（防空转），'ladder' 视为没给。 */
  nonPositive: 'clamp' | 'ladder';
}

export function retryDelayMs(
  attempt: number,
  retryAfterSeconds: number | null | undefined,
  ladder: RetryLadder,
): number {
  if (typeof retryAfterSeconds === 'number' && Number.isFinite(retryAfterSeconds)) {
    if (retryAfterSeconds > 0 || ladder.nonPositive === 'clamp') {
      return Math.min(Math.max(retryAfterSeconds, 1), ladder.capSeconds) * 1000;
    }
  }
  const index = Math.max(0, Math.floor(attempt) - 1);
  return index < ladder.ladderMs.length ? ladder.ladderMs[index] : ladder.tailMs;
}
