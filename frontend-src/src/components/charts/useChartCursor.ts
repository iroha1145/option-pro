/**
 * 图表读数游标：指针悬停或键盘逐点移动时，告诉调用方当前指着第几个点，
 * 调用方把表头读数换成这一点的值；指针离开或按 Esc 回到最新值（index 为 null）。
 *
 * - 指针：监听 ECharts 的 updateAxisPointer，类目轴上 value 就是点的下标；事件里没有
 *   下标（比如指到某条线的空值处）不清空读数，只有指针离开图表（globalout）才回到最新。
 * - 键盘：图表容器是一个 role="slider"，←/→ 逐点、Home/End 到两端、Esc 放开；
 *   移动时按该点的横坐标像素发 showTip，提示框跟着走，不依赖哪条线在该点有值；
 *   失焦时收起。
 * 只用于类目横轴的折线图；K 线图有自己的十字线与提示，不走这里。
 */
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import type { EChartsInstance } from '@/lib/chart';

export function useChartCursor(dates: readonly string[], valueText: (index: number) => string) {
  const count = dates.length;
  const sequenceKey = JSON.stringify(dates);
  const chartRef = useRef<EChartsInstance | null>(null);
  const [cursor, setCursor] = useState<{ sequenceKey: string; count: number; index: number | null }>({
    sequenceKey, count, index: null,
  });
  // 日期序列换了，旧下标在本轮渲染就失效。不能等 effect：调用方此刻就要读日期和数值。
  // 同样点数的新区间也要复位；日期不变、仅分数刷新时则保留正在查看的那一天。
  const index = cursor.sequenceKey === sequenceKey ? cursor.index : null;
  if (cursor.sequenceKey !== sequenceKey) setCursor({ sequenceKey, count, index: null });

  const clearPointer = useCallback(() => {
    const chart = chartRef.current;
    if (!chart || chart.isDisposed()) return;
    // hideTip 只收起提示框；leave 同时隐藏轴游标，并取消它留下的点高亮。
    chart.dispatchAction({ type: 'updateAxisPointer', currTrigger: 'leave' });
    chart.dispatchAction({ type: 'hideTip' });
  }, []);

  useEffect(() => {
    clearPointer();
  }, [sequenceKey, clearPointer]);

  const onInit = useCallback((chart: EChartsInstance) => {
    chartRef.current = chart;
    chart.on('updateAxisPointer', (event: unknown) => {
      const value = (event as { axesInfo?: { value?: unknown }[] }).axesInfo?.[0]?.value;
      if (typeof value === 'number' && Number.isInteger(value)) {
        setCursor((previous) => value >= 0 && value < previous.count && previous.index !== value
          ? { ...previous, index: value }
          : previous);
      }
    });
    chart.getZr().on('globalout', () => setCursor((previous) => previous.index === null ? previous : { ...previous, index: null }));
  }, []);

  const moveTo = useCallback((next: number | null) => {
    const chart = chartRef.current;
    setCursor((previous) => previous.index === next ? previous : { ...previous, index: next });
    if (!chart || chart.isDisposed()) return;
    if (next === null) {
      clearPointer();
      return;
    }
    const x = chart.convertToPixel({ xAxisIndex: 0 }, next);
    if (typeof x === 'number' && Number.isFinite(x)) chart.dispatchAction({ type: 'showTip', x, y: chart.getHeight() / 2 });
  }, [clearPointer]);

  const last = count - 1;
  const current = index ?? last;
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (count === 0) return;
    const step: Record<string, number | null> = {
      ArrowLeft: Math.max(0, current - 1),
      ArrowRight: Math.min(last, current + 1),
      Home: 0,
      End: last,
      Escape: null,
    };
    if (!(event.key in step)) return;
    event.preventDefault();
    moveTo(step[event.key]);
  };

  const sliderProps = {
    role: 'slider' as const,
    tabIndex: count > 0 ? 0 : -1,
    'aria-valuemin': 0,
    'aria-valuemax': Math.max(0, last),
    'aria-valuenow': Math.max(0, current),
    'aria-valuetext': count > 0 ? valueText(current) : undefined,
    onKeyDown,
    onBlur: () => moveTo(null),
  };

  return { index, onInit, sliderProps };
}
