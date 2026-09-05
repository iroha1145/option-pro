/**
 * ECharts 按需引入 + 全站统一工艺（design.md §6）
 * 发丝网格 / 毛玻璃 tooltip / 绘制动画 / 点阵面积 / 斜纹柱
 */
import * as echarts from 'echarts/core';
import { BarChart, CandlestickChart, CustomChart, LineChart, PieChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ComposeOption } from 'echarts/core';
import type { BarSeriesOption, CandlestickSeriesOption, CustomSeriesOption, LineSeriesOption, PieSeriesOption } from 'echarts/charts';
import type {
  DataZoomComponentOption,
  GraphicComponentOption,
  GridComponentOption,
  MarkAreaComponentOption,
  MarkLineComponentOption,
  MarkPointComponentOption,
  TooltipComponentOption,
} from 'echarts/components';
import { CHART_MONO_FONT, CHART_TEXT_FONT } from './chartFonts.ts';
import { directionColors, getColorMode } from './colorPreference.ts';

echarts.use([
  LineChart, BarChart, CandlestickChart, PieChart, CustomChart,
  GridComponent, TooltipComponent, DataZoomComponent, MarkLineComponent,
  MarkPointComponent, MarkAreaComponent, GraphicComponent,
  CanvasRenderer,
]);

export { echarts };

export type ChartOption = ComposeOption<
  | LineSeriesOption
  | BarSeriesOption
  | CandlestickSeriesOption
  | PieSeriesOption
  | CustomSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | DataZoomComponentOption
  | MarkLineComponentOption
  | MarkPointComponentOption
  | MarkAreaComponentOption
  | GraphicComponentOption
>;

/** echarts.init 返回的实例类型（供交互层 convertFromPixel/zr 事件使用） */
export type EChartsInstance = ReturnType<typeof echarts.init>;

/* ---------- 调色（与 CSS 变量一致；up/down 随涨跌色彩习惯） ---------- */
export const CH = {
  ink400: '#626F8B',
  ink300: '#B7BFD3',
  lineChart: '#EDF0F4', // v8.1 随纸面降温
  brand600: '#2E46E0',
  brand500: '#3B59F2',
  brand400: '#6B82FF',
  get up600() {
    return directionColors().up600;
  },
  get down600() {
    return directionColors().down600;
  },
  warn600: '#E8930C',
  ai600: '#0B7285', // v8.1 弃 AI 紫 → 青瓷 teal（与 CSS 变量一致）
};

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

export { CHART_MONO_FONT };

export function categoryAxis(labels: string[]) {
  return {
    type: 'category' as const,
    data: labels,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: CHART_TEXT_FONT },
  };
}

export function valueAxis(overrides: Record<string, unknown> = {}) {
  return {
    type: 'value' as const,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: CHART_TEXT_FONT },
    splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
    ...overrides,
  };
}

/** 保留现有调用接口，小窗使用白底、细边和克制阴影。 */
export function glassTooltip(overrides: Record<string, unknown> = {}) {
  return {
    trigger: 'axis' as const,
    transitionDuration: 0,
    className: 'cloud-chart-tooltip',
    backgroundColor: '#FFFFFF',
    /* tooltip 为 DOM 渲染：边框跟随 --line 令牌（线条细化后自动同步） */
    borderColor: 'var(--line)',
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: '#3D4A68', fontSize: 12, fontFamily: CHART_TEXT_FONT },
    extraCssText:
      'box-shadow:var(--popover-shadow);border-radius:9px;font-variant-numeric:tabular-nums;transition:opacity 140ms ease-out;',
    axisPointer: {
      type: 'line' as const,
      lineStyle: { color: CH.ink300, width: 1, type: [3, 3] as number[] },
    },
    ...overrides,
  };
}

/* ---------- Insight Cards 折线工艺（beautifului.dev · Insight Cards） ----------
 * 只用于折线图卡片：2.25px 圆头线、极淡渐变面积、实心细游标（26% 墨色）、
 * 顶部锚定的白底胶囊 tooltip（发丝边 + sh-2 + r-8 + 圆点数值行）。
 * K线/柱状等其他图仍走 glassTooltip 毛玻璃工艺，互不串味。 */

