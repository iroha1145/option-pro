/**
 * MatrixLoader —— 4×4 点阵加载（transitions.dev 31-matrix-loader）。
 *
 * 比转圈安静：16 个 2px 点共用一条变色关键帧，靠每个点的延迟表形成扫描感。
 * 适合「整块内容还没来、但不值得一个显眼转圈」的位置（路由分包加载占位）。
 * 变体只是延迟表：scan 按列扫过，orbit 沿外圈转、中心静止。
 */
import type { CSSProperties } from 'react';
import { cn } from '@/lib/utils';

const CYCLE_MS = 1200; // 与 --matrix-cycle 同值
const RING = [1, 2, 7, 11, 14, 13, 8, 4];

function delayFor(variant: 'scan' | 'orbit', index: number): number | null {
  if (variant === 'scan') return Math.round((index % 4) * (CYCLE_MS / 10));
  const k = RING.indexOf(index);
  return k === -1 ? null : Math.round(k * (CYCLE_MS / 8));
}

export default function MatrixLoader({
  variant = 'scan',
  className,
}: {
  variant?: 'scan' | 'orbit';
  className?: string;
}) {
  return (
    <span className={cn('t-matrix shrink-0', className)} data-variant={variant} aria-hidden="true">
      {Array.from({ length: 16 }, (_, index) => {
        const delay = delayFor(variant, index);
        return (
          <i
            key={index}
            style={delay === null ? { animation: 'none' } : ({ '--d': delay } as CSSProperties)}
          />
        );
      })}
    </span>
  );
}
