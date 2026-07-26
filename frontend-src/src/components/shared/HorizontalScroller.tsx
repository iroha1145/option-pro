/**
 * 横向滚动条（带真实的可操作入口）。
 *
 * 这些卡片带一直是 `overflow-x-auto` + `no-scrollbar`：滚动条被藏起来了。
 * 触屏可以直接划，所以问题一直没暴露；但桌面端用普通鼠标（没有横向滚轮、
 * 没有触控板横扫）根本无从滚动 —— 最右边那张卡永远是半截，而且**看不出
 * 右边还有没有内容**。热点带 8 张 260px 的卡合计 2204px，是这个问题最明显的
 * 地方。
 *
 * 因此补三件事，且都不改变现有视觉语言：
 *   1. 溢出时两侧出现渐隐遮罩 —— 明确告诉人「还有」；
 *   2. 桌面端出现左右按钮，同时也让键盘用户能操作；
 *   3. 触屏保持原样，滑动和 snap 都不变。
 *
 * 按钮只在真的溢出、且还能往那个方向滚时出现，否则会出现一个点了没反应的键。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { computeScrollEdges } from '@/lib/scrollEdges';

interface Props {
  children: ReactNode;
  /** 落在外层容器上（负 margin、内边距等）。 */
  className?: string;
  /** 落在实际滚动的元素上。 */
  scrollerClassName?: string;
  /** 无障碍名称，例如「热点主题带」。 */
  label?: string;
}

export default function HorizontalScroller({
  children,
  className,
  scrollerClassName,
  label,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    // 1px 容差：子像素宽度会让 scrollWidth 永远比 clientWidth 大一点点。
    const next = computeScrollEdges(node.scrollLeft, node.scrollWidth, node.clientWidth);
    // 值没变就返回原对象，让 React 直接跳过这次渲染。
    //
    // 无条件 setEdges({...}) 每次都是新对象，一定触发重渲染；再加上下面的 effect
    // 一度把 children 放进依赖（每次渲染都是新引用），就成了
    // measure → setState → 重渲染 → effect 重跑 → measure 的无限循环。
    setEdges((prev) =>
      prev.left === next.left && prev.right === next.right ? prev : next,
    );
  }, []);

  // 监听只挂一次：依赖里不能有每次渲染都变的值。
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    measure();
    node.addEventListener('scroll', measure, { passive: true });
    const observer =
      typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    // 观察滚动容器本身（视口变化）和内部轨道（卡片增减导致宽度变化）。
    observer?.observe(node);
    const track = node.firstElementChild;
    if (track) observer?.observe(track);
    return () => {
      node.removeEventListener('scroll', measure);
      observer?.disconnect();
    };
  }, [measure]);

  // 内容换了要重新判断，但只是重新测量，不重挂监听。
  useEffect(() => {
    measure();
  }, [children, measure]);

  const nudge = useCallback((direction: 1 | -1) => {
    const node = ref.current;
    if (!node) return;
    // 翻将近一屏，留一点重叠，免得刚划过去的那张完全看不见了。
    node.scrollBy({ left: direction * node.clientWidth * 0.85, behavior: 'smooth' });
  }, []);

  const arrow = (side: 'left' | 'right') => {
    const active = side === 'left' ? edges.left : edges.right;
    if (!active) return null;
    return (
      <button
        type="button"
        aria-label={side === 'left' ? '向左滚动' : '向右滚动'}
        onClick={() => nudge(side === 'left' ? -1 : 1)}
        className={cn(
          // 触屏直接划就行，按钮只给指针设备。
          'absolute top-1/2 z-20 hidden size-8 -translate-y-1/2 items-center justify-center',
          'rounded-full border border-line-strong bg-card text-ink-500 shadow-sh-2',
          'transition-colors duration-fast hover:text-ink-800 focus-visible:outline-none',
          'focus-visible:ring-2 focus-visible:ring-brand-500/30 md:inline-flex',
          side === 'left' ? 'left-1 md:left-2' : 'right-1 md:right-2',
        )}
      >
        {/* 图标集里只有 chevron-right，向左用它旋转 180°，不为此扩表。 */}
        <Icon
          name="chevron-right"
          size={15}
          className={side === 'left' ? 'rotate-180' : undefined}
        />
      </button>
    );
  };

  return (
    <div className={cn('relative', className)}>
      {/* 渐隐遮罩：不吃点击，只负责说明「这边还有」 */}
      {edges.left && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 left-0 z-10 w-10 bg-gradient-to-r from-paper to-transparent"
        />
      )}
      {edges.right && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 z-10 w-10 bg-gradient-to-l from-paper to-transparent"
        />
      )}
      {arrow('left')}
      {arrow('right')}
      <div
        ref={ref}
        className={cn('overflow-x-auto no-scrollbar', scrollerClassName)}
        /* 键盘可达：溢出容器要能获得焦点才能用方向键滚动。 */
        tabIndex={edges.left || edges.right ? 0 : -1}
        role={label ? 'group' : undefined}
        aria-label={label}
      >
        {children}
      </div>
    </div>
  );
}
