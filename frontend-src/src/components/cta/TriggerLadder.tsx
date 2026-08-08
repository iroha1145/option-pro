/**
 * 触发阶梯（/cta 主区）：纵向价格轴上的模型触发位可视化。
 *
 * - 上=上方触发区、中=现价标记、下=下方触发区；每个区间一块：预计仓位变化
 *   为正用 up 系、为负用 down 系，块宽≈该触发位权重占比（weight_share 归一）。
 * - 触发位全部「需收盘确认」：盘中穿越的区间挂脉动圆点，详情里再给
 *   「盘中已穿越 · 待收盘确认」暂定章，不构成正式触发。
 * - 点击区间块展开详情（标签/类型/模型/权重/估算 Δ 与趋势/波动率拆分）。
 */
import { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import InfoHint from '@/components/shared/InfoHint';
import { CTA_HINTS } from '@/lib/ctaHints';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { CtaInstrumentEstimate, CtaTriggerZone } from '@/api/types';
import { t } from '../../i18n/core.ts';
import { MODEL_SHORT, ZONE_KIND, ZONE_LABELS, signed } from './ctaMeta';

/** 脉动圆点（盘中穿越标记）：framer 全局遵守系统 reducedMotion */
function PulseDot({ className }: { className?: string }) {
  return (
    <motion.span
      aria-hidden="true"
      className={cn('inline-block size-1.5 rounded-full bg-warn-600', className)}
      animate={{ opacity: [1, 0.3, 1] }}
      transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
    />
  );
}

export default function TriggerLadder({ row }: { row: CtaInstrumentEstimate }) {
  const above = useMemo(() => [...(row.trigger_levels?.above ?? [])].sort((a, b) => a.price - b.price), [row]);
  const below = useMemo(() => [...(row.trigger_levels?.below ?? [])].sort((a, b) => b.price - a.price), [row]);
  const zones = useMemo(() => [...above, ...below], [above, below]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const axis = useMemo(() => {
    if (!zones.length || row.reference_price === null) return null;
    const ref = row.reference_price;
    let min = ref;
    let max = ref;
    for (const z of zones) {
      min = Math.min(min, z.price_low);
      max = Math.max(max, z.price_high);
    }
    const pad = Math.max((max - min) * 0.1, ref * 0.004);
    return { min: min - pad, max: max + pad, ref };
  }, [zones, row.reference_price]);

  const selected: CtaTriggerZone | null =
    zones.find((z) => z.id === selectedId) ?? zones.reduce<CtaTriggerZone | null>(
      (best, z) => (best === null || Math.abs(z.distance_pct) < Math.abs(best.distance_pct) ? z : best),
      null,
    );

  if (!axis || !selected) {
    return (
      <div>
        <p className="flex items-center gap-1 text-micro text-ink-400">
          {t('模型触发位（需收盘确认）')}
          <InfoHint hint={CTA_HINTS.triggers} size={10} />
        </p>
        <p className="mt-2 text-micro text-ink-400">{t('±12% 情景范围内没有会显著改变目标仓位的价格')}</p>
      </div>
    );
  }

  const maxWeight = Math.max(...zones.map((z) => z.weight_share), 0.01);
  const topPct = (price: number) => ((axis.max - price) / (axis.max - axis.min)) * 100;
  const crossedIds = row.intraday?.crossed_zone_ids ?? [];

  return (
    <div>
      <p className="flex items-center gap-1 text-micro text-ink-400">
        {t('模型触发位（需收盘确认）')}
        <InfoHint hint={CTA_HINTS.triggers} size={10} />
      </p>

      <div className="mt-2 flex gap-2">
        {/* 价格轴（顶=最高触发价 · 底=最低触发价） */}
        <div className="relative h-72 w-10 shrink-0" aria-hidden="true">
          <span className="absolute right-0 top-0 font-mono text-micro text-ink-400 tnum">{fmtPrice(axis.max)}</span>
          <span className="absolute bottom-0 right-0 font-mono text-micro text-ink-400 tnum">{fmtPrice(axis.min)}</span>
        </div>

        {/* 阶梯轨道 */}
        <div className="relative h-72 flex-1" role="group" aria-label={t('触发阶梯')}>
          <span className="absolute inset-y-0 left-0 w-px bg-line-strong" aria-hidden />
          {zones.map((zone) => {
            const crossed = crossedIds.includes(zone.id);
            const mid = (zone.price_low + zone.price_high) / 2;
            const isUp = zone.est_position_change >= 0;
            const isSelected = selected.id === zone.id;
            return (
              <button
                key={zone.id}
                type="button"
                aria-pressed={isSelected}
                aria-label={`${ZONE_LABELS[zone.label_key] ?? zone.label_key} ${fmtPrice(zone.price_low)} – ${fmtPrice(zone.price_high)}`}
                onClick={() => setSelectedId(zone.id)}
                className={cn(
                  'absolute left-2 flex min-h-[30px] -translate-y-1/2 items-center gap-1.5 overflow-hidden rounded-md border px-2 py-1 text-left transition-[box-shadow,border-color] duration-fast',
                  isUp ? 'border-up-600/25 bg-up-50' : 'border-down-600/25 bg-down-50',
                  isSelected && 'ring-2 ring-brand-500/40',
                )}
                style={{
                  top: `${topPct(mid)}%`,
                  /* 块宽≈权重占比：权重越大的触发位在阶梯上越宽 */
                  width: `${34 + (zone.weight_share / maxWeight) * 54}%`,
                }}
              >
                <span className={cn('absolute inset-y-0 left-0 w-0.5', isUp ? 'bg-up-600' : 'bg-down-600')} aria-hidden />
                <span className={cn('truncate text-micro font-medium', isUp ? 'text-up-700' : 'text-down-700')}>
                  {ZONE_LABELS[zone.label_key] ?? zone.label_key}
                </span>
                <span className="shrink-0 font-mono text-micro text-ink-500 tnum">
                  {zone.distance_pct > 0 ? '+' : ''}{zone.distance_pct.toFixed(1)}%
                </span>
                {crossed && <PulseDot className="shrink-0" />}
              </button>
            );
          })}
          {/* 现价标记 */}
          <div className="absolute inset-x-0" style={{ top: `${topPct(axis.ref)}%` }}>
            <span className="absolute inset-x-0 top-0 border-t border-dashed border-ink-400" aria-hidden />
            <span className="absolute right-0 top-0 -translate-y-1/2 rounded-pill border border-line bg-card px-1.5 py-0.5 font-mono text-micro text-ink-600 tnum">
              {t('现价 {p}', { p: fmtPrice(axis.ref) })}
            </span>
          </div>
        </div>
      </div>

      {/* 选中区间详情 */}
      <div className="mt-3 rounded-md bg-paper-2 px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className={cn('text-caption font-medium', selected.est_position_change >= 0 ? 'text-up-700' : 'text-down-700')}>
            {ZONE_LABELS[selected.label_key] ?? selected.label_key}
          </span>
          <span className="font-mono text-caption text-ink-800 tnum">
            {fmtPrice(selected.price_low)} – {fmtPrice(selected.price_high)}
          </span>
          <span className="rounded-pill border border-line bg-card px-1.5 py-0.5 text-micro text-ink-500">
            {ZONE_KIND[selected.kind]}
          </span>
          <span className="rounded-pill border border-line bg-card px-1.5 py-0.5 text-micro text-ink-500">
            {t('需收盘确认')}
          </span>
          {crossedIds.includes(selected.id) && (
            <span className="inline-flex items-center gap-1 rounded-pill bg-warn-50 px-1.5 py-0.5 text-micro text-warn-600">
              <PulseDot />
              {t('盘中已穿越 · 待收盘确认')}
            </span>
          )}
        </div>
        <p className="mt-1.5 font-mono text-micro text-ink-500 tnum">
          {t('估算 Δ{v}', { v: signed(selected.est_position_change) })}
          {' · '}
          {t('趋势 {a} · 波动率 {b}', { a: signed(selected.trend_change), b: signed(selected.vol_change) })}
        </p>
        <p className="mt-0.5 font-mono text-micro text-ink-400 tnum">
          {selected.models.map((m) => MODEL_SHORT[m] ?? m).join('/')}
          {' · '}
          {t('权重 {w}%', { w: Math.round(selected.weight_share * 100) })}
          {' · '}
          {selected.distance_pct > 0 ? '+' : ''}{selected.distance_pct.toFixed(1)}%
        </p>
      </div>
    </div>
  );
}
