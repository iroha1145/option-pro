import { useCallback, useMemo, useState } from 'react';
import { ApiError } from '@/api/client';
import { stocksApi } from '@/api/modules/stocks';
import type { WatchlistItem } from '@/api/types';
import { personalWatchlistRows } from '@/lib/personalWatchlist';
import { useAccess } from './useAccess';
import { usePersonalWatchlist } from './usePersonalWatchlist';
import { usePolling, type PollingState } from './usePolling';

type MoverSnapshot = {
  selection: object;
  items: WatchlistItem[] | null;
  error: ApiError | null;
  updatedAt: number | null;
};

/** 首页异动只切换自己的成员来源；财报和扫描统计仍使用公共池。 */
export function useHomeMovers(publicQuery: PollingState<WatchlistItem[]>) {
  const access = useAccess();
  const personal = usePersonalWatchlist();
  const scope = JSON.stringify([personal.key, personal.tickers]);
  const selection = useMemo(() => ({ scope }), [scope]);
  const [lastSuccess, setLastSuccess] = useState<MoverSnapshot | null>(null);
  const enabled = personal.enabled && !personal.loading && !personal.error && personal.tickers !== null;
  // 响应携带成员范围：成员改变后的第一帧也不能显示上一份行情或错误。
  const query = usePolling<MoverSnapshot>(async () => {
    try {
      const items = await stocksApi.watchlistFor(personal.tickers ?? [], true);
      return { selection, items, error: null, updatedAt: Date.now() };
    } catch (error) {
      return { selection, items: null, error: error instanceof ApiError ? error : new ApiError(500, String(error)), updatedAt: null };
    }
  }, 300_000, [selection], { enabled });
  const current = query.data?.selection === selection ? query.data : null;
  if (current && current.items !== null && lastSuccess !== current) setLastSuccess(current);
  // 只保留当前成员任期的成功读数；A→B→A 也不能重新露出第一次 A 的结果。
  const successful = current?.items ? current : lastSuccess?.selection === selection ? lastSuccess : null;
  const { error: memberError, loading: memberLoading, refresh: refreshMembers } = personal;
  const { refresh: refreshQuotes } = query;
  const retry = useCallback(() => {
    if (memberError || memberLoading || !enabled) void refreshMembers();
    else refreshQuotes({ force: true });
  }, [memberError, memberLoading, enabled, refreshMembers, refreshQuotes]);

  if (!access.hasConfirmedIdentity || access.loading || access.identityUnavailable) {
    return {
      data: null, loading: access.loading || !access.identityUnavailable,
      error: personal.error ? new ApiError(503, personal.error) : null,
      refreshing: false, lastUpdatedAt: null, refresh: retry, personal: false,
      tickers: [] as string[],
    };
  }
  if (!access.canManageWatchlist) {
    return { ...publicQuery, personal: false, tickers: (publicQuery.data ?? []).map((item) => item.ticker) };
  }
  return {
    data: successful?.items ? personalWatchlistRows(personal.tickers ?? [], successful.items) : null,
    loading: personal.loading || (enabled && !current),
    error: personal.error ? new ApiError(503, personal.error) : current?.error ?? null,
    refreshing: personal.busy || query.refreshing,
    lastUpdatedAt: successful?.updatedAt ?? null,
    refresh: retry, personal: true, tickers: personal.tickers ?? [],
  };
}
