/**
 * B3 右侧栏卡片
 * 1. TierHistogram 强度剖面卡：S/A/B/C/D 五档 hatch 柱（命中=实心 brand-600，全市场参照=斜纹 ink-400），点击联动 B1 分档
 * 2. MethodCard 评分方法卡（可折叠 accordion）：四因子权重条 + 说明 + SourceNote
 */
import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { MarketStrength, StrengthProfile } from '@/api/types';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import HatchLegend from '@/components/shared/HatchLegend';
import SourceNote from '@/components/shared/SourceNote';
import { SUBSCORE_META, type Tier, type TierFilter } from './types';
import { t as __t } from '../../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];
const SPRING_POP = { type: 'spring', stiffness: 520, damping: 32 } as const;

const TIERS: Tier[] = ['S', 'A', 'B', 'C', 'D'];

/* ---------------- 强度剖面卡 ---------------- */
export function TierHistogram({
  hits,
  market,
  activeTier,
  onSelect,
}: {
  /** 当前结果各档命中数 */
  hits: Record<Tier, number> | null;
  /** 全市场参照（strengthApi.market 直方图） */
  market: MarketStrength | null;
  activeTier: TierFilter;
  onSelect: (t: TierFilter) => void;
}) {
  // 全市场 10 桶 → 五档；live 契约无直方图（histogram=[]）→ 参照如实隐藏，不编造
  const ref: Record<Tier, number> | null =
    market && market.histogram.length >= 10
      ? {
          S: market.histogram[9],
          A: market.histogram[8],
          B: market.histogram[7],
          C: market.histogram[6],
          D: market.histogram.slice(0, 6).reduce((s, n) => s + n, 0),
        }
      : null;
  const maxHit = Math.max(1, ...TIERS.map((t) => hits?.[t] ?? 0));
  const maxRef = Math.max(1, ...TIERS.map((t) => ref?.[t] ?? 0));

  return (
    <div className="card-surface p-5">
      <p className="eyebrow">{__t('强度剖面 · 分档命中')}</p>
      <div className="mt-4 flex h-28 items-end gap-2.5">
        {TIERS.map((t, i) => {
          const hit = hits?.[t] ?? 0;
          const refN = ref?.[t] ?? 0;
          const selectable = t !== 'D';
          const active = activeTier === t;
          return (
            <motion.button
              key={t}
              onClick={selectable ? () => onSelect(active ? 'all' : t) : undefined}
              disabled={!selectable}
              animate={{ scale: active ? 1.04 : 1 }}
              transition={SPRING_POP}
              aria-pressed={active}
              title={selectable ? __t('只看 {tier} 档', { tier: t }) : __t('D 档（<60）计入「全部」')}
              className={cn(
                'group relative flex h-full flex-1 flex-col items-center justify-end gap-1 rounded-t-[4px] border-b-2 pb-0.5 transition-colors duration-fast',
                active ? 'border-brand-600 bg-brand-50' : 'border-transparent hover:bg-paper-2',
                !selectable && 'cursor-default opacity-70',
              )}
            >
              <span className="font-mono text-[10px] leading-none text-ink-400 tnum">{hit}</span>
              {/* 全市场参照（斜纹）：live 无直方图时整列隐藏 */}
              {ref !== null && (
                <motion.span
                  className="w-full max-w-[26px] rounded-t-[3px] border border-ink-300/60"
                  initial={{ scaleY: 0 }}
                  whileInView={{ scaleY: 1 }}
                  viewport={{ once: true, amount: 0.3 }}
                  transition={{ duration: 0.7, ease: EASE_PAPER, delay: i * 0.05 }}
                  style={{
                    height: `${Math.max(4, (refN / maxRef) * 72)}px`,
                    transformOrigin: 'bottom',
                    backgroundImage: 'repeating-linear-gradient(45deg, rgba(138,148,176,.45) 0 1.2px, transparent 1.2px 4px)',
                  }}
                  aria-hidden="true"
                />
              )}
              {/* 命中（实心） */}
              <motion.span
                className={cn('-mt-1 w-full max-w-[26px] rounded-t-[3px]', active ? 'bg-brand-600' : 'bg-brand-600/85')}
                initial={{ scaleY: 0 }}
                whileInView={{ scaleY: 1 }}
                viewport={{ once: true, amount: 0.3 }}
                transition={{ duration: 0.7, ease: EASE_PAPER, delay: 0.08 + i * 0.05 }}
                style={{ height: `${Math.max(hit > 0 ? 5 : 2, (hit / maxHit) * 56)}px`, transformOrigin: 'bottom' }}
                aria-hidden="true"
              />
            </motion.button>
          );
        })}
      </div>
      <div className="mt-1.5 flex gap-2.5">
        {TIERS.map((t) => (
          <span key={t} className={cn('flex-1 text-center font-mono text-[10px] tnum', activeTier === t ? 'text-brand-600' : 'text-ink-400')}>
            {t}
          </span>
        ))}
      </div>
      {ref !== null ? (
        <HatchLegend className="mt-3.5" actual={__t("本次命中")} estimate={__t("全市场参照")} />
      ) : (
        /* 契约无全市场直方图：只标注命中分布，参照留空优于编造 */
        <p className="mt-3.5 text-micro text-ink-400">{__t('仅统计本次筛选命中的标的')}</p>
      )}
    </div>
  );
}

/* ---------------- 评分方法卡（可折叠） ---------------- */
export function MethodCard({ profile }: { profile: StrengthProfile | null }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="card-surface p-5">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between"
      >
        <span className="eyebrow">{__t('评分方法 ·')} {profile ? profile.name : __t('默认权重')}</span>
        <Icon name="chevron-down" size={14} className={cn('text-ink-400 transition-transform duration-200', open && 'rotate-180')} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.26, ease: EASE_PAPER }}
            className="overflow-hidden"
          >
            {profile && !profile.weights ? (
              /* live 契约 /strength/profiles 仅返回枚举，无权重明细：隐藏权重条，不编造默认 25% */
              <p className="mt-4 text-caption leading-[18px] text-ink-400">{__t('该档位暂无权重明细')}</p>
            ) : (
              <div className="mt-4 space-y-2.5">
                {SUBSCORE_META.map(({ key, label }, i) => {
                  const w = profile?.weights?.[key] ?? 25;
                  return (
                    <div key={key} className="grid grid-cols-[40px_1fr_40px] items-center gap-2.5">
                      <span className="text-caption text-ink-500">{label}</span>
                      <span className="h-1.5 overflow-hidden rounded-pill bg-line" role="presentation">
                        <motion.span
                          className="block h-full origin-left rounded-pill bg-brand-500"
                          initial={{ scaleX: 0 }}
                          whileInView={{ scaleX: 1 }}
                          viewport={{ once: true, amount: 0.4 }}
                          transition={{ duration: 0.7, ease: EASE_PAPER, delay: i * 0.05 }}
                          style={{ width: `${w}%` }}
                        />
                      </span>
                      <span className="text-right font-mono text-caption text-ink-800 tnum">{w}%</span>
                    </div>
                  );
                })}
              </div>
            )}
            <p className="mt-3 text-caption leading-[18px] text-ink-500">
              {profile?.description ||
                (profile && !profile.weights
                  ? __t('偏好档位决定评分时侧重哪些因子，最终强度分 0–100，≥85 为高强度区。')
                  : __t('最终强度分为四因子加权合成（0–100），≥85 为高强度区。'))}
            </p>
            <SourceNote className="mt-3" text={__t("权重取自当前选用的评分档位")} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
