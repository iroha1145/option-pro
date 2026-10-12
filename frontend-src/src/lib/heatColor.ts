/**
 * 涨跌热力色阶（§1.7 连续映射）。
 *
 * 从 lib/chart.ts 抽出的纯数学实现：不依赖 echarts，让 /sectors 这类只用
 * 热力色、不用图表的页面不必背上整个 eps-chart chunk。chart.ts 原样 re-export，
 * 既有调用方接口不变。
 */
import { getColorMode } from './colorPreference.ts';
import { getAppearance } from './themePreference.ts';

/* 中点随 v8.1 冷灰统一：原 [241,239,232] 是暖米色（hue≈45°），平盘砖在冷纸面上发黄。 */
export function heatStops(span: number): { pct: number; rgb: [number, number, number] }[] {
  /* 夜间的半程色不能沿用日间的浅薄荷/浅珊瑚：色阶按周期放宽后大多数砖落在半程
     附近，浅色砖在深底上发亮刺眼。夜间从深灰中点往两端逐步加深、加饱和。 */
  const dark = getAppearance() === 'dark';
  const mid: [number, number, number] = dark ? [33, 36, 43] : [236, 239, 244];
  return [
    { pct: -span, rgb: [214, 53, 59] },
    { pct: -span / 2, rgb: dark ? [126, 46, 52] : [240, 131, 127] },
    { pct: 0, rgb: mid },
    { pct: span / 2, rgb: dark ? [24, 96, 74] : [124, 207, 169] },
    { pct: span, rgb: [14, 159, 110] },
  ];
}

/**
 * span 是色阶两端对应的涨跌幅（%）。默认 ±3% 是日内口径；板块矩阵展示的是
 * 1/3/6 个月收益，沿用 ±3% 几乎每块砖都会顶到最深色，由调用方按周期放宽。
 */
export function heatColor(pct: number, span = 3): string {
  /* 热力两端是「涨/跌」不是固定绿/红：亚洲习惯下翻转符号，色阶两端对调。 */
  const signed = getColorMode() === 'asian' ? -pct : pct;
  const clamped = Math.max(-span, Math.min(span, signed));
  const stops = heatStops(span);
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    if (clamped >= a.pct && clamped <= b.pct) {
      const t = (clamped - a.pct) / (b.pct - a.pct);
      const mix = a.rgb.map((v, k) => Math.round(v + (b.rgb[k] - v) * t));
      return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
    }
  }
  const mid = stops[2].rgb;
  return `rgb(${mid[0]},${mid[1]},${mid[2]})`;
}
