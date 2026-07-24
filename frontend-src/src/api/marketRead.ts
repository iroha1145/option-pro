import { ApiError, get } from './client.ts';

interface MarketReadOptions {
  /** Successful response freshness window. */
  ttlMs?: number;
  /** Maximum age for reusing the same real response during 429/5xx failures. */
  staleMs?: number;
  /** Bypass a fresh local value after a completed background refresh. */
  force?: boolean;
}

interface MarketReadEntry {
  value: unknown;
  expiresAt: number;
  staleUntil: number;
  touchedAt: number;
}

const DEFAULT_TTL_MS = 15_000;
const DEFAULT_STALE_MS = 5 * 60_000;
const MAX_ENTRIES = 256;
const cache = new Map<string, MarketReadEntry>();
const inFlight = new Map<string, Promise<unknown>>();
const pathVersions = new Map<string, number>();
const forceBackoffExemptions = new Set<string>();
let marketBackoffUntil = 0;

function prune(now: number): void {
  for (const [key, entry] of cache) {
    if (entry.staleUntil <= now) cache.delete(key);
  }
  if (cache.size <= MAX_ENTRIES) return;
  const removeCount = cache.size - MAX_ENTRIES;
  [...cache.entries()]
    .sort((a, b) => a[1].touchedAt - b[1].touchedAt)
    .slice(0, removeCount)
    .forEach(([key]) => cache.delete(key));
}

/**
 * Shared read path for stock-detail market snapshots.
 *
 * Components requesting the same URL share one promise, successful values have
 * a short bounded cache, and a gateway 429 pauses the whole stock-detail read
 * group. Only previously received provider data may be reused; this module
 * never synthesizes a response.
 */
export function marketGet<T>(
  path: string,
  options: MarketReadOptions = {},
): Promise<T> {
  const now = Date.now();
  prune(now);
  const hit = cache.get(path);
  if (hit) hit.touchedAt = now;

  if (!options.force && hit && hit.expiresAt > now) {
    return Promise.resolve(hit.value as T);
  }
  const pending = inFlight.get(path);
  // Path invalidation explicitly detaches work that predates an owner pull.
  // Any request still registered here started after the latest invalidation,
  // so forced readers should share it instead of duplicating provider calls.
  if (pending) return pending as Promise<T>;

  // A completed owner pull grants the exact refreshed URL one follow-up read.
  // Consume the grant when that force read starts, even when no pause is
  // currently active, so a later request cannot reuse a stale exemption.
  const bypassSharedBackoff =
    options.force && forceBackoffExemptions.delete(path);
  if (marketBackoffUntil > now && !bypassSharedBackoff) {
    if (!options.force && hit && hit.staleUntil > now) {
      return Promise.resolve(hit.value as T);
    }
    return Promise.reject(
      new ApiError(429, '市场数据请求过于频繁，请稍后重试', {
        bizCode: 'rate_limited',
        retryable: true,
        retryAfter: Math.ceil((marketBackoffUntil - now) / 1000),
      }),
    );
  }

  const ttlMs = Math.max(0, options.ttlMs ?? DEFAULT_TTL_MS);
  const staleMs = Math.max(ttlMs, options.staleMs ?? DEFAULT_STALE_MS);
  const requestVersion = pathVersions.get(path) ?? 0;
  const request = get<T>(path)
    .then((value) => {
      const completedAt = Date.now();
      // A completed manual pull may invalidate an older GET while that GET is
      // still in flight. Never let the older response overwrite the newly
      // fetched provider result.
      if ((pathVersions.get(path) ?? 0) === requestVersion) {
        cache.set(path, {
          value,
          expiresAt: completedAt + ttlMs,
          staleUntil: completedAt + staleMs,
          touchedAt: completedAt,
        });
        prune(completedAt);
      }
      return value;
    })
    .catch((error: unknown) => {
      const failedAt = Date.now();
      if (error instanceof ApiError && error.code === 429) {
        const retrySeconds = Math.min(
          300,
          Math.max(1, Math.ceil(error.retryAfter ?? 60)),
        );
        marketBackoffUntil = Math.max(
          marketBackoffUntil,
          failedAt + retrySeconds * 1000,
        );
      }
      const stale = cache.get(path);
      if (
        !options.force
        && stale
        && stale.staleUntil > failedAt
        && error instanceof ApiError
        && (error.code === 429 || error.code >= 500)
      ) {
        stale.touchedAt = failedAt;
        return stale.value as T;
      }
      throw error;
    })
    .finally(() => {
      if (inFlight.get(path) === request) inFlight.delete(path);
    });
  inFlight.set(path, request);
  return request;
}

/**
 * Invalidate resources that were explicitly refreshed by an owner action.
 * Forced follow-up reads already bypass the shared 429 pause. Keep that pause
 * intact for unrelated market endpoints so one successful stock pull cannot
 * restart a provider request storm.
 */
export function resetMarketReadPaths(paths: string[]): void {
  for (const path of paths) {
    cache.delete(path);
    // Detach reads that started before the successful owner action. Their
    // responses may still resolve for their original caller, but versioning
    // below prevents them from repopulating this cache.
    inFlight.delete(path);
    pathVersions.set(path, (pathVersions.get(path) ?? 0) + 1);
    forceBackoffExemptions.add(path);
  }
}

/** Test-only reset; production callers should rely on bounded expiry. */
export function resetMarketReadState(): void {
  cache.clear();
  inFlight.clear();
  pathVersions.clear();
  forceBackoffExemptions.clear();
  marketBackoffUntil = 0;
}
