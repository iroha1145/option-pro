/**
 * 身份探测失败后的重试节奏。
 *
 * /access/status 是全站身份的地基：它失败时旧实现停在初始 visitor 上，而 60 秒
 * 定时核验又只对「已有主体」的会话开（hasPrincipal 门），于是**首个探测撞上
 * 429/断网的会话永远是访客**——期间画的图形被静默降级进访客本地桶、绝不上传
 * （CI 取证抓到的「零 POST、服务端恒空」死局即此）。探测必须自己会好。
 *
 * 服务端 Retry-After 是诚实值（通常 1–3s），优先按它排；没有就走短梯子。
 * 上限 60s，与既有的定时核验节奏对齐。尺子本体在 lib/retryDelay。
 */
import { retryDelayMs, type RetryLadder } from './retryDelay';

const IDENTITY_LADDER: RetryLadder = {
  ladderMs: [2_000, 5_000, 15_000],
  tailMs: 30_000,
  capSeconds: 60,
  nonPositive: 'ladder',
};

export function identityRetryDelayMs(attempt: number, retryAfterSeconds?: number | null): number {
  return retryDelayMs(attempt, retryAfterSeconds, IDENTITY_LADDER);
}
