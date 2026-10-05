import { useCallback, useEffect, useRef, useState } from 'react';
import { accountApi, watchlistErrorMessage, type AccountWatchlist, type WatchlistUndo } from '@/api/modules/account';
import { ApiError } from '@/api/client';
import { useAccess } from './useAccess';
import { t } from '@/i18n/core';

const CHANGED = 'optix:personal-watchlist-changed';
const pendingWrites = new Set<string>();
type Snapshot = { key: string; data: AccountWatchlist | null; error: string | null; ready: boolean };

/** Guard stale reads and identity changes, including navigation during a save. */
export function usePersonalWatchlist({ load = true }: { load?: boolean } = {}) {
  const access = useAccess();
  const { identityUnavailable, refresh: refreshIdentity } = access;
  const key = `${access.role}\0${access.username ?? ''}`;
  const enabled = access.canManageWatchlist && !access.loading && !access.identityUnavailable;
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: '', data: null, error: null, ready: false });
  const [busy, setBusy] = useState(false);
  const life = useRef({ key: '', generation: 0, alive: false });
  // Every saved callback reads the current eligibility, including a temporary
  // identity failure with the same principal key. An effect cannot refresh a
  // callback's captured `enabled` value.
  const live = useRef({ key, enabled });
  live.current = { key, enabled };
  const isCurrentPrincipal = useCallback((principalKey: string) =>
    life.current.alive && live.current.enabled && live.current.key === principalKey, []);

  const refresh = useCallback(async () => {
    if (!enabled || !load || !isCurrentPrincipal(key) || pendingWrites.has(key)) return;
    const generation = ++life.current.generation;
    try {
      const data = await accountApi.watchlist();
      if (life.current.alive && life.current.key === key && life.current.generation === generation) {
        setSnapshot({ key, data, error: null, ready: true });
      }
    } catch (error) {
      if (life.current.alive && life.current.key === key && life.current.generation === generation) {
        setSnapshot((current) => ({ key, data: current.key === key ? current.data : null, error: watchlistErrorMessage(error), ready: true }));
      }
    }
  }, [enabled, key, load, isCurrentPrincipal]);

  useEffect(() => {
    life.current = { key, generation: life.current.generation + 1, alive: true };
    setBusy(pendingWrites.has(key));
    void refresh();
    const changed = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== key) return;
      // A pending GET predating a write must not resurrect removed membership.
      life.current.generation += 1;
      setBusy(pendingWrites.has(key));
      if (!pendingWrites.has(key)) void refresh();
    };
    const focus = () => void refresh();
    window.addEventListener(CHANGED, changed);
    window.addEventListener('focus', focus);
    return () => {
      life.current.alive = false;
      life.current.generation += 1;
      window.removeEventListener(CHANGED, changed);
      window.removeEventListener('focus', focus);
    };
  }, [key, refresh]);

  const write = useCallback(async <Result extends AccountWatchlist>(
    principalKey: string, operation: () => Promise<Result>, validate: (next: Result) => boolean,
  ) => {
    if (!isCurrentPrincipal(principalKey)) {
      throw new ApiError(409, t('登录身份已变化，请重新操作'), { bizCode: 'watchlist_identity_changed' });
    }
    if (pendingWrites.has(principalKey)) throw new ApiError(409, t('请等待当前操作完成'));
    pendingWrites.add(principalKey);
    window.dispatchEvent(new CustomEvent(CHANGED, { detail: principalKey }));
    const generation = life.current.generation;
    try {
      const next = await operation();
      if (!validate(next)) throw new ApiError(502, t('自选修改尚未确认，请重试'));
      // A route unmount does not revoke the principal, but a confirmed account
      // change or identity suspension must not publish the old result as current.
      if (!live.current.enabled || live.current.key !== principalKey) {
        throw new ApiError(409, t('登录身份已变化，请重新操作'), { bizCode: 'watchlist_identity_changed' });
      }
      if (life.current.alive && life.current.key === principalKey && life.current.generation === generation) {
        setSnapshot({ key: principalKey, data: next, error: null, ready: true });
      }
      return next;
    } finally {
      pendingWrites.delete(principalKey);
      window.dispatchEvent(new CustomEvent(CHANGED, { detail: principalKey }));
    }
  }, [isCurrentPrincipal]);

  const edit = useCallback((add: string[], remove: string[]) => write(key,
    () => accountApi.edit(add, remove),
    (next) => add.every((symbol) => next.tickers.includes(symbol)) && remove.every((symbol) => !next.tickers.includes(symbol)),
  ), [key, write]);
  const remove = useCallback(async (symbol: string) => {
    const expectedUsername = access.username ?? (access.isOwner ? 'admin' : '');
    const next = await write(key, () => accountApi.remove(symbol, expectedUsername),
      (result) => !result.tickers.includes(symbol) && (!result.undo || result.undo.ticker === symbol));
    return { ...next, principalKey: key };
  }, [access.username, access.isOwner, key, write]);
  const restore = useCallback((undo: WatchlistUndo, principalKey: string) => write(principalKey,
    () => accountApi.restore(undo), (next) => next.tickers.includes(undo.ticker),
  ), [write]);

  // A transient identity failure suspends I/O, but does not revoke the last
  // confirmed principal. Keep only this mounted hook's matching display data.
  const current = access.canManageWatchlist && !access.loading && snapshot.key === key ? snapshot : null;
  const retry = useCallback(async () => {
    if (identityUnavailable) {
      await refreshIdentity().catch(() => undefined);
      // A successful confirmation enables the effect above to reload membership.
      return;
    }
    await refresh();
  }, [identityUnavailable, refreshIdentity, refresh]);
  return {
    key, enabled, tickers: current?.data?.tickers ?? null,
    maxTickers: current?.data?.maxTickers ?? 50,
    loading: access.loading || (enabled && !current?.ready),
    error: access.identityUnavailable ? t('身份暂时无法确认，请稍后重试') : current?.error ?? null,
    busy, refresh: retry, edit, remove, restore, isCurrentPrincipal,
    add: (symbol: string) => edit([symbol], []),
  };
}
