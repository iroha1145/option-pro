import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '@/api/client';
import { t } from '../i18n/core.ts';

export interface PollingState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;      // 首次加载中
  refreshing: boolean;   // 后续轮询中
  lastUpdatedAt: number | null;
  /**
   * force：手动刷新的强制语义。普通 refresh 在同世代已有在途请求时是
   * 合流 no-op——写路径（POST 成功后）必须用 force 开新世代，否则旧在途
   * 响应最后返回时会把刷新前的数据写回页面（审计 P1-01）。
   */
  refresh: (options?: { force?: boolean }) => void;
}

export interface PollingOptions<T> {
  /**
   * 冷启动恢复:首个网络响应到达前,用上一份持久化的真实数据先行填充。
   * loading 保持 true(界面据此显示"确认中"而非把旧数据冒充刚更新);
   * fetcher 一旦兑现,以网络结果为准。resolve null 表示没有可恢复的数据。
   */
  restore?: () => Promise<T | null>;
}

/**
 * 轮询 Hook（design.md §11）
 * - intervalMs 为 null / <=0 时不轮询，仅首取
 * - 页面不可见（document.visibilityState）时暂停，回到前台立即补一次
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number | null,
  deps: unknown[] = [],
  options?: PollingOptions<T>,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const fetcherRef = useRef(fetcher);
  const restoreRef = useRef(options?.restore);
  useEffect(() => {
    fetcherRef.current = fetcher;
    restoreRef.current = options?.restore;
  }, [fetcher, options?.restore]);
  const generationRef = useRef(0);
  const activeGenerationsRef = useRef(new Set<number>());
  const inFlightGenerationsRef = useRef(new Set<number>());
  const settledGenerationsRef = useRef(new Set<number>());
  const restoreAttemptedRef = useRef(false);

  const tick = useCallback(async (first = false, generation = generationRef.current) => {
    if (inFlightGenerationsRef.current.has(generation)) return;
    inFlightGenerationsRef.current.add(generation);
    if (first) setLoading(true);
    else setRefreshing(true);
    try {
      const result = await fetcherRef.current();
      if (!activeGenerationsRef.current.has(generation) || generation !== generationRef.current) return;
      settledGenerationsRef.current.add(generation);
      setData(result);
      setError(null);
      setLastUpdatedAt(Date.now());
    } catch (e) {
      if (!activeGenerationsRef.current.has(generation) || generation !== generationRef.current) return;
      settledGenerationsRef.current.add(generation);
      setError(e instanceof ApiError ? e : new ApiError(500, e instanceof Error ? e.message : t('未知错误')));
    } finally {
      inFlightGenerationsRef.current.delete(generation);
      if (activeGenerationsRef.current.has(generation) && generation === generationRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  const refresh = useCallback((options?: { force?: boolean }) => {
    if (options?.force) {
      // 开新世代：旧在途响应从此过不了世代守卫（写不进 data/error），
      // 新世代不受「同世代在途即跳过」的合流闸限制，立即真正发起请求。
      const next = generationRef.current + 1;
      generationRef.current = next;
      activeGenerationsRef.current.add(next);
      void tick(false, next);
      return;
    }
    void tick(false);
  }, [tick]);

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const activeGenerations = activeGenerationsRef.current;
    activeGenerations.add(generation);
    let timer: ReturnType<typeof setInterval> | null = null;
    const restore = restoreRef.current;
    if (restore && !restoreAttemptedRef.current) {
      // 只在首个世代恢复一次;网络结果先到时恢复值直接丢弃。
      restoreAttemptedRef.current = true;
      void restore().then((restored) => {
        if (restored === null || restored === undefined) return;
        if (generation !== generationRef.current) return;
        if (settledGenerationsRef.current.has(generation)) return;
        setData((current) => (current === null ? restored : current));
      }).catch(() => undefined);
    }
    void tick(true, generation);
    if (intervalMs && intervalMs > 0) {
      timer = setInterval(() => {
        if (document.visibilityState === 'visible') void tick(false);
      }, intervalMs);
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible' && intervalMs && intervalMs > 0) void tick(false);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      // 本效果任期内 force 派生的世代一并回收（范围 [本世代, 当前世代]：
      // React 先跑旧效果清理再跑新效果，此刻 generationRef 尚未进入下一任期）。
      for (const spawned of Array.from(activeGenerations)) {
        if (spawned >= generation && spawned <= generationRef.current) {
          activeGenerations.delete(spawned);
        }
      }
      if (timer) clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  return { data, error, loading, refreshing, lastUpdatedAt, refresh };
}
