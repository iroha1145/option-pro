import { useCallback, useEffect, useRef, useState } from 'react';
import { accountApi, type AccountWatchlist } from '@/api/modules/account';
import { ApiError } from '@/api/client';
import { useAccess } from './useAccess';
import { t } from '@/i18n/core';

const CHANGED = 'optix:personal-watchlist-changed';
const pendingWrites = new Set<string>();
type Snapshot = { key: string; data: AccountWatchlist | null; error: string | null; ready: boolean };

/** Guard stale reads and identity changes, including navigation during a save. */
export function usePersonalWatchlist() {
  const access = useAccess();
  const key = `${access.role}\0${access.username ?? ''}`;
  const enabled = access.canManageWatchlist && !access.loading && !access.identityUnavailable;
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: '', data: null, error: null, ready: false });
  const [busy, setBusy] = useState(false);
  const life = useRef({ key: '', generation: 0, alive: false });

  const refresh = useCallback(async () => {
    if (!enabled || !life.current.alive || life.current.key !== key || pendingWrites.has(key)) return;
    const generation = ++life.current.generation;
    try {
      const data = await accountApi.watchlist();
      if (life.current.alive && life.current.key === key && life.current.generation === generation) {
        setSnapshot({ key, data, error: null, ready: true });
      }
    } catch (error) {
      if (life.current.alive && life.current.key === key && life.current.generation === generation) {
        setSnapshot((current) => ({ key, data: current.key === key ? current.data : null, error: error instanceof Error ? error.message : t('请稍后再试'), ready: true }));
      }
    }
  }, [enabled, key]);

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

  const edit = useCallback(async (add: string[], remove: string[]) => {
    if (!enabled || !life.current.alive || life.current.key !== key || pendingWrites.has(key)) {
      throw new ApiError(409, t('请等待当前操作完成'));
    }
    pendingWrites.add(key);
    window.dispatchEvent(new CustomEvent(CHANGED, { detail: key }));
    try {
      const next = await accountApi.edit(add, remove);
      if (add.some((symbol) => !next.tickers.includes(symbol)) || remove.some((symbol) => next.tickers.includes(symbol))) {
        throw new ApiError(502, t('自选修改尚未确认，请重试'));
      }
      if (life.current.alive && life.current.key === key) {
        setSnapshot({ key, data: next, error: null, ready: true });
      }
      return next;
    } finally {
      pendingWrites.delete(key);
      window.dispatchEvent(new CustomEvent(CHANGED, { detail: key }));
    }
  }, [enabled, key]);

  const current = enabled && snapshot.key === key ? snapshot : null;
  return {
    key, enabled, tickers: current?.data?.tickers ?? null,
    maxTickers: current?.data?.maxTickers ?? 50,
    loading: access.loading || (enabled && !current?.ready),
    error: access.identityUnavailable ? t('身份暂时无法确认，请稍后重试') : current?.error ?? null,
    busy, refresh, edit,
    add: (symbol: string) => edit([symbol], []),
    remove: (symbol: string) => edit([], [symbol]),
  };
}