/** hex → rgba（insight 渐变面积用；CH 调色全部是 6 位 hex） */
export function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/** Insight 内嵌图台：暖白纸面 + 发丝边，把绘图区收进一层里（外边距各站点自给） */
export const INSIGHT_FRAME = 'insight-frame rounded-lg border border-line bg-card-warm p-2';

/** Insight 滑翔平滑度（贝塞尔近似 Catmull-Rom）：只给愿意被平滑的曲线显式传入 */
export const INSIGHT_SMOOTH = 0.45;

const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

/* 一层深合并：lineStyle/itemStyle 这类嵌套工艺基底不该被调用点整段重抄，
   否则以后调工艺会静默绕过每个抄过的 series。调用点只写 delta。 */
function mergeOneLevel(
  base: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    const prev = out[key];
    out[key] = isPlainObject(prev) && isPlainObject(value) ? { ...prev, ...value } : value;
  }
  return out;
}

/** Insight 折线 series 片段：overrides 只需给 delta（如 lineStyle: { width: 1.5 }） */
export function insightLine(color: string, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return mergeOneLevel(
    {
      type: 'line' as const,
      showSymbol: false,
      /* 分析型曲线默认不平滑：平滑会在真实观测点之间伪造出没测过的读数。
         要 Insight 的滑翔手感，调用点显式传 smooth: INSIGHT_SMOOTH。 */
      smooth: false,
      lineStyle: { color, width: 1.8, cap: 'round', join: 'round' },
      itemStyle: { color },
    },
    overrides,
  );
}

/**
 * Insight 渐变面积：同色 12% 渐隐，浓的一头永远贴着数据线。
 *
 * areaStyle 的基线是 0，负值段的多边形长在 0 轴「下方」——固定的上→下渐变
 * 会把浓色压在 0 轴、把透明留给曲线，正好反过来（净空是 CTA 的常规状态，
 * 不是边角情况）。所以按 0 在多边形包围盒里的位置切一刀：正负两瓣各自从
 * 曲线端起浓、到 0 轴收干。values 用来定位那一刀，必须是本条 series 的数据。
 */
export function insightAreaStyle(
  color: string,
  values: (number | null | undefined)[],
): LineSeriesOption['areaStyle'] {
  const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  const top = Math.max(0, ...nums);
  const bottom = Math.min(0, ...nums);
  const strong = withAlpha(color, 0.08);
  const clear = withAlpha(color, 0);
  /* 全正/全负时 0 落在包围盒边界，下面的去重会把它退化成单向渐变 */
  const zero = top === bottom ? 1 : top / (top - bottom);
  const stops = [
    { offset: 0, color: top > 0 ? strong : clear },
    { offset: zero, color: clear },
    { offset: 1, color: bottom < 0 ? strong : clear },
  ].filter((stop, i, all) => i === 0 || stop.offset !== all[i - 1].offset);
  return { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, stops) };
}

/**
 * Insight 端点圆点：最后一个数据点上的实心点 + 白环（参考图的签名之一）。
 * 它标的是「读数落在这里」，所以只给最后一点，不是每点都发 symbol。
 * 返回整个 markPoint，调用点若已有 markPoint 需自行合并（一级合并会整段覆盖）。
 */
export function insightEndpointMark(color: string, index: number, value: number): Record<string, unknown> {
  return {
    symbol: 'circle',
    symbolSize: 6,
    silent: true,
    label: { show: false },
    itemStyle: { color, borderColor: '#FFFFFF', borderWidth: 1.5 },
    data: [{ coord: [index, value] }],
  };
}

/**
 * Insight 参考位：同色淡虚线（阈值/末值参照）。返回的是 markLine 的**一条数据**，
 * 好让调用点和自己已有的参考线（如 0 轴）并存，而不是被整段替换。
 */
