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
import { useCallback, useRef, useState, type KeyboardEvent } from 'react';
import type { EChartsInstance } from '@/lib/chart';

export function useChartCursor(count: number, valueText: (index: number) => string) {
  const chartRef = useRef<EChartsInstance | null>(null);
  const [index, setIndex] = useState<number | null>(null);

  const onInit = useCallback((chart: EChartsInstance) => {
    chartRef.current = chart;
    chart.on('updateAxisPointer', (event: unknown) => {
      const value = (event as { axesInfo?: { value?: unknown }[] }).axesInfo?.[0]?.value;
      if (typeof value === 'number' && Number.isInteger(value)) setIndex(value);
    });
    chart.getZr().on('globalout', () => setIndex(null));
  }, []);

  const moveTo = useCallback((next: number | null) => {
    const chart = chartRef.current;
    setIndex(next);
    if (!chart || chart.isDisposed()) return;
    if (next === null) {
      chart.dispatchAction({ type: 'hideTip' });
      return;
    }
    const x = chart.convertToPixel({ xAxisIndex: 0 }, next);
    if (typeof x === 'number' && Number.isFinite(x)) chart.dispatchAction({ type: 'showTip', x, y: chart.getHeight() / 2 });
  }, []);

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
