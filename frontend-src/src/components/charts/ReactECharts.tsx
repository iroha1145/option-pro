/** ReactECharts 包装组件：按需 echarts 实例 + ResizeObserver 自适应 */
import { useEffect, useRef } from 'react';
import { echarts, type ChartOption, type EChartsInstance } from '@/lib/chart';
import { useIsMobile } from '@/hooks/use-mobile';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';

interface ReactEChartsProps {
  option: ChartOption;
  /** Read interaction state only when committing the option to the chart. */
  prepareOption?: (option: ChartOption) => ChartOption;
  className?: string;
  style?: React.CSSProperties;
  onClick?: (params: unknown) => void;
  /** 实例创建后回调一次；实例随组件卸载 dispose，外部持有需以此回调刷新引用 */
  onInit?: (chart: EChartsInstance) => void;
  ariaLabel?: string;
}

export default function ReactECharts({ option, prepareOption, className, style, onClick, onInit, ariaLabel }: ReactEChartsProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsInstance | null>(null);
  const onInitRef = useRef(onInit);
  const mobile = useIsMobile();
  const reduced = usePrefersReducedMotion();
  const initialMobile = useRef(mobile);

  useEffect(() => {
    onInitRef.current = onInit;
  }, [onInit]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, {
      renderer: 'canvas',
      // DPR 3/4 phones otherwise rasterize 9/16 pixels per CSS pixel on every pan.
      devicePixelRatio: initialMobile.current ? Math.min(window.devicePixelRatio || 1, 2) : undefined,
    });
    chartRef.current = chart;
    let width = chart.getWidth();
    let height = chart.getHeight();
    let frame = 0;
    const ro = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const nextWidth = Math.round(entry.contentRect.width);
      const nextHeight = Math.round(entry.contentRect.height);
      if (!nextWidth || !nextHeight || (nextWidth === width && nextHeight === height)) return;
      width = nextWidth;
      height = nextHeight;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!chart.isDisposed()) chart.resize({ width, height });
      });
    });
    ro.observe(ref.current);
    onInitRef.current?.(chart);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(frame);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const prepared = prepareOption ? prepareOption(option) : option;
    chart.setOption(mobile || reduced ? { ...prepared, animation: false } : prepared, { notMerge: true });
  }, [option, prepareOption, mobile, reduced]);

  // A new callback is not new chart data; rebinding must not reset zoom/series.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    chart.on('click', onClick);
    return () => { if (!chart.isDisposed()) chart.off('click', onClick); };
  }, [onClick]);

  return (
    <div
      ref={ref}
      className={className}
      style={{ width: '100%', height: '100%', ...style }}
      role="img"
      aria-label={ariaLabel}
    />
  );
}
