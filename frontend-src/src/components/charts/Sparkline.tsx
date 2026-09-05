/**
 * 迷你 sparkline（design.md §6-4）
 * variant="line"：默认 48×20、1.5px 折线、涨跌着色、无轴；表格单元用。
 * variant="area"：卡片折线图——细线 + 极浅面积 + 小端点。
 * 小图保持真实观测点间的折线，和 Cloud Monitor 的清晰小图一致。
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

const Sparkline = memo(function Sparkline({ data, width = 48, height = 20, change, variant = 'line', stretch = false, className }: SparklineProps) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '');
  const isArea = variant === 'area';
  /* 面积版留够边距，末点的实心圆 + 白环（半径合计 ~3.8px）才不会被裁掉 */
  const pad = isArea ? 5 : 2;
  const { line, area, last } = useMemo(() => {
    if (data.length < 2) return { line: '', area: '', last: null as Pt | null };
    const pts = toPoints(data, width, height, pad);
    const path = straight(pts);
    return {
      line: path,
      area: `${path}L${pts[pts.length - 1][0].toFixed(1)},${height}L${pts[0][0].toFixed(1)},${height}Z`,
      last: pts[pts.length - 1],
    };
  }, [data, width, height, pad]);

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
            <stop offset="0" stopColor={color} stopOpacity="0.06" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
      )}
      {isArea && <path d={area} fill={`url(#fill-${id})`} stroke="none" />}
      <path
        d={line}
        pathLength={1}
        fill="none"
        stroke={lineColor}
        strokeWidth={isArea ? 1.65 : 1.4}
        strokeOpacity="0.82"
        vectorEffect="non-scaling-stroke"
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
        <circle cx={last[0]} cy={last[1]} r="1.9" fill={color} stroke="var(--card)" strokeWidth="1.25" />
      )}
    </svg>
  );
});

export default Sparkline;
