import { memo, useEffect, useState } from 'react';
import { fmtLocaleDateTime } from '@/lib/format';
import { CH, type EChartsInstance } from '@/lib/chart';
import { t } from '../../../i18n/core.ts';
import type { PanePlot } from '../chart-drawings/analysis/overlaysToMarks.ts';
import { formatIndicatorValue, indicatorCursorIndex, type IndicatorLayout } from './layout.ts';

/** Only this small component rerenders on crosshair motion, never KlineChart. */
function IndicatorReadouts({ chart, bars, range, panes, layout }: {
  chart: EChartsInstance | null;
  range: string;
  bars: { t: string; v: number }[];
  panes: PanePlot[];
  layout: IndicatorLayout;
}) {
  const [cursor, setCursor] = useState<{ bars: typeof bars; index: number } | null>(null);
  const selected = cursor?.bars === bars ? cursor.index : null;
  const index = selected ?? bars.length - 1;
  const bar = bars[index];
  useEffect(() => {
    if (!chart || chart.isDisposed()) return;
    const onPointer = (event: unknown) => {
      const index = indicatorCursorIndex(event, bars.length);
      setCursor(previous => index === null ? null
        : previous?.bars === bars && previous.index === index ? previous : { bars, index });
    };
    const reset = () => setCursor(null);
    chart.on('updateAxisPointer', onPointer);
    chart.on('datazoom', reset);
    chart.on('hideTip', reset);
    const zr = chart.getZr();
    zr.on('globalout', reset);
    return () => {
      if (chart.isDisposed()) return;
      chart.off('updateAxisPointer', onPointer);
      chart.off('datazoom', reset);
      chart.off('hideTip', reset);
      zr.off('globalout', reset);
    };
  }, [chart, bars]);
  const stamp = bar?.t ? (range === '1d' || range === '1w'
    ? bar.t.slice(0, 10) : fmtLocaleDateTime(bar.t, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })) : '—';
  const rows = [{ id: 'volume', label: t('成交量'), series: [{ name: t('量'), value: bar?.v }] },
    ...panes.map(pane => ({ id: pane.id, label: pane.label, series: pane.series.map(series => ({
      name: series.name, value: series.data[index],
    })) }))];
  return <div className="pointer-events-none absolute inset-0" data-indicator-readouts>
    {rows.map((row, rowIndex) => {
      const header = layout.headers[rowIndex];
      if (!header) return null;
      return <div key={row.id} data-indicator-header={row.id}
        className="absolute left-2 right-2 min-w-0 border-t border-line-chart pt-1.5 text-micro text-ink-400"
        style={{ top: header.top, height: header.height }}>
        <div className="flex min-w-0 items-center justify-between gap-3 leading-5">
          <span className="truncate font-medium text-ink-600" title={row.label}>{row.label}</span>
          <span className="shrink-0 font-mono text-[10px]" title={stamp}>
            {selected === null ? t('末根读数') : t('光标读数')} · {stamp}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono tnum leading-5">
          {row.series.map((series, seriesIndex) => <span key={series.name} className="inline-flex items-center gap-1.5 whitespace-nowrap"
            data-indicator-value={series.name} title={`${series.name}: ${series.value ?? '—'} · ${stamp}`}>
            <i className="h-1.5 w-1.5 shrink-0 rounded-full" aria-hidden style={{ backgroundColor: rowIndex === 0 ? CH.ink400 : seriesIndex === 0 ? CH.brand500 : seriesIndex === 1 ? CH.ai600 : CH.ink400 }} />
            <span>{series.name}</span><span className="text-ink-600">{formatIndicatorValue(series.value)}</span>
          </span>)}
        </div>
      </div>;
    })}
  </div>;
}
export default memo(IndicatorReadouts);
