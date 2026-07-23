import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '@/api/client';

export interface PollingState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;      // 首次加载中
  refreshing: boolean;   // 后续轮询中
  lastUpdatedAt: number | null;
  refresh: () => void;
}

/**
 * 轮询 Hook（design.md §11）
 * - intervalMs 为 null / <=0 时不轮询，仅首取
 * - 页面不可见（document.visibilityState）时暂停，回到前台立即补一次
 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number | null, deps: unknown[] = []): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const generationRef = useRef(0);
  const activeGenerationsRef = useRef(new Set<number>());
  const inFlightGenerationsRef = useRef(new Set<number>());

  const tick = useCallback(async (first = false, generation = generationRef.current) => {
    if (inFlightGenerationsRef.current.has(generation)) return;
    inFlightGenerationsRef.current.add(generation);
    if (first) setLoading(true);
    else setRefreshing(true);
    try {
      const result = await fetcherRef.current();
      if (!activeGenerationsRef.current.has(generation) || generation !== generationRef.current) return;
      setData(result);
      setError(null);
      setLastUpdatedAt(Date.now());
    } catch (e) {
      if (!activeGenerationsRef.current.has(generation) || generation !== generationRef.current) return;
      setError(e instanceof ApiError ? e : new ApiError(500, e instanceof Error ? e.message : '未知错误'));
    } finally {
      inFlightGenerationsRef.current.delete(generation);
      if (activeGenerationsRef.current.has(generation) && generation === generationRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  const refresh = useCallback(() => {
    void tick(false);
  }, [tick]);

  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const activeGenerations = activeGenerationsRef.current;
    activeGenerations.add(generation);
    let timer: ReturnType<typeof setInterval> | null = null;
    void tick(true, generation);
    if (intervalMs && intervalMs > 0) {
      timer = setInterval(() => {
        if (document.visibilityState === 'visible') void tick(false, generation);
      }, intervalMs);
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible' && intervalMs && intervalMs > 0) void tick(false, generation);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      activeGenerations.delete(generation);
      if (timer) clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  return { data, error, loading, refreshing, lastUpdatedAt, refresh };
}
