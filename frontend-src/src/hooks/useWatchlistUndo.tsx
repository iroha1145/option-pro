import { createContext, useCallback, useContext, useLayoutEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { ApiError } from '@/api/client';
import { watchlistErrorMessage, type WatchlistRemoval } from '@/api/modules/account';
import { usePersonalWatchlist } from './usePersonalWatchlist';
import { useToast } from './useToast';
import { t } from '@/i18n/core';

type Removal = WatchlistRemoval & { principalKey: string };
const WatchlistUndoContext = createContext<((ticker: string, principalKey: string) => Promise<Removal>) | null>(null);

/** Own undo execution above the routes, so navigation does not retire it. */
export function WatchlistUndoProvider({ children }: { children: ReactNode }) {
  const personal = usePersonalWatchlist({ load: false });
  const toast = useToast();
  const latest = useRef(personal);
  const principal = useRef({ key: personal.key, enabled: personal.enabled, epoch: 0 });
  useLayoutEffect(() => {
    latest.current = personal;
    const retired = principal.current.key !== personal.key || (principal.current.enabled && !personal.enabled);
    principal.current = { key: personal.key, enabled: personal.enabled, epoch: principal.current.epoch + (retired ? 1 : 0) };
  }, [personal]);
  const removeWithUndo = useCallback(async (ticker: string, principalKey: string) => {
    const epoch = principal.current.epoch;
    if (!latest.current.isCurrentPrincipal(principalKey)) {
      throw new ApiError(409, t('登录身份已变化，请重新操作'), { bizCode: 'watchlist_identity_changed' });
    }
    const removal = await latest.current.remove(ticker);
    if (principal.current.epoch !== epoch || !latest.current.isCurrentPrincipal(principalKey)) {
      throw new ApiError(409, t('登录身份已变化，请重新操作'), { bizCode: 'watchlist_identity_changed' });
    }
    if (!removal.undo) return removal;
    const undo = removal.undo;
    let restoring = false;
    let restored = false;
    toast.info(t('已移出自选'), undo.ticker, {
      action: { label: t('撤销'), onClick: () => {
        if (restoring || restored) return;
        restoring = true;
        const restore = async () => {
          if (principal.current.epoch !== epoch) {
            throw new Error(t('登录身份已变化，请重新操作'));
          }
          await latest.current.restore(undo, removal.principalKey);
          if (principal.current.epoch !== epoch || !latest.current.isCurrentPrincipal(removal.principalKey)) {
            throw new Error(t('登录身份已变化，请重新操作'));
          }
          restored = true;
          toast.success(t('已恢复到自选'), undo.ticker);
        };
        void restore().catch((error) => {
          toast.error(t('恢复失败'), watchlistErrorMessage(error, removal.maxTickers));
        }).finally(() => { restoring = false; });
      } },
    });
    return removal;
  }, [toast]);
  return <WatchlistUndoContext.Provider value={removeWithUndo}>{children}</WatchlistUndoContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWatchlistUndo() {
  const context = useContext(WatchlistUndoContext);
  if (!context) throw new Error('useWatchlistUndo requires WatchlistUndoProvider');
  return context;
}
