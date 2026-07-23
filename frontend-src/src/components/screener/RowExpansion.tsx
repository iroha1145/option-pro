/**
 * 行展开分项明细（screener.md B2 行展开 · accordion 260ms）
 * ① 分项强度 breakdown（4 条 grow-bar + 分值 + 权重）
 * ② 迷你点阵面积图（§6-2 stipple）
 * ③ 操作（打开详情 / 相关突破事件）+ 信号 + 成交额
 */
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import type { ScreenerRow, Signal } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtCompact } from '@/lib/format';
import Icon from '@/components/icons';
import SignalChip from '@/components/shared/SignalChip';
import Sparkline from '@/components/charts/Sparkline';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import { SUBSCORE_META } from './types';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

export interface RowExpansionProps {
  row: ScreenerRow;
  weights: { trend: number; momentum: number; volume: number; volatility: number } | null;
  dollarVolume: number | null;
  signals: Signal[] | null;
  onOpenDetail: (ticker: string) => void;
}

export default function RowExpansion({ row, weights, dollarVolume, signals, onOpenDetail }: RowExpansionProps) {
  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-5 border-t border-line bg-card-warm/60 px-4 py-4 md:grid-cols-3">
      {/* ① 分项强度 breakdown */}
      <div>
        <p className="eyebrow">分项强度 · BREAKDOWN</p>
        <div className="mt-3 space-y-2.5">
          {SUBSCORE_META.map(({ key, label }, i) => {
            const v = row.subscores[key];
            return (
              <div key={key} className="grid grid-cols-[40px_1fr_64px] items-center gap-2.5">
                <span className="text-caption text-ink-500">{label}</span>
                <span className="h-1.5 overflow-hidden rounded-pill bg-line" role="presentation">
                  <motion.span
                    className={cn('block h-full origin-left rounded-pill', strengthBarClass(v))}
                    initial={{ scaleX: 0 }}
                    animate={{ scaleX: 1 }}
                    transition={{ duration: 0.7, ease: EASE_PAPER, delay: i * 0.05 }}
                    style={{ width: `${Math.max(2, Math.min(100, v))}%` }}
                  />
                </span>
                <span className="text-right font-mono text-caption text-ink-800 tnum">
                  {v}
                  {weights && <span className="ml-1 text-micro text-ink-300">×{weights[key]}%</span>}
                </span>
              </div>
            );
          })}
        </div>
        {weights && <p className="mt-2.5 text-micro text-ink-400">权重来自当前评分方法（右侧栏）</p>}
      </div>

      {/* ② 迷你点阵面积图 */}
      <div>
        <p className="eyebrow">近 5 日 · 点阵面积</p>
        <div className="mt-3 rounded-md border border-line bg-card p-3">
          <Sparkline data={row.sparkline} width={260} height={72} change={row.changePct} variant="area" className="w-full" />
          <div className="mt-2 flex items-center justify-between font-mono text-micro text-ink-400 tnum">
            <span>低 {Math.min(...row.sparkline).toFixed(1)}</span>
            <span>高 {Math.max(...row.sparkline).toFixed(1)}</span>
          </div>
        </div>
      </div>

      {/* ③ 操作 + 信号 + 成交额 */}
      <div>
        <p className="eyebrow">操作与信号</p>
        <div className="mt-3 flex flex-col items-start gap-2">
          <button
            onClick={() => onOpenDetail(row.ticker)}
            className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3 py-1.5 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          >
            <Icon name="arrow-up-right" size={13} />
            打开详情
          </button>
          <Link
            to="/breakouts"
            className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3 py-1.5 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          >
            <Icon name="radar" size={13} />
            相关突破事件
          </Link>
        </div>
        <div className="mt-3.5 border-t border-line pt-3">
          <p className="mb-1.5 text-micro text-ink-400">活跃信号</p>
          {signals === null ? (
            <div className="flex gap-1.5" aria-hidden="true">
              <span className="skeleton-shimmer h-5 w-14 rounded-xs" />
              <span className="skeleton-shimmer h-5 w-14 rounded-xs" />
            </div>
          ) : signals.length === 0 ? (
            <p className="text-caption text-ink-400">— 暂无信号</p>
          ) : (
            <span className="flex flex-wrap gap-1.5">
              {signals.map((s, i) => (
                <SignalChip key={i} type={s.type} label={s.label} />
              ))}
            </span>
          )}
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
          <span className="text-micro text-ink-400">美元成交额（推导）</span>
          <span className="font-mono text-data-m text-ink-800 tnum">
            {dollarVolume === null ? '—' : fmtCompact(dollarVolume)}
          </span>
        </div>
      </div>
    </div>
  );
}
