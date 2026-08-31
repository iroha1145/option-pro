/** 催化剂页共享小件：LED / 分类 chip / 分析状态 chip / 置信度·影响标注 / 代码 chip / 热度计 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { DUR_SECTION } from '@/lib/motion';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import type { NewsAnalysisStatus, NewsClassification } from './api';
import { t } from '../../i18n/core.ts';

/* ---------------- 状态 LED ---------------- */
type LedTone = 'up' | 'down' | 'warn' | 'brand' | 'ai' | 'muted';

const LED_BG: Record<LedTone, string> = {
  up: 'bg-up-600',
  down: 'bg-down-600',
  warn: 'bg-warn-600',
  brand: 'bg-brand-600',
  ai: 'bg-ai-600',
  muted: 'bg-ink-400',
};

export function Led({ tone, pulse = false, className }: { tone: LedTone; pulse?: boolean; className?: string }) {
  return (
    <span
      className={cn('inline-block size-2 shrink-0 rounded-full', LED_BG[tone], pulse && 'animate-led-pulse', className)}
      aria-hidden="true"
    />
  );
}

/* ---------------- 分类 chip（bullish 绿 / bearish 红 / neutral 灰） ---------------- */
const CLS_STYLE: Record<NewsClassification, { label: string; cls: string }> = {
  bullish: { label: t('利多'), cls: 'bg-up-50 text-up-700' },
  bearish: { label: t('利空'), cls: 'bg-down-50 text-down-700' },
  neutral: { label: t('中性'), cls: 'bg-paper-2 text-ink-500 border border-line' },
};

export function ClassificationChip({ classification, className }: { classification: NewsClassification; className?: string }) {
  const s = CLS_STYLE[classification];
  return (
    <motion.span
      initial={{ scale: 0.86, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: 'spring', stiffness: 520, damping: 32 }}
      className={cn('inline-flex items-center rounded-xs px-1.5 py-0.5 text-micro font-medium', s.cls, className)}
    >
      {s.label}
    </motion.span>
  );
}

/* ---------------- 分析状态 chip ---------------- */
const ANALYSIS_STYLE: Record<NewsAnalysisStatus, { label: string; cls: string; led?: LedTone; pulse?: boolean }> = {
  pending: { label: t('未分析'), cls: 'border border-line text-ink-400' },
  queued: { label: t('排队中'), cls: 'bg-warn-50 text-warn-600', led: 'warn', pulse: true },
  in_progress: { label: t('分析中'), cls: 'bg-brand-50 text-brand-600', led: 'brand', pulse: true },
  completed: { label: t('已分析'), cls: 'bg-ai-50 text-ai-600' },
  insufficient_context: { label: t('信息不足'), cls: 'bg-paper-2 text-ink-500 border border-line' },
  failed: { label: t('分析失败'), cls: 'bg-down-50 text-down-700' },
};

export function AnalysisStatusChip({ status, className }: { status: NewsAnalysisStatus; className?: string }) {
  const s = ANALYSIS_STYLE[status];
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-xs px-1.5 py-0.5 text-micro font-medium', s.cls, className)}>
      {s.led && <Led tone={s.led} pulse={s.pulse} className="size-1.5" />}
      {s.label}
    </span>
  );
}

/* ---------------- 置信度「N%」 ---------------- */
export function ConfidenceLabel({
  value,
  className,
  bare = false,
}: {
  value: number;
  className?: string;
  /**
   * 只给数字，不带自己的 ⓘ。
   *
   * 给同一行里已经有一个合并说明的调用方用（新闻流的逐条评估）。「置信不是胜率」
   * 这句声明不能丢，所以调用方必须自己给出，见 SCORE_HINTS.newsAssessment。
   */
  bare?: boolean;
}) {
  return (
    <span className={cn('font-mono text-micro text-ink-500 tnum', className)}>
      {(value * 100).toFixed(0)}%
      {!bare && <InfoHint hint={SCORE_HINTS.newsConfidence} size={11} className="ml-1" />}
    </span>
  );
}

