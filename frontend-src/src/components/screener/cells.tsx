/**
 * 结果行共享单元格：强度分条（grow-bar 错峰）/ 分项微条（§6-5 色阶）/ 催化剂 72h 徽标
 * 桌面表格与移动卡片流共用。
 */
import { motion } from 'framer-motion';
import type { ScreenerRow } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import Icon from '@/components/icons';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import { SUBSCORE_META, type CatalystSummary } from './types';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

/* ---------------- 强度分：Mono 15 600 + 64px 强度条（≥85 up-700 加粗） ---------------- */
export function ScoreCell({ score, index }: { score: number; index: number }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <span
        className={cn(
          'font-mono text-[15px] leading-[20px] font-semibold tnum',
          score >= 85 ? 'text-up-700' : 'text-ink-900',
        )}
      >
        {score}
      </span>
      <span className="h-1 w-16 overflow-hidden rounded-pill bg-line" role="presentation">
        <motion.span
          className={cn('block h-full origin-left rounded-pill', strengthBarClass(score))}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ duration: 0.7, ease: EASE_PAPER, delay: 0.15 + index * 0.03 }}
          style={{ width: `${Math.max(2, Math.min(100, score))}%` }}
        />
      </span>
    </span>
  );
}

/* ---------------- 分项微条：4 段（14×3px，§6-5 色阶，hover 毛玻璃 tooltip） ---------------- */
export function SubscoreTicks({ row }: { row: ScreenerRow }) {
  return (
    <span className="group relative inline-flex items-center gap-1" aria-label="分项强度">
      {SUBSCORE_META.map(({ key }) => {
        const v = row.subscores[key];
        return (
          <span
            key={key}
            className={cn('inline-block h-[3px] w-[14px] rounded-full', strengthBarClass(v))}
            aria-hidden="true"
          />
        );
      })}
      <span className="glass pointer-events-none absolute -top-2 left-1/2 z-20 hidden w-40 -translate-x-1/2 -translate-y-full rounded-md border border-line p-2.5 shadow-sh-2 group-hover:block">
        {SUBSCORE_META.map(({ key, label }) => (
          <span key={key} className="flex items-center justify-between py-0.5 text-micro">
            <span className="text-ink-500">{label}</span>
            <span className="font-mono text-ink-800 tnum">{row.subscores[key]}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

/* ---------------- 催化剂汇总（72h 窗口） ---------------- */
export function CatalystBadge({ summary }: { summary: CatalystSummary | undefined }) {
  if (!summary || !summary.loaded) {
    return <span className="skeleton-shimmer inline-block h-5 w-16 rounded-xs" aria-hidden="true" />;
  }
  if (summary.count === 0) {
    return <span className="font-mono text-caption text-ink-300 tnum">—</span>;
  }
  const net = summary.pos - summary.neg;
  const tone = net > 0 ? 'text-up-700 bg-up-50' : net < 0 ? 'text-down-700 bg-down-50' : 'text-ink-500 bg-card-warm';
  const label = net > 0 ? '利多' : net < 0 ? '利空' : '中性';
  return (
    <span className="group relative inline-flex">
      <span className={cn('inline-flex items-center gap-1 rounded-xs px-1.5 py-0.5 text-micro font-medium leading-[16px]', tone)}>
        <Icon name="bolt" size={11} />
        {label}
        <span className="font-mono tnum">{summary.count}</span>
      </span>
      <span className="glass pointer-events-none absolute -top-2 right-0 z-20 hidden w-60 -translate-y-full rounded-md border border-line p-3 shadow-sh-2 group-hover:block">
        <span className="block text-micro text-ink-500">
          72h 窗口 · 利多 <span className="font-mono text-up-700 tnum">{summary.pos}</span>
          {' · '}利空 <span className="font-mono text-down-700 tnum">{summary.neg}</span>
          {' · '}中性 <span className="font-mono tnum">{summary.neu}</span>
        </span>
        {summary.latestTitle && (
          <span className="mt-1.5 block truncate text-caption text-ink-800" title={summary.latestTitle}>
            {summary.latestTitle}
          </span>
        )}
        {summary.latestAt && (
          <span className="mt-0.5 block font-mono text-micro text-ink-400 tnum">{fmtRelative(summary.latestAt)}</span>
        )}
      </span>
    </span>
  );
}
