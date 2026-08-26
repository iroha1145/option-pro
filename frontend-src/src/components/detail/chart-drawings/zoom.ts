/** Recorded inside dataZoom window. Survives setOption / color / layer rebuilds. */
import type { ChartRange } from './types.ts';

export interface ZoomWindow {
  start: number;
  end: number;
  pinnedEnd: boolean;
}

const DEFAULT_ZOOM_BARS: Partial<Record<ChartRange, number>> = { '1d': 126, '1w': 104 };

export function insideZoom(
  range: ChartRange,
  barCount: number,
  axes: number[],
  saved?: ZoomWindow | null,
) {
  const window = DEFAULT_ZOOM_BARS[range];
  if (!window || barCount <= window) return undefined;
  const last = barCount - 1;
  let startValue = barCount - window;
  let endValue = last;
  if (saved) {
    const span = Math.max(1, saved.end - saved.start);
    endValue = saved.pinnedEnd ? last : Math.min(last, Math.max(1, saved.end));
    startValue = Math.max(0, Math.min(endValue - 1, saved.pinnedEnd ? endValue - span : saved.start));
  }
  return [
    {
      type: 'inside' as const,
      xAxisIndex: axes,
      startValue,
      endValue,
      minValueSpan: 15,
    },
  ];
}

export function zoomFromOption(
  option: {
    dataZoom?: Array<{ startValue?: unknown; endValue?: unknown; start?: unknown; end?: unknown }>;
  } | null | undefined,
  barCount: number,
): ZoomWindow | null {
  const row = option?.dataZoom?.[0];
  const start = Number(row?.startValue ?? row?.start);
  const end = Number(row?.endValue ?? row?.end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return {
    start: Math.max(0, Math.round(start)),
    end: Math.round(end),
    pinnedEnd: Math.round(end) >= barCount - 1,
  };
}
