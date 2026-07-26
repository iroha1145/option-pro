/**
 * B2 结果表（桌面 ≥768px）：紧凑 44px 行 · 发丝线 · 行展开 accordion（260ms）· 分页
 * 列：# / 代码 / 强度分 / 分项 / 价·涨跌 / 催化剂 72h / 成交额 / ▸
 * 行 stagger 30ms 仅第一页入场（翻页直接呈现）；排序切换 layout 重排 320ms；价格 tick-flash。
 */
import { Fragment } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { ScreenerRow } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtPrice } from '@/lib/format';
import Icon from '@/components/icons';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import MacroFitBadge from '@/components/shared/MacroFitBadge';
import { MACRO_SHADOW_HINT } from '@/lib/macroFit';
import { SCORE_HINTS, type ScoreHint } from '@/lib/scoreHints';
import RowExpansion from './RowExpansion';
import { CatalystBadge, ScoreCell, SubscoreTicks } from './cells';
import { tierOf, TIER_RANGE, type CatalystSummary, type DetailCache, type RowSignalsState } from './types';
import { t } from '../../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

export interface ResultTableProps {
  rows: ScreenerRow[];
  startIndex: number;
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
  expanded: string | null;
  onToggle: (ticker: string) => void;
  catalysts: Record<string, CatalystSummary | undefined>;
  details: DetailCache;
  flashes: Record<string, 'up' | 'down'>;
  weights: { trend: number; momentum: number; volume: number; volatility: number } | null;
  signals: Record<string, RowSignalsState | undefined>;
  onOpenDetail: (ticker: string) => void;
  /** 变化时重跑 stagger（扫描完成 / 翻页） */
  animKey: string;
  stale?: boolean;
  /** 可选列：宏观适配（影子字段，不参与排序） */
  showMacro?: boolean;
}

const HEADS: { label: string; align?: 'right' | 'center'; width?: string; hint?: ScoreHint }[] = [
  { label: '#', width: '36px' },
  { label: t('代码') },
  { label: t('强度分'), hint: SCORE_HINTS.strengthComposite },
  { label: t('分项') },
  { label: t('价 / 涨跌'), align: 'right' },
  { label: t('催化剂 · 72H') },
  { label: t('成交额'), align: 'right' },
  { label: '', width: '40px' },
];

/** 宏观列插在强度分之后：它读起来是强度分的背景，不是又一个价格字段。 */
const MACRO_HEAD_INDEX = 3;

function headsFor(showMacro: boolean) {
  if (!showMacro) return HEADS;
  return [
    ...HEADS.slice(0, MACRO_HEAD_INDEX),
    // 带上口径说明：一个 0–100 的分数放在强度分旁边，默认会被当成评分的一部分。
    { label: t('宏观适配'), hint: MACRO_SHADOW_HINT },
    ...HEADS.slice(MACRO_HEAD_INDEX),
  ];
}

