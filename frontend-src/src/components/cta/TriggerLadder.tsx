/**
 * 触发阶梯（/cta 主区）：交易终端式「价格梯」——发丝线分隔的等距表列行。
 *
 * - 行序=价格降序：上方触发区（高→低）→ 现价虚线行 → 下方触发区（高→低，
 *   离现价最近的紧贴现价行）；方向符 ▲/▼ 表示触发区在现价上方/下方。
 * - 数字一律 font-mono tnum 右对齐表列：价格区间 / 距离 / 估算 Δ / 权重计量条
 *   （bg-line 轨道 + ink-500 填充，宽=weight_share/maxWeight）。
 * - 触发位全部「需收盘确认」：盘中穿越的区间在标签后挂脉动圆点，展开详情里
 *   再给「盘中已穿越 · 待收盘确认」暂定章，不构成正式触发。
 * - 点击行在手风琴内展开详情（类型/确认章/估算 Δ 与趋势/波动率拆分/模型权重），
 *   默认展开距现价最近的一行；选中行 = 左侧 2px brand 竖条 + bg-paper-2（不用 ring）。
 * - 语义纪律：这是多周期趋势模型群的**代理估算**，不是任何机构的真实仓位披露。
 */
import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
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

/** 方向小三角（CSS border 三角，▲=现价上方触发区 / ▼=下方）。
 *  中性墨色：三角只表示价格位置，不表示交易动作——「涨上去反而净减仓」
 *  的区间里绿三角+红Δ会构成混合语义（审计 v3），红绿只留给净仓位变化。 */
function DirTick({ up }: { up: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block size-0 shrink-0 border-x-[3.5px] border-x-transparent',
        up ? 'border-b-[5px] border-b-ink-400' : 'border-t-[5px] border-t-ink-400',
      )}
    />
  );
}

/** 权重计量条：24px 宽 3px 高，bg-line 轨道 + ink-500 填充 */
function WeightMeter({ share, maxWeight }: { share: number; maxWeight: number }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1">
      <span className="inline-block h-[3px] w-6 overflow-hidden rounded-pill bg-line" role="presentation">
        <span
          className="block h-full bg-ink-500"
          style={{ width: `${Math.max(4, (share / maxWeight) * 100)}%` }}
        />
      </span>
      <span className="font-mono text-micro text-ink-500 tnum">{Math.round(share * 100)}%</span>
    </span>
  );
}

