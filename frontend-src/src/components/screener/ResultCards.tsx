/**
 * B4 移动端结果卡片流（<768px 表格转卡片）
 * 卡内：代码 + 强度大分 + 分项微条 + 涨跌 + 催化剂徽标；点按展开 accordion 明细。
 */
import { AnimatePresence, motion } from 'framer-motion';
import type { ScreenerRow, Signal } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtPrice } from '@/lib/format';
import Icon from '@/components/icons';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import RowExpansion from './RowExpansion';
import { CatalystBadge, SubscoreTicks } from './cells';
import {
  screenerStrengthPresentation,
  type CatalystSummary,
  type DetailCache,
} from './types';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

export interface ResultCardsProps {
  rows: ScreenerRow[];
  expanded: string | null;
  onToggle: (ticker: string) => void;
  catalysts: Record<string, CatalystSummary | undefined>;
  details: DetailCache;
  weights: { trend: number; momentum: number; volume: number; volatility: number } | null;
  signals: Record<string, Signal[] | undefined>;
  onOpenDetail: (ticker: string) => void;
  animKey: string;
}

export default function ResultCards({
  rows,
  expanded,
  onToggle,
  catalysts,
  details,
  weights,
  signals,
  onOpenDetail,
  animKey,
}: ResultCardsProps) {
  return (
    <div className="grid grid-cols-1 gap-3" key={animKey}>
      {rows.map((r, i) => {
        const isOpen = expanded === r.ticker;
        const strength = screenerStrengthPresentation(r.strengthScore);
        const strengthWidth = Math.max(2, Math.min(100, r.strengthScore));
        return (
          <motion.div
            key={r.ticker}
            layout="position"
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.48, ease: EASE_PAPER, delay: Math.min(i * 0.045, 0.6), layout: { duration: 0.32, ease: EASE_PAPER } }}
            className="card-surface overflow-hidden"
          >
            <button
              onClick={() => onToggle(r.ticker)}
              aria-expanded={isOpen}
              className="flex w-full flex-col p-4 text-left"
            >
              <span className="flex items-center gap-2.5">
                <TickerLogo ticker={r.ticker} size={32} />
                <span className="min-w-0 flex-1">
                  <span className="block font-mono text-body-s font-semibold text-ink-800">{r.ticker}</span>
                  <span className="block truncate text-micro text-ink-400">{r.name} · {r.sector}</span>
                </span>
                <ChangeBadge value={r.changePct} size="sm" />
                <Icon
                  name="chevron-down"
                  size={14}
                  className={cn('text-ink-300 transition-transform duration-200', isOpen && 'rotate-180 text-brand-600')}
                />
              </span>
              <span className="mt-3 flex items-end justify-between gap-3">
                <span>
                  <span className={cn('font-mono text-data-xl tnum', strength.textClass)}>
                    {r.strengthScore}
                  </span>
                  <span className="ml-1.5 text-micro text-ink-400">
                    强度分 · {strength.band} {strength.label}
                  </span>
                </span>
                <span className="pb-0.5 text-right">
                  <span className="block font-mono text-data-m text-ink-800 tnum">{fmtPrice(r.price)}</span>
                </span>
              </span>
              <span
                className="relative mt-2.5 h-1 w-full rounded-pill bg-line"
                role="presentation"
                aria-hidden="true"
                data-strength-band={strength.band}
                data-strength-tone={strength.tone}
              >
                <motion.span
                  className={cn('block h-full origin-left rounded-pill', strength.barClass)}
                  initial={{ scaleX: 0 }}
                  animate={{ scaleX: 1 }}
                  transition={{ duration: 0.7, ease: EASE_PAPER, delay: 0.15 + i * 0.03 }}
                  style={{ width: `${strengthWidth}%` }}
                />
                <motion.span
                  className={cn(
                    'absolute top-1/2 size-2 -translate-y-1/2 rounded-full border-2 border-card shadow-sh-1',
                    strength.barClass,
                  )}
                  initial={{ opacity: 0, scale: 0.65 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.26, ease: EASE_PAPER, delay: 0.72 + i * 0.03 }}
                  style={{ left: `calc(${strengthWidth}% - 4px)` }}
                  aria-hidden="true"
                />
              </span>
              <span className="mt-3 flex items-center justify-between border-t border-line pt-3">
                <SubscoreTicks row={r} />
                <CatalystBadge summary={catalysts[r.ticker]} />
              </span>
            </button>
            <AnimatePresence>
              {isOpen && (
                <motion.div
                  key="exp"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.26, ease: EASE_PAPER }}
                  className="overflow-hidden"
                >
                  <RowExpansion
                    row={r}
                    weights={weights}
                    dollarVolume={details[r.ticker]?.dollarVolume ?? null}
                    signals={signals[r.ticker] ?? null}
                    onOpenDetail={onOpenDetail}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        );
      })}
    </div>
  );
}
