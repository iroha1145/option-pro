/** ReactECharts 包装组件：按需 echarts 实例 + ResizeObserver 自适应 */
import { useEffect, useRef } from 'react';
import { echarts, type ChartOption, type EChartsInstance } from '@/lib/chart';

interface ReactEChartsProps {
  option: ChartOption;
  className?: string;
  style?: React.CSSProperties;
  onClick?: (params: unknown) => void;
  /** 实例创建后回调一次；实例随组件卸载 dispose，外部持有需以此回调刷新引用 */
  onInit?: (chart: EChartsInstance) => void;
  ariaLabel?: string;
}

export default function ReactECharts({ option, className, style, onClick, onInit, ariaLabel }: ReactEChartsProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsInstance | null>(null);
  const onInitRef = useRef(onInit);

  useEffect(() => {
    onInitRef.current = onInit;
  }, [onInit]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => {
      if (chart.isDisposed()) return;
      chart.resize();
    });
    ro.observe(ref.current);
    onInitRef.current?.(chart);
    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setOption(option, { notMerge: true });
    if (onClick) {
      chart.off('click');
      chart.on('click', onClick);
    }
  }, [option, onClick]);

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
