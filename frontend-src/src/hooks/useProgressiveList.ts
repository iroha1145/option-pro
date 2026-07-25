/**
 * 渐进挂载长列表。
 *
 * 测量结果：自选页作为访客一次性挂载 214 张卡片 —— 移动端 39,878px（约 49 屏）、
 * 3,943 个 DOM 节点、445 个 SVG、222 个入场动画元素。Lighthouse 的
 * Style & Layout 因此高达 1,285ms，几乎与脚本执行持平。
 *
 * 这里只限制**渲染多少**，不限制**拿到多少**：排序、统计、涨跌家数一律仍在完整
 * 数据上计算，用户滚动到底部会自动接着挂载。分页会改变「这就是全部」的语义，
 * 渐进挂载不会。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface ProgressiveList<T> {
  /** 当前允许渲染的条目。 */
  visible: T[];
  /** 是否还有没挂载的条目。 */
  hasMore: boolean;
  /** 还剩多少条。 */
  remaining: number;
  /** 挂载下一批。 */
  loadMore: () => void;
  /** 一次性挂载全部（打印、Ctrl+F 查找等场景）。 */
  loadAll: () => void;
  /**
   * 挂在列表末尾的哨兵元素上；进入视口即自动加载下一批。
   * 不支持 IntersectionObserver 时不会自动加载，此时「加载更多」按钮仍然可用。
   */
  sentinelRef: (node: HTMLElement | null) => void;
}

export function useProgressiveList<T>(
  items: T[],
  { initial = 24, step = 24 }: { initial?: number; step?: number } = {},
): ProgressiveList<T> {
  const [limit, setLimit] = useState(initial);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const total = items.length;

  // 数据集换了（切换用户自选、刷新拿到新列表）就退回首批，
  // 否则上一份列表滚到 200 条的状态会让新列表一次性全挂。
  const identity = total;
  useEffect(() => {
    setLimit(initial);
  }, [identity, initial]);

  const loadMore = useCallback(() => {
    setLimit((current) => (current >= total ? current : current + step));
  }, [step, total]);

  const loadAll = useCallback(() => setLimit(Number.MAX_SAFE_INTEGER), []);

  const sentinelRef = useCallback(
    (node: HTMLElement | null) => {
      observerRef.current?.disconnect();
      if (!node || typeof IntersectionObserver === 'undefined') return;
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) loadMore();
        },
        // 提前一屏开始挂载，让滚动过程中看不出分批。
        { rootMargin: '600px 0px' },
      );
      observer.observe(node);
      observerRef.current = observer;
    },
    [loadMore],
  );

  useEffect(() => () => observerRef.current?.disconnect(), []);

  const visible = useMemo(
    () => (limit >= total ? items : items.slice(0, limit)),
    [items, limit, total],
  );

  return {
    visible,
    hasMore: limit < total,
    remaining: Math.max(0, total - limit),
    loadMore,
    loadAll,
    sentinelRef,
  };
}
