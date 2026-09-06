/** Presentation only: never changes indicator values, drawings, or chart gates. */
export interface IndicatorGrid {
  left: number;
  right: number;
  top: number;
  height: number;
  containLabel: false;
}
export interface IndicatorLayout {
  height: number;
  grids: IndicatorGrid[];
  headers: { top: number; height: number }[];
}
export type IndicatorView = 'single' | 'all';

/** Grow the component instead of dividing a fixed canvas among more indicators. */
export function indicatorLayout(minimumHeight: number, paneCount: number, narrow = false): IndicatorLayout {
  const base = Number.isFinite(minimumHeight) ? Math.max(240, minimumHeight) : 320;
  const count = Number.isFinite(paneCount) ? Math.max(0, Math.min(16, Math.floor(paneCount))) : 0;
  const grids: IndicatorGrid[] = [];
  const headers: IndicatorLayout['headers'] = [];
  let cursor = 16;
  const grid = (height: number) => {
    grids.push({ left: 8, right: 56, top: cursor, height, containLabel: false });
    cursor += height;
  };
  grid(Math.max(240, Math.round(base * 0.72)));
  const header = (height: number) => {
    cursor += 16;
    headers.push({ top: cursor, height });
    cursor += height;
  };
  header(narrow ? 60 : 44);
  grid(72);
  for (let index = 0; index < count; index++) {
    header(narrow ? 80 : 56);
    grid(108);
  }
  // One shared date axis lives below the last grid, never in the next header.
  return { height: cursor + 36, grids, headers };
}

export function selectIndicatorPanes<T extends { id: string }>(panes: T[], view: IndicatorView, selected: string): T[] {
  if (view === 'all') return panes;
  const pane = panes.find(row => row.id === selected) ?? panes[0];
  return pane ? [pane] : [];
}

/** Small values must not all become 0.00; large OBV values must fit the axis. */
export function formatIndicatorValue(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (value === 0) return '0';
  const abs = Math.abs(value);
  const sign = value < 0 ? '−' : '';
  const trim = (text: string) => text.replace(/\.?0+$/, '');
  if (abs >= 1e15 || abs < 1e-4) return `${sign}${abs.toExponential(2)}`;
  for (const [divisor, suffix] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']] as const) {
    if (abs >= divisor) return `${sign}${trim((abs / divisor).toFixed(2))}${suffix}`;
  }
  const digits = abs < 0.01 ? 6 : abs < 1 ? 4 : 2;
  return `${sign}${trim(abs.toFixed(digits))}`;
}

/** ECharts passes a category index for these axes. Never substitute a missing value. */
export function indicatorCursorIndex(payload: unknown, barCount: number): number | null {
  if (!payload || typeof payload !== 'object') return null;
  const axes = (payload as { axesInfo?: unknown }).axesInfo;
  if (!Array.isArray(axes)) return null;
  for (const axis of axes) {
    if (!axis || axis.axisDim !== 'x') continue;
    const value = axis.value;
    if (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < barCount) return value;
  }
  return null;
}
