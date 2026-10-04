/**
 * MatrixLoader —— 4×4 点阵加载（transitions.dev 31-matrix-loader）。
 *
 * 比转圈安静：16 个 2px 点共用一条变色关键帧，靠每个点的延迟表形成扫描感。
 * 适合「整块内容还没来、但不值得一个显眼转圈」的位置（路由分包加载占位）。
 * 延迟表让点阵按列从左到右扫过。
 */
import type { CSSProperties } from 'react';
import { cn } from '@/lib/utils';

const CYCLE_MS = 1200; // 与 --matrix-cycle 同值

export default function MatrixLoader({ className }: { className?: string }) {
  return (
    <span className={cn('t-matrix shrink-0', className)} aria-hidden="true">
      {Array.from({ length: 16 }, (_, index) => (
        <i key={index} style={{ '--d': Math.round((index % 4) * (CYCLE_MS / 10)) } as CSSProperties} />
      ))}
    </span>
  );
}
