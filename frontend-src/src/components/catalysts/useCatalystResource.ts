import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
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
  const { identityUnavailable, refresh: refreshIdentity } = access;
  // Never restore another role's snapshot before /access/status has answered.
  const enabled = !access.loading && !access.identityUnavailable;
  const key = JSON.stringify(['catalysts-v1', API_MODE, commit, localeTag(), access.role, access.username, resourceKey]);
  // Freeze the loader per key. A mutable ref shared across filter changes could
  // make an old subscribed cache entry fetch the NEW filter under the OLD key.
  const load = useCallback((previous: T | null) => loader(previous), [key]); // eslint-disable-line react-hooks/exhaustive-deps, react-hooks/preserve-manual-memoization
  const subscribe = useCallback((listener: () => void) => enabled
    ? catalystResources.subscribe(key, policy, listener) : () => {}, [enabled, key, policy]);
  const getSnapshot = useCallback(() => enabled
    ? catalystResources.snapshot<T>(key, policy) : EMPTY as ResourceSnapshot<T>, [enabled, key, policy]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY as ResourceSnapshot<T>);
  // Keep only a snapshot already shown by this mounted hook. Do not restore
  // persistent/cache data while identity is unknown, or reuse another key.
  const [confirmed, setConfirmed] = useState<{ key: string; snapshot: ResourceSnapshot<T> } | null>(null);
  if (enabled && snapshot.data !== null && (confirmed?.key !== key || confirmed.snapshot !== snapshot)) {
    setConfirmed({ key, snapshot });
  }
  const displayed = enabled ? snapshot : confirmed?.key === key ? confirmed.snapshot : EMPTY as ResourceSnapshot<T>;
  useEffect(() => {
    if (!enabled) return;
    void catalystResources.ensure(key, policy, load);
    return retainCatalystRuntime();
  }, [enabled, key, policy, load]);
  const refresh = useCallback(() => {
    if (identityUnavailable) {
      void refreshIdentity().catch(() => undefined);
      return;
    }
    if (!enabled) return;
    clearCatalystReadCache();
    void catalystResources.ensure(key, policy, load);
  }, [enabled, key, policy, load, identityUnavailable, refreshIdentity]);
  const update = useCallback((change: (data: T | null) => T | null, expected?: T | null) =>
    enabled && catalystResources.update(key, policy, change, expected), [enabled, key, policy]);
  const error = access.identityUnavailable ? new ApiError(503, t('身份暂时无法确认，请稍后重试'))
    : displayed.error instanceof ApiError ? displayed.error : displayed.error
      ? new ApiError(500, displayed.error instanceof Error ? displayed.error.message : t('加载失败')) : null;
  return { ...displayed, refreshing: enabled && displayed.refreshing, error, loading: displayed.data === null && !error,
    lastUpdatedAt: displayed.validatedAt || null, refresh, update, key, enabled };
}
