import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { API_MODE, ApiError } from '@/api/client';
import { useAccess } from '@/hooks/useAccess';
import { localeTag, t } from '@/i18n/core';
import { clearCatalystReadCache } from './api';
import type { ResourcePolicy, ResourceSnapshot } from './resourceCache';
import { catalystResources, retainCatalystRuntime } from './resourceRuntime';

const EMPTY: ResourceSnapshot<never> = Object.freeze({
  data: null, error: null, refreshing: false, validatedAt: 0, restored: false,
});
const commit = typeof document === 'undefined' ? ''
  : document.querySelector('meta[name="x-app-commit"]')?.getAttribute('content') ?? '';

export function useCatalystResource<T>(
  resourceKey: string, policy: ResourcePolicy<T>, loader: (previous: T | null) => Promise<T>,
) {
  const access = useAccess();
  // Never restore another role's snapshot before /access/status has answered.
  const enabled = !access.loading && !access.identityUnavailable;
  const key = JSON.stringify(['catalysts-v1', API_MODE, commit, localeTag(), access.role, access.username, resourceKey]);
  // Freeze the loader per key. A mutable ref shared across filter changes could
  // make an old subscribed cache entry fetch the NEW filter under the OLD key.
  const load = useCallback((previous: T | null) => loader(previous), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const subscribe = useCallback((listener: () => void) => enabled
    ? catalystResources.subscribe(key, policy, listener) : () => {}, [enabled, key, policy]);
  const getSnapshot = useCallback(() => enabled
    ? catalystResources.snapshot<T>(key, policy) : EMPTY as ResourceSnapshot<T>, [enabled, key, policy]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY as ResourceSnapshot<T>);
  useEffect(() => {
    if (!enabled) return;
    void catalystResources.ensure(key, policy, load);
    return retainCatalystRuntime();
  }, [enabled, key, policy, load]);
  const refresh = useCallback(() => {
    if (!enabled) return;
    clearCatalystReadCache();
    void catalystResources.ensure(key, policy, load);
  }, [enabled, key, policy, load]);
  const update = useCallback((change: (data: T | null) => T | null, expected?: T | null) =>
    enabled && catalystResources.update(key, policy, change, expected), [enabled, key, policy]);
  const error = snapshot.error instanceof ApiError ? snapshot.error : snapshot.error
    ? new ApiError(500, snapshot.error instanceof Error ? snapshot.error.message : t('加载失败'))
    : access.identityUnavailable ? new ApiError(503, t('加载失败')) : null;
  return { ...snapshot, error, loading: snapshot.data === null && !error,
    lastUpdatedAt: snapshot.validatedAt || null, refresh, update, key };
}
