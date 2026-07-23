/** ReactECharts 包装组件：按需 echarts 实例 + ResizeObserver 自适应 */
import { useEffect, useRef } from 'react';
import { echarts, type ChartOption } from '@/lib/chart';

interface ReactEChartsProps {
  option: ChartOption;
  className?: string;
  style?: React.CSSProperties;
  onClick?: (params: unknown) => void;
  ariaLabel?: string;
}

export default function ReactECharts({ option, className, style, onClick, ariaLabel }: ReactEChartsProps) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
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
