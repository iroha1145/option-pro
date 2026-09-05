/**
 * 迷你 sparkline（design.md §6-4）
 * variant="line"：默认 48×20、1.5px 折线、涨跌着色、无轴；表格单元用。
 * variant="area"：卡片折线图，走 Insight Cards 工艺（beautifului.dev）——
 *   平滑曲线 + 同色渐隐面积 + 末点实心圆白环 + 左缘淡出。
 * 两者都保留首绘 draw-line。
 */
import { memo, useId, useMemo } from 'react';

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  change: number;         // 决定着色（涨绿跌红）
  variant?: 'line' | 'area';
  stretch?: boolean;
  className?: string;
}

type Pt = readonly [number, number];

function toPoints(data: number[], w: number, h: number, pad: number): Pt[] {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const step = (w - pad * 2) / (data.length - 1);
  return data.map((v, i) => [pad + i * step, h - pad - ((v - min) / span) * (h - pad * 2)] as const);
}

const straight = (pts: Pt[]): string =>
  pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join('');

/**
 * Catmull-Rom → 三次贝塞尔，控制点的 y 钳在本段两端之间。
 * 不钳会过冲：曲线会拱到比真实读数更高/更低的位置，等于在两个观测点之间
 * 画出没测过的极值——迷你图也不该撒这种谎（同 insightLine 默认不平滑的理由）。
 */
function smoothPath(pts: Pt[], tension = 0.5): string {
  if (pts.length < 3) return straight(pts);
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const lo = Math.min(p1[1], p2[1]);
    const hi = Math.max(p1[1], p2[1]);
    const clamp = (v: number) => Math.min(hi, Math.max(lo, v));
    const c1x = p1[0] + ((p2[0] - p0[0]) / 6) * tension;
    const c1y = clamp(p1[1] + ((p2[1] - p0[1]) / 6) * tension);
    const c2x = p2[0] - ((p3[0] - p1[0]) / 6) * tension;
    const c2y = clamp(p2[1] - ((p3[1] - p1[1]) / 6) * tension);
    d += `C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

const Sparkline = memo(function Sparkline({ data, width = 48, height = 20, change, variant = 'line', stretch = false, className }: SparklineProps) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '');
  const isArea = variant === 'area';
  /* 面积版留够边距，末点的实心圆 + 白环（半径合计 ~3.8px）才不会被裁掉 */
  const pad = isArea ? 5 : 2;
  const { line, area, last } = useMemo(() => {
    if (data.length < 2) return { line: '', area: '', last: null as Pt | null };
    const pts = toPoints(data, width, height, pad);
    const path = isArea ? smoothPath(pts) : straight(pts);
    return {
      line: path,
      area: `${path}L${pts[pts.length - 1][0].toFixed(1)},${height}L${pts[0][0].toFixed(1)},${height}Z`,
      last: pts[pts.length - 1],
    };
  }, [data, width, height, pad, isArea]);

  /* 三态与同屏 ChangeBadge 一致：持平不是上涨。
     卡片图以前不分涨跌一律刷品牌蓝，同一张卡上「−2.51%」是红的、曲线却是蓝的，
     等于把方向这条最该一眼看出的信息从图里抽走了。现在线/面积/端点同色。 */
  const color = change > 0 ? 'var(--up-600)' : change < 0 ? 'var(--down-600)' : 'var(--ink-400)';
  const lineColor = color;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio={stretch ? 'none' : undefined}
      className={className}
      aria-hidden="true"
      role="presentation"
    >
      {isArea && (
        <defs>
          {/* 同色渐隐：浓的一头贴着曲线，到底收干 */}
          <linearGradient id={`fill-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.14" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
          {/* 左缘淡出：卡片只截了最近一段，起点是窗口边界不是数据开端 */}
          <linearGradient id={`stroke-${id}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor={color} stopOpacity="0" />
            <stop offset="0.16" stopColor={color} stopOpacity="1" />
            <stop offset="1" stopColor={color} stopOpacity="1" />
          </linearGradient>
        </defs>
      )}
      {isArea && <path d={area} fill={`url(#fill-${id})`} stroke="none" />}
      <path
        d={line}
        pathLength={1}
        fill="none"
        stroke={isArea ? `url(#stroke-${id})` : lineColor}
        strokeWidth={isArea ? 2.25 : 1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="spark-draw"
        /* 首绘 500ms（覆盖 index.css 的 700ms；reduced-motion 下 CSS !important 仍然生效） */
        // Normalize the dash to the whole path; a fixed 300px dash leaves gaps
        // when a wider daily curve is longer than 300px.
        style={{ strokeDasharray: 1, strokeDashoffset: 1, animationDuration: '500ms' }}
      />
      {isArea && last && (
        /* 末点读数落点：实心圆 + 白环，和大图的 insightEndpointMark 同一套语言 */
        <circle cx={last[0]} cy={last[1]} r="2.75" fill={color} stroke="var(--card)" strokeWidth="1.5" />
      )}
    </svg>
  );
});

export default Sparkline;
