/**
 * 迷你 sparkline（design.md §6-4）
 * 默认 48×20、1.5px 线、涨跌着色、无轴；首绘 draw-line 700ms。
 * variant="area" 时点阵面积工艺（§6-2），用于卡片模式。
 */
import { memo, useId, useMemo } from 'react';

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  change: number;         // 决定着色（涨绿跌红）
  variant?: 'line' | 'area';
  className?: string;
}

function buildPath(data: number[], w: number, h: number, pad: number): { line: string; area: string } {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const step = (w - pad * 2) / (data.length - 1);
  const pts = data.map((v, i) => [pad + i * step, h - pad - ((v - min) / span) * (h - pad * 2)] as const);
  const line = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join('');
  const area = `${line}L${pts[pts.length - 1][0].toFixed(1)},${h}L${pts[0][0].toFixed(1)},${h}Z`;
  return { line, area };
}

const Sparkline = memo(function Sparkline({ data, width = 48, height = 20, change, variant = 'line', className }: SparklineProps) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '');
  const { line, area } = useMemo(
    () => (data.length >= 2 ? buildPath(data, width, height, variant === 'area' ? 3 : 2) : { line: '', area: '' }),
    [data, width, height, variant],
  );
  const color = change >= 0 ? 'var(--up-600)' : 'var(--down-600)';
  const lineColor = variant === 'area' ? 'var(--brand-500)' : color;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      aria-hidden="true"
      role="presentation"
    >
      <defs>
        <pattern id={`stip-${id}`} width="6" height="6" patternUnits="userSpaceOnUse">
          <circle cx="3" cy="3" r="1.1" fill="rgba(46,70,224,.20)" />
        </pattern>
      </defs>
      {variant === 'area' && <path d={area} fill={`url(#stip-${id})`} stroke="none" />}
      <path
        d={line}
        fill="none"
        stroke={lineColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="spark-draw"
        /* 首绘 500ms（覆盖 index.css 的 700ms；reduced-motion 下 CSS !important 仍然生效） */
        style={{ strokeDasharray: 300, strokeDashoffset: 300, animationDuration: '500ms' }}
      />
    </svg>
  );
});

export default Sparkline;