/* ---------------- 影响分「N」 ---------------- */
export function ImpactValue({
  value,
  className,
  dash = '—',
  bare = false,
}: {
  value: number | null;
  className?: string;
  dash?: string;
  /**
   * 只给数字，不带自己的 ⓘ。
   *
   * 给同一行里已经有一个合并说明的调用方用（焦点周期的逐股评估、新闻流的逐条
   * 评估）。同一行挂两个 ⓘ，读者要读四样东西才看到两个数。说明本身不能丢，
   * 所以调用方必须自己给出，见 SCORE_HINTS.focusCycleAssessment / newsAssessment。
   */
  bare?: boolean;
}) {
  if (value === null || Number.isNaN(value)) {
    return <span className={cn('shrink-0 font-mono text-micro text-ink-300', className)}>{dash}</span>;
  }
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  const tone = value > 0.05 ? 'text-up-700' : value < -0.05 ? 'text-down-700' : 'text-ink-500';
  return (
    /* shrink-0 + nowrap：这是一个紧凑读数，「−4.00 ⓘ」不该被折行拆开。 */
    <span className={cn('shrink-0 whitespace-nowrap font-mono text-micro tnum', tone, className)}>
      {sign}
      {Math.abs(value).toFixed(2)}
      {!bare && <InfoHint hint={SCORE_HINTS.newsImpact} size={11} className="ml-1" />}
    </span>
  );
}

/* ---------------- 代码 chip(无 onClick 渲染为 span,可安全嵌入按钮内) ---------------- */
export function TickerChip({ ticker, onClick, className }: { ticker: string; onClick?: () => void; className?: string }) {
  const cls = cn(
    'inline-flex items-center rounded-xs bg-brand-50 px-1.5 py-0.5 font-mono text-[11px] leading-[14px] font-medium text-brand-700 transition-colors duration-fast',
    onClick ? 'hover:bg-brand-100 cursor-pointer' : 'cursor-default',
    className,
  );
  if (!onClick) return <span className={cls}>{ticker}</span>;
  return (
    <button type="button" onClick={onClick} className={cls}>
      {ticker}
    </button>
  );
}

/* ---------------- 热度计（5 段弧条，grow-bar  stagger） ---------------- */
export function HeatMeter({ level, heat, className }: { level: number; heat: number; className?: string }) {
  return (
    <span className={cn('inline-flex items-end gap-[3px]', className)} role="img" aria-label={t('热度 {heat}，{level} / 5 段', { heat, level })}>
      {Array.from({ length: 5 }, (_, i) => (
        <motion.span
          key={i}
          initial={{ scaleY: 0 }}
          animate={{ scaleY: 1 }}
          transition={{ duration: DUR_SECTION, ease: [0.16, 1, 0.3, 1], delay: 0.15 + i * 0.07 }}
          className={cn('w-[4px] origin-bottom rounded-[2px]', i < level ? (level >= 4 ? 'bg-down-600' : level === 3 ? 'bg-warn-600' : 'bg-brand-500') : 'bg-line')}
          style={{ height: 6 + i * 3 }}
        />
      ))}
      <span className="ml-1 font-mono text-micro text-ink-500 tnum">{heat}</span>
    </span>
  );
}

/* ---------------- 过期 chip ---------------- */
export function StaleChip() {
  return (
    <span className="inline-flex items-center rounded-xs border border-line bg-card-warm px-1.5 py-0.5 text-micro font-medium text-ink-400">
      {t('过期')}
    </span>
  );
}

/* ---------------- 区块标题 ---------------- */
export function SectionHead({ eyebrow, title, meta }: { eyebrow: string; title: string; meta?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-2">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2 className="mt-1 text-h2 text-ink-900">{title}</h2>
      </div>
      {meta}
    </div>
  );
}