export default function TriggerLadder({ row }: { row: CtaInstrumentEstimate }) {
  /* 显示序=价格降序：上方组高压顶、下方组高压上（离现价最近者贴住现价行） */
  const above = useMemo(() => [...(row.trigger_levels?.above ?? [])].sort((a, b) => b.price - a.price), [row]);
  const below = useMemo(() => [...(row.trigger_levels?.below ?? [])].sort((a, b) => b.price - a.price), [row]);
  const zones = useMemo(() => [...above, ...below], [above, below]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const crossedIds = row.intraday?.crossed_zone_ids ?? [];

  const nearest: CtaTriggerZone | null = zones.reduce<CtaTriggerZone | null>(
    (best, z) => (best === null || Math.abs(z.distance_pct) < Math.abs(best.distance_pct) ? z : best),
    null,
  );
  /* 手风琴：默认展开距现价最近的一行 */
  const expandedId = selectedId ?? nearest?.id ?? null;

  if (!zones.length || row.reference_price === null || !nearest) {
    return (
      <div>
        <p className="flex items-center gap-1 text-micro text-ink-400">
          {t('模型断点区（需收盘确认）')}
          <InfoHint hint={CTA_HINTS.triggers} size={10} />
        </p>
        <p className="mt-2 text-micro text-ink-400">{t('±12% 情景范围内没有会显著改变目标仓位的价格')}</p>
      </div>
    );
  }

  const maxWeight = Math.max(...zones.map((z) => z.weight_share), 0.01);

  const renderZone = (zone: CtaTriggerZone, up: boolean) => {
    const crossed = crossedIds.includes(zone.id);
    const isUp = zone.est_position_change >= 0;
    const expanded = expandedId === zone.id;
    const tone = isUp ? 'text-up-700' : 'text-down-700';
    return (
      <div key={zone.id} className="border-t border-line">
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={`${ZONE_LABELS[zone.label_key] ?? zone.label_key} ${fmtPrice(zone.price_low)} – ${fmtPrice(zone.price_high)}`}
          onClick={() => setSelectedId(expanded ? (nearest.id === zone.id ? null : nearest.id) : zone.id)}
          className={cn(
            'relative flex w-full flex-wrap items-center gap-x-2 py-2 pl-3 pr-1 text-left transition-colors duration-fast',
            expanded ? 'bg-paper-2' : 'hover:bg-paper-2',
          )}
        >
          {expanded && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand-600" aria-hidden />}
          {/* 1 方向符 ▲/▼ */}
          <DirTick up={up} />
          {/* 2 标签（可缩） */}
          <span className="inline-flex min-w-0 flex-1 items-center gap-1.5">
            <span className="truncate text-caption font-medium text-ink-800">
              {ZONE_LABELS[zone.label_key] ?? zone.label_key}
            </span>
            {crossed && <PulseDot className="shrink-0" />}
          </span>
          {/* 3 距离（右对齐定宽）——价格位置读数用中性色，红绿只留给净 Δ */}
          <span className="order-3 w-12 shrink-0 text-right font-mono text-caption text-ink-600 tnum sm:order-4">
            {zone.distance_pct > 0 ? '+' : ''}{zone.distance_pct.toFixed(1)}%
          </span>
          {/* 4 估算 Δ（右对齐定宽）——手机竖屏留在第一行 */}
          <span className={cn('order-4 w-12 shrink-0 text-right font-mono text-caption font-semibold tnum sm:order-5', tone)}>
            {signed(zone.est_position_change)}
          </span>
          {/* 5+6 价格区间 & 权重计量条：手机竖屏 basis-full 强制折到第二行；
              桌面 sm:contents 拆平包装器，子列恢复 价格区间→…→权重 的列序 */}
          <span className="order-5 flex basis-full items-center gap-2 pl-[18px] sm:contents">
            <span className="shrink-0 font-mono text-micro text-ink-500 tnum sm:order-3 sm:text-caption sm:text-ink-800">
              {fmtPrice(zone.price_low)} – {fmtPrice(zone.price_high)}
            </span>
            <span className="shrink-0 sm:order-6">
              <WeightMeter share={zone.weight_share} maxWeight={maxWeight} />
            </span>
          </span>
        </button>

        <AnimatePresence initial={false}>
          {expanded && (
            <motion.div
              key="detail"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
              className="overflow-hidden"
            >
              <div className="border-t border-line/60 bg-paper-2 px-3 py-2">
                <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
                  <span className="rounded-pill border border-line px-1.5 py-0.5 text-micro text-ink-500">
                    {ZONE_KIND[zone.kind]}
                  </span>
                  <span className="rounded-pill border border-line px-1.5 py-0.5 text-micro text-ink-500">
                    {t('需收盘确认')}
                  </span>
                  {crossed && (
                    <span className="inline-flex items-center gap-1 rounded-pill border border-warn-600/40 px-1.5 py-0.5 text-micro text-warn-600">
                      <PulseDot />
                      {t('盘中已穿越 · 待收盘确认')}
                    </span>
                  )}
                </div>
                {/* 状态迁移读数：标签的生成依据，让「翻空/减仓」可当场核验 */}
                {zone.position_before !== null && zone.position_after !== null && (
                  <p className="mt-1.5 font-mono text-micro text-ink-600 tnum">
                    {t('仓位 {a} → {b}', { a: signed(zone.position_before), b: signed(zone.position_after) })}
                  </p>
                )}
                <p className="mt-0.5 font-mono text-micro text-ink-500 tnum">
                  {t('估算 Δ{v}', { v: signed(zone.est_position_change) })}
                  {' · '}
                  {t('趋势 {a} · 波动率 {b}', { a: signed(zone.trend_change), b: signed(zone.vol_change) })}
                </p>
                <p className="mt-0.5 font-mono text-micro text-ink-400 tnum">
                  {zone.models.map((m) => MODEL_SHORT[m] ?? m).join('/')}
                  {' · '}
                  {t('权重 {w}%', { w: Math.round(zone.weight_share * 100) })}
                  {/* 垫衬/钳位可让区间边界贴到现价（0.0%）：单列最近原始断点，
                      避免把聚簇缓冲边界读成精确阈值（审计 QQQ 0.0% 案例） */}
                  {zone.nearest_event_distance_pct !== null && (
                    <>
                      {' · '}
                      {t('最近断点 {v}%', {
                        v: `${zone.nearest_event_distance_pct > 0 ? '+' : ''}${zone.nearest_event_distance_pct.toFixed(1)}`,
                      })}
                    </>
                  )}
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  };

  return (
    <div>
      <p className="flex items-center gap-1 text-micro text-ink-400">
        {t('模型断点区（需收盘确认）')}
        <InfoHint hint={CTA_HINTS.triggers} size={10} />
      </p>

      <div className="mt-2 border-b border-line" role="group" aria-label={t('触发阶梯')}>
        {above.map((zone) => renderZone(zone, true))}

        {/* 现价行：虚线发丝分隔，mono 品牌色读数 */}
        <div className="flex items-center justify-center border-t border-dashed border-ink-400 bg-paper-2 py-2">
          <span className="font-mono text-caption text-brand-700 tnum">
            {t('现价 {p}', { p: fmtPrice(row.reference_price) })}
          </span>
        </div>

        {below.map((zone) => renderZone(zone, false))}
      </div>
    </div>
  );
}
