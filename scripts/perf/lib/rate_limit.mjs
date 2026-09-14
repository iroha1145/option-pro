/**
 * Shared laboratory pacing. Heavy APIs are 30 / 60s; back-to-back Playwright
 * pairs can 429 and then spend 30s+ on client retries. Scripts must wait,
 * not ask the product to raise the limit.
 */
export const REPEAT_GAP_MS = Number(
  process.env.OPTIX_PERF_PAIR_GAP_MS || process.env.OPTIX_PERF_REPEAT_GAP_MS || 8000,
);
export const RATE_LIMIT_COOLDOWN_MS = Number(process.env.OPTIX_PERF_429_COOLDOWN_MS || 60_000);

export function attach429Counter(page) {
  const state = { count: 0, urls: [] };
  page.on('response', (response) => {
    if (response.status() === 429) {
      state.count += 1;
      state.urls.push(String(response.url()).replace(/^https?:\/\/[^/]+/, ''));
    }
  });
  return state;
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function afterSampleGap({ rateLimitedCount = 0, last = false } = {}) {
  if (last) return;
  if (rateLimitedCount > 0 && RATE_LIMIT_COOLDOWN_MS > 0) {
    console.log(`rate-limited x${rateLimitedCount}; cooldown ${RATE_LIMIT_COOLDOWN_MS}ms`);
    await sleep(RATE_LIMIT_COOLDOWN_MS);
    return;
  }
  if (REPEAT_GAP_MS > 0) await sleep(REPEAT_GAP_MS);
}