export default function ResultTable({
  rows,
  startIndex,
  page,
  totalPages,
  onPageChange,
  expanded,
  onToggle,
  catalysts,
  details,
  flashes,
  weights,
  signals,
  onOpenDetail,
  animKey,
  stale = false,
  showMacro = false,
}: ResultTableProps) {
  const heads = headsFor(showMacro);
  return (
    <div className={cn('card-surface overflow-hidden', stale && 'opacity-60')}>
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-card-warm">
            {heads.map((h, i) => (
              <th
                key={i}
                style={h.width ? { width: h.width } : undefined}
                className={cn(
                  'border-b border-line px-3 py-2.5 text-eyebrow font-sans uppercase tracking-[0.14em] text-ink-400',
                  h.align === 'right' ? 'text-right' : h.align === 'center' ? 'text-center' : 'text-left',
                )}
              >
                {h.label}
                {h.hint && <InfoHint hint={h.hint} side="bottom" size={11} className="ml-1" />}
              </th>
            ))}
          </tr>
        </thead>
        <tbody key={animKey}>
          {rows.map((r, i) => {
            const isOpen = expanded === r.ticker;
            const dv = details[r.ticker]?.dollarVolume;
            const dvPending = details[r.ticker] === undefined;
            return (
              <Fragment key={r.ticker}>
                <motion.tr
                  layout="position"
                  initial={page === 1 ? { opacity: 0, y: 14 } : false}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, ease: EASE_PAPER, delay: page === 1 ? Math.min(i * 0.03, 0.3) : 0, layout: { duration: 0.32, ease: EASE_PAPER } }}
                  onClick={() => onToggle(r.ticker)}
                  className={cn(
                    'group h-11 cursor-pointer border-b border-line transition-colors duration-fast hover:bg-paper-2',
                    isOpen && 'bg-paper-2',
                  )}
                  aria-expanded={isOpen}
                >
                  {/* # */}
                  <td className="px-3 py-2 font-mono text-caption text-ink-400 tnum">{startIndex + i + 1}</td>
                  {/* 代码 */}
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-2.5">
                      <TickerLogo ticker={r.ticker} size={28} />
                      <span>
                        <span className="block font-mono text-body-s font-semibold text-ink-800">{r.ticker}</span>
                        <span className="block max-w-[150px] truncate text-micro text-ink-400">{r.name} · {r.sector}</span>
                      </span>
                    </span>
                  </td>
                  {/* 强度分 */}
                  <td className="px-3 py-2">
                    <ScoreCell score={r.strengthScore} index={i} />
                  </td>
                  {/* 宏观适配（可选列） */}
                  {showMacro && (
                    <td className="px-3 py-2">
                      <MacroFitBadge score={r.macroFit} tailwind={r.macroTailwind} compact />
                    </td>
                  )}
                  {/* 分项微条 */}
                  <td className="px-3 py-2">
                    <SubscoreTicks row={r} />
                  </td>
                  {/* 价 / 涨跌 */}
                  <td className="px-3 py-2 text-right">
                    <span
                      key={`${r.ticker}-${r.price}`}
                      className={cn(
                        'inline-block rounded-xs px-1 font-mono text-body-s text-ink-900 tnum',
                        flashes[r.ticker] === 'up' && 'animate-tick-flash-up',
                        flashes[r.ticker] === 'down' && 'animate-tick-flash-down',
                      )}
                    >
                      {fmtPrice(r.price)}
                    </span>
                    <span className="ml-1.5 align-middle">
                      <ChangeBadge value={r.changePct} size="sm" />
                    </span>
                  </td>
                  {/* 催化剂 72h */}
                  <td className="px-3 py-2">
                    <CatalystBadge summary={catalysts[r.ticker]} />
                  </td>
                  {/* 成交额 */}
                  <td className="px-3 py-2 text-right font-mono text-body-s text-ink-600 tnum">
                    {dvPending ? <span className="text-ink-300">…</span> : dv === null || dv === undefined ? '—' : fmtCompact(dv)}
                  </td>
                  {/* 展开 */}
                  <td className="px-3 py-2 text-right">
                    <span
                      className={cn(
                        'inline-flex size-6 items-center justify-center rounded-sm border border-line text-ink-400 transition-transform duration-200',
                        isOpen && 'rotate-180 border-brand-400 text-brand-600',
                      )}
                    >
                      <Icon name="chevron-down" size={13} />
                    </span>
                  </td>
                </motion.tr>
                {/* 行展开 accordion */}
                <AnimatePresence>
                  {isOpen && (
                    <tr key={`${r.ticker}-exp`}>
                      <td colSpan={heads.length} className="p-0">
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: 'auto', opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.26, ease: EASE_PAPER }}
                          className="overflow-hidden"
                        >
                          <RowExpansion
                            row={r}
                            weights={weights}
                            dollarVolume={dv ?? null}
                            signals={signals[r.ticker] ?? null}
                            onOpenDetail={onOpenDetail}
                          />
                        </motion.div>
                      </td>
                    </tr>
                  )}
                </AnimatePresence>
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {/* 分档角标说明 + 分页 */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-card-warm px-4 py-2.5">
        <p className="font-mono text-micro text-ink-400 tnum">
          {t('每页 20 · 分档')} {rows.length > 0 ? `${tierOf(rows[0].strengthScore)}–${tierOf(rows[rows.length - 1].strengthScore)}（${TIER_RANGE[tierOf(rows[0].strengthScore)]}）` : '—'}
        </p>
        <nav className="flex items-center gap-1" aria-label={t("分页")}>
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            aria-label={t("上一页")}
            className="flex size-7 items-center justify-center rounded-sm border border-line text-ink-500 transition-colors hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Icon name="chevron-right" size={13} className="rotate-180" />
          </button>
          {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
            <button
              key={p}
              onClick={() => onPageChange(p)}
              aria-current={p === page ? 'page' : undefined}
              className={cn(
                'flex size-7 items-center justify-center rounded-sm border font-mono text-caption tnum transition-colors duration-fast',
                p === page ? 'border-brand-600 bg-brand-600 text-white' : 'border-line text-ink-500 hover:border-brand-400 hover:text-brand-600',
              )}
            >
              {p}
            </button>
          ))}
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
            aria-label={t("下一页")}
            className="flex size-7 items-center justify-center rounded-sm border border-line text-ink-500 transition-colors hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Icon name="chevron-right" size={13} />
          </button>
        </nav>
      </div>
    </div>
  );
}
