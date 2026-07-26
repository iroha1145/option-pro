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
  /**
   * 一次性挂载全部。
   *
   * 打印用：只挂了 24 张卡时，打印出来的也只有 24 张 —— 数据在内存里，
   * 但纸上没有。浏览器原生查找（⌘F）同样只能找到已挂载的部分，那个限制
   * 无法在页面里可靠拦截，因此不在这里假装解决。
   */
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

  /**
   * 只夹紧，不回退。
   *
   * 原先是「条数一变就退回首批」。但这个列表每 60 秒轮询一次：只要覆盖范围或
   * 用户自选变动一条，已经加载到 96 条的人就会被打回 24 条 —— 页面高度瞬间少掉
   * 约一万两千像素，浏览器把滚动位置夹到底，人直接被甩到列表的另一处。
   *
   * 用户要过的东西不该被一次后台刷新收回去；需要的只是别超过现有总数。
   */
  useEffect(() => {
    setLimit((current) => (current > total && total > 0 ? Math.max(initial, total) : current));
  }, [total, initial]);

  const loadAll = useCallback(() => setLimit(Number.MAX_SAFE_INTEGER), []);

  const loadMore = useCallback(() => {
    setLimit((current) => (current >= total ? current : current + step));
  }, [step, total]);

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
