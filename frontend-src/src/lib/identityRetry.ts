/**
 * 身份探测失败后的重试节奏。
 *
 * /access/status 是全站身份的地基：它失败时旧实现停在初始 visitor 上，而 60 秒
 * 定时核验又只对「已有主体」的会话开（hasPrincipal 门），于是**首个探测撞上
 * 429/断网的会话永远是访客**——期间画的图形被静默降级进访客本地桶、绝不上传
 * （CI 取证抓到的「零 POST、服务端恒空」死局即此）。探测必须自己会好。
 *
 * 服务端 Retry-After 是诚实值（滚动窗口真实等待，通常 1–3s），优先按它排；
 * 没有就走短梯子。上限 60s，与既有的定时核验节奏对齐。
 */
export function identityRetryDelayMs(attempt: number, retryAfterSeconds?: number | null): number {
  if (typeof retryAfterSeconds === 'number' && Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
    return Math.min(Math.max(retryAfterSeconds, 1), 60) * 1000;
  }
  const ladder = [2_000, 5_000, 15_000];
  const index = Math.max(0, Math.floor(attempt) - 1);
  return index < ladder.length ? ladder[index] : 30_000;
}