export function insightReferenceMark(color: string, yValue: number): Record<string, unknown> {
  return {
    yAxis: yValue,
    lineStyle: { color: withAlpha(color, 0.45), width: 1, type: [5, 5] as number[] },
    label: { show: false },
  };
}

/**
 * Insight 左缘淡出：线色改为横向渐变，最左侧收到透明。
 *
 * 它表达的是「窗口从这里开始，之前的数据没画」，不是「这段数据不确定」——
 * 只给窗口边界本身就是任意截断的曲线用（如固定回看 N 日），坐标轴两端即为
 * 数据全集的图别用，否则会读成对早期读数的信心衰减。
 */
export function insightLineFade(color: string): unknown {
  return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
    { offset: 0, color: withAlpha(color, 0) },
    { offset: 0.14, color },
    { offset: 1, color },
  ]);
}

/** 自定义图表提示返回 HTML，文本必须转义后再进入模板。 */
export function escapeTooltipText(value: unknown): string {
  return echarts.format.encodeHTML(value == null ? '' : String(value));
}

/** Insight tooltip 数值行：8px 圆点 + 可选标签 + 加粗数值 */
export function insightDotRow(color: string, label: string, value: string): string {
  // Tooltip formatters return raw HTML, unlike React text children. Keep
  // snapshot labels/values as text and accept only literal palette colors.
  const safeColor = /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(color)
    ? color : CH.brand500;
  return (
    `<span style="display:inline-flex;align-items:center;gap:6px">` +
    `<span style="width:8px;height:8px;border-radius:50%;background:${safeColor};flex:0 0 8px"></span>` +
    (label ? `<span style="color:${CH.ink400}">${escapeTooltipText(label)}</span>` : '') +
    `<span style="font-weight:600">${escapeTooltipText(value)}</span></span>`
  );
}

/**
 * Insight tooltip 正文：日期头 + 数值行 + 可选脚注，排版只此一处。
 * 行距交给 flex 的 column-gap——早先用 12px 占位 span 拼接，换行时那个
 * span 会落在行首，读成一段幽灵缩进。
 */
export function insightTooltipBody(header: string, rows: string[], meta = ''): string {
  return (
    `<div style="color:${CH.ink400};font-size:11px;margin-bottom:4px">${escapeTooltipText(header)}</div>` +
    `<div style="display:flex;flex-wrap:wrap;gap:4px 12px">${rows.join('')}</div>` +
    (meta ? `<div style="color:${CH.ink400};font-size:11px;margin-top:4px">${escapeTooltipText(meta)}</div>` : '')
  );
}

/** Insight 白底胶囊 tooltip：顶部 6px 锚定跟随游标 x，实心 1px 墨游标 */
export function insightTooltip(overrides: Record<string, unknown> = {}) {
  return {
    trigger: 'axis' as const,
    transitionDuration: 0,
    className: 'cloud-chart-tooltip',
    backgroundColor: '#FFFFFF',
    borderColor: 'var(--line)',
    borderWidth: 1,
    padding: [9, 12],
    textStyle: { color: '#2A3550', fontSize: 12, fontFamily: CHART_TEXT_FONT },
    extraCssText:
      'box-shadow:var(--popover-shadow);border-radius:9px;transition:opacity 140ms ease-out;' +
      'font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1;',
    confine: true,
    position: (
      point: number[],
      _params: unknown,
      _dom: unknown,
      _rect: unknown,
      size: { contentSize: number[]; viewSize: number[] },
    ) => [
      Math.min(Math.max(point[0] - size.contentSize[0] / 2, 0), size.viewSize[0] - size.contentSize[0]),
      6,
    ],
    axisPointer: {
      type: 'line' as const,
      lineStyle: { color: 'rgba(13,22,38,.26)', width: 1 },
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
  /* 热力两端是「涨/跌」不是固定绿/红：亚洲习惯下翻转符号，色阶两端对调。 */
  const signed = getColorMode() === 'asian' ? -pct : pct;
  const clamped = Math.max(-3, Math.min(3, signed));
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
