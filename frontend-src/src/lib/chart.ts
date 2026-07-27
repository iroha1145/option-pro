/**
 * ECharts 按需引入 + 全站统一工艺（design.md §6）
 * 发丝网格 / 毛玻璃 tooltip / 绘制动画 / 点阵面积 / 斜纹柱
 */
import * as echarts from 'echarts/core';
import { BarChart, CandlestickChart, LineChart, PieChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ComposeOption } from 'echarts/core';
import type { BarSeriesOption, CandlestickSeriesOption, LineSeriesOption, PieSeriesOption } from 'echarts/charts';
import type {
  DataZoomComponentOption,
  GridComponentOption,
  MarkAreaComponentOption,
  MarkLineComponentOption,
  MarkPointComponentOption,
  TooltipComponentOption,
} from 'echarts/components';

echarts.use([
  LineChart, BarChart, CandlestickChart, PieChart,
  GridComponent, TooltipComponent, DataZoomComponent, MarkLineComponent,
  MarkPointComponent, MarkAreaComponent,
  CanvasRenderer,
]);

export { echarts };

export type ChartOption = ComposeOption<
  | LineSeriesOption
  | BarSeriesOption
  | CandlestickSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | DataZoomComponentOption
  | MarkLineComponentOption
  | MarkPointComponentOption
  | MarkAreaComponentOption
>;

/** echarts.init 返回的实例类型（供交互层 convertFromPixel/zr 事件使用） */
export type EChartsInstance = ReturnType<typeof echarts.init>;

/* ---------- 调色（与 CSS 变量一致） ---------- */
export const CH = {
  ink400: '#8A94B0',
  ink300: '#B7BFD3',
  lineChart: '#EDF0F4', // v8.1 随纸面降温
  brand600: '#2E46E0',
  brand500: '#3B59F2',
  brand400: '#6B82FF',
  up600: '#0E9F6E',
  down600: '#E5484D',
  warn600: '#E8930C',
  ai600: '#0B7285', // v8.1 弃 AI 紫 → 青瓷 teal（与 CSS 变量一致）
} as const;

/* ---------- 通用配置 ---------- */
/* 数据是读的：入场/更新动画统一 300ms cubicOut，range 切换不重复播长动画 */
export const baseAnimation = {
  animationDuration: 300,
  animationDurationUpdate: 300,
  animationEasing: 'cubicOut' as const,
  animationEasingUpdate: 'cubicOut' as const,
};

export function baseGrid(overrides: Partial<GridComponentOption> = {}): GridComponentOption {
  return { left: 8, right: 8, top: 12, bottom: 8, containLabel: true, ...overrides };
}

export function categoryAxis(labels: string[]) {
  return {
    type: 'category' as const,
    data: labels,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: '"IBM Plex Mono", monospace' },
  };
}

export function valueAxis(overrides: Record<string, unknown> = {}) {
  return {
    type: 'value' as const,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: '"IBM Plex Mono", monospace' },
    splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
    ...overrides,
  };
}

/** 毛玻璃 tooltip（§6：overlay + blur(14px) + sh-2 + r-md） */
export function glassTooltip(overrides: Record<string, unknown> = {}) {
  return {
    trigger: 'axis' as const,
    backgroundColor: 'rgba(253,252,249,0.88)',
    /* tooltip 为 DOM 渲染：边框跟随 --line 令牌（线条细化后自动同步） */
    borderColor: 'var(--line)',
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: '#3D4A68', fontSize: 12, fontFamily: 'Inter, sans-serif' },
    extraCssText:
      'backdrop-filter:blur(14px) saturate(1.5);-webkit-backdrop-filter:blur(14px) saturate(1.5);' +
      'box-shadow:0 1px 2px rgba(13,22,38,.04),0 8px 24px -12px rgba(13,22,38,.12);border-radius:8px;',
    axisPointer: {
      type: 'line' as const,
      lineStyle: { color: CH.ink300, width: 1, type: [3, 3] as number[] },
    },
    ...overrides,
  };
}

/* ---------- 点阵面积图 pattern（§6-2） ---------- */
let stippleCanvas: HTMLCanvasElement | null = null;
export function stipplePattern(): HTMLCanvasElement | null {
  if (typeof document === 'undefined') return null;
  if (stippleCanvas) return stippleCanvas;
  const c = document.createElement('canvas');
  c.width = 6;
  c.height = 6;
  const ctx = c.getContext('2d');
  if (!ctx) return null;
  ctx.fillStyle = 'rgba(46,70,224,.20)';
  ctx.beginPath();
  ctx.arc(3, 3, 1.1, 0, Math.PI * 2);
  ctx.fill();
  stippleCanvas = c;
  return c;
}

/** 点阵面积（stipple area）series 片段 */
export function stippleAreaStyle(): LineSeriesOption['areaStyle'] {
  const pattern = stipplePattern();
  return pattern
    ? { color: { image: pattern, repeat: 'repeat' } as unknown as string, opacity: 1 }
    : { color: 'rgba(46,70,224,.10)' };
}

/* ---------- 斜纹柱 decal（§6-3） ---------- */
export function hatchDecal(color = CH.brand600) {
  return {
    symbol: 'rect',
    symbolSize: 1,
    rotation: Math.PI / 4,
    dashArrayX: [1, 0] as [number, number],
    dashArrayY: [1.2, 4] as [number, number],
    color,
    symbolKeepAspect: true,
  };
}

/* ---------- 涨跌热力色阶（§1.7 连续映射） ---------- */
const HEAT_STOPS: { pct: number; rgb: [number, number, number] }[] = [
  { pct: -3, rgb: [214, 53, 59] },
  { pct: -1.5, rgb: [240, 131, 127] },
  { pct: 0, rgb: [241, 239, 232] },
  { pct: 1.5, rgb: [124, 207, 169] },
  { pct: 3, rgb: [14, 159, 110] },
];

export function heatColor(pct: number): string {
  const clamped = Math.max(-3, Math.min(3, pct));
  for (let i = 0; i < HEAT_STOPS.length - 1; i++) {
    const a = HEAT_STOPS[i];
    const b = HEAT_STOPS[i + 1];
    if (clamped >= a.pct && clamped <= b.pct) {
      const t = (clamped - a.pct) / (b.pct - a.pct);
      const mix = a.rgb.map((v, k) => Math.round(v + (b.rgb[k] - v) * t));
      return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
    }
  }
  return 'rgb(241,239,232)';
}

/** 强度分色阶（§6-5） */
export function strengthColor(score: number): string {
  if (score >= 85) return CH.up600;
  if (score >= 70) return CH.brand600;
  if (score >= 50) return CH.brand400;
  return CH.ink300;
}

export const upColor = (change: number) => (change >= 0 ? CH.up600 : CH.down600);
