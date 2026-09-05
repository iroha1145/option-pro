/**
 * B4 移动端结果卡片流（<768px 表格转卡片）
 * 卡内：代码 + 强度大分 + 分项微条 + 涨跌 + 催化剂徽标；点按展开 accordion 明细。
 */
import SoftBadge from '@/components/shared/SoftBadge';
import { AnimatePresence, motion } from 'framer-motion';
import type { ScreenerRow } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtPrice } from '@/lib/format';
import Icon from '@/components/icons';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import MacroFitBadge from '@/components/shared/MacroFitBadge';
import { SCORE_HINTS } from '@/lib/scoreHints';
import RowExpansion from './RowExpansion';
import { CatalystBadge, SubscoreTicks } from './cells';
import {
  screenerStrengthPresentation,
  type CatalystSummary,
  type DetailCache,
  type RowSignalsState,
} from './types';
import { t } from '../../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

export interface ResultCardsProps {
  rows: ScreenerRow[];
  expanded: string | null;
  onToggle: (ticker: string) => void;
  catalysts: Record<string, CatalystSummary | undefined>;
  details: DetailCache;
  weights: { trend: number; momentum: number; volume: number; volatility: number } | null;
  signals: Record<string, RowSignalsState | undefined>;
  onOpenDetail: (ticker: string) => void;
  animKey: string;
  /** 当前页码：入场 stagger 只在第一页触发 */
  page?: number;
  /** 与桌面端可选列同一个开关 */
  showMacro?: boolean;
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
  page = 1,
  showMacro = false,
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
            initial={page === 1 ? { opacity: 0, y: 14 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: EASE_PAPER, delay: page === 1 ? Math.min(i * 0.03, 0.3) : 0, layout: { duration: 0.32, ease: EASE_PAPER } }}
            /* 可展开结果卡：hover 上浮 -3px/240ms（whileHover 避免入场后内联 transform 压掉 CSS 位移），阴影用 CSS */
            whileHover={{ y: -3, transition: { duration: 0.24, ease: 'easeOut' } }}
            className="card-surface overflow-hidden transition-shadow duration-240 ease-out hover:shadow-sh-2"
          >
            <button
              onClick={() => onToggle(r.ticker)}
              aria-expanded={isOpen}
              className="flex w-full flex-col p-4 text-left"
            >
              <span className="flex items-center gap-2.5">
                <TickerLogo ticker={r.ticker} size={32} />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-body-s font-semibold text-ink-800">{r.ticker}</span>
                    {r.sector && <SoftBadge className="max-w-[7.5rem]" title={r.sector}><span className="truncate">{r.sector}</span></SoftBadge>}
                  </span>
                  <span className="block truncate text-micro text-ink-400" title={r.name}>{r.name}</span>
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
                  <SoftBadge tone={strength.badgeTone} size="md" className="metric-value text-data-l tnum">
                    {/* 与表格列同口径的一位小数，避免 84 / 84.4 混排 */}
                    {r.strengthScore.toFixed(1)}
                  </SoftBadge>
                  <span className="ml-1.5 text-micro text-ink-400">
                    {t('强度分 ·')} {strength.band} {strength.label}
                    <InfoHint hint={SCORE_HINTS.strengthComposite} size={11} className="ml-1" />
                  </span>
                </span>
                <span className="pb-0.5 text-right">
                  <span className="block metric-value text-data-m text-ink-800 tnum">{fmtPrice(r.price)}</span>
                </span>
              </span>
              <span
                className="strength-track relative mt-2.5 h-1 w-full rounded-pill bg-paper"
                role="presentation"
                aria-hidden="true"
                data-strength-band={strength.band}
                data-strength-tone={strength.tone}
              >
                <span
                  className={cn('block h-full origin-left rounded-pill', strength.barClass)}
                  style={{ width: `${strengthWidth}%` }}
                />
                {/* 几何全部走 top/left，一点都不用 CSS transform：framer 为了动 scale
                    会写内联 transform，把 Tailwind 的 -translate-y-1/2 整个覆盖掉，
                    8px 的点就此丢掉垂直居中、稳定偏下 4px。scale 的 transform-origin
                    默认在中心，所以缩放不会再移动位置。 */}
                <motion.span
                  className={cn(
                    'absolute size-2 rounded-full border-2 border-card shadow-sh-1',
                    strength.barClass,
                  )}
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.26, ease: EASE_PAPER, delay: 0.72 + i * 0.03 }}
                  style={{ left: `calc(${strengthWidth}% - 4px)`, top: 'calc(50% - 4px)' }}
                  aria-hidden="true"
                />
              </span>
              <span className="mt-3 flex items-center justify-between border-t border-line pt-3">
                <SubscoreTicks row={r} />
                <span className="flex items-center gap-1.5">
                  {/* 开关是用户自己打开的，所以「无读数」也要显示出来。只在有分数时
                      才出现，等于让「列关着」和「这只票没读数」长得一样 —— 那正是这
                      整个字段想避免的含混。 */}
                  {showMacro && (
                    <MacroFitBadge score={r.macroFit} tailwind={r.macroTailwind} compact />
                  )}
                  <CatalystBadge summary={catalysts[r.ticker]} />
                </span>
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
