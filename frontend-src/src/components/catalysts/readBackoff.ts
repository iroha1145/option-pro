import { boundedReadRetryDelayMs } from '../../lib/boundedReadRetry.ts';

/**
 * 持续失败时的读取退避：退避期内直接复用上一次的错误，不再发请求；
 * 等待取本地退避与服务端 Retry-After 的较大值。成功一次即复位。
 * 固定间隔的轮询套上它，就不会在接口持续出错时仍按原频率打后端。
 */
export function createReadBackoff<T>(
  read: () => Promise<T>,
  waitsMs: readonly number[],
  now: () => number = Date.now,
): () => Promise<T> {
  let failures = 0;
  let retryAt = 0;
  let lastError: unknown = null;
  return async () => {
    if (failures > 0 && now() < retryAt) throw lastError;
    try {
      const value = await read();
      failures = 0;
      retryAt = 0;
      lastError = null;
      return value;
    } catch (error) {
      failures += 1;
      lastError = error;
      retryAt = now() + boundedReadRetryDelayMs(failures - 1, error, waitsMs);
      throw error;
    }
  };
}
