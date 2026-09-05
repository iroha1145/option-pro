/**
 * 价格标尺（breakouts.md B3/B4 核心组件）
 * 轨道：失效价 ──●── 目标价，三段渐变 down-600 → ink-300 → up-600
 * markers：shield 失效 / flag 触发 / target 目标；现价菱形游标 brand-600 + 2px 白描边
 * 首绘游标对应真实现价；后续位置变化使用共享短过渡与更新反馈。
 */
import { motion } from 'framer-motion';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { cn } from '@/lib/utils';
import { DUR_UI, EASE_PAPER } from '@/lib/motion';
import { fmtPrice } from '@/lib/format';
import Icon from '@/components/icons';
import type { IconName } from '@/components/icons';
import { t } from '../../i18n/core.ts';

interface PriceScaleProps {
  /** live 契约可空（如无目标价 target_price）：缺失的标记不画、不编造 */
  invalidation: number | null | undefined;
  trigger: number | null | undefined;
  target: number | null | undefined;
  current: number | null | undefined;
  /** B4 放大版：四点完整价格标注 */
  large?: boolean;
  flash?: 'up' | 'down' | null;
  className?: string;
}

interface MarkerDef {
  key: string;
  label: string;
  icon: IconName;
  value: number;
  iconCls: string;
}

const fin = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);
const edgeAnchor = (pct: number): string =>
  pct < 12 ? 'translate-x-0 text-left' : pct > 88 ? '-translate-x-full text-right' : '-translate-x-1/2 text-center';
/** flag 的视觉锚点是旗杆（glyph x=5.4/24），不是图标盒中心：按盒居中时旗杆
    悬在刻度点左侧 ~28% 图标宽、什么都不指。把盒右移这个量，杆尖正好戳在
    刻度点上（地图图钉惯例），旗身自然向右飘。 */
const FLAG_POLE_ANCHOR = (12 - 5.4) / 24;

export default function PriceScale({ invalidation, trigger, target, current, large = false, flash = null, className }: PriceScaleProps) {
  const reduced = usePrefersReducedMotion();

  const known = [invalidation, trigger, target, current].filter(fin);
  /* 已知价位不足两个：画不出标尺，诚实空态 */
  if (known.length < 2 || !fin(current)) {
    return (
      <div className={cn('w-full', className)} aria-label={t("价格标尺数据不足")}>
        <div className="flex h-9 items-center justify-center rounded-md border border-line bg-card-warm font-mono text-micro text-ink-300">
          {t('— 价位数据不足')}
        </div>
      </div>
    );
  }
  const lo = Math.min(...known);
  const hi = Math.max(...known);
  const span = Math.max(0.0001, hi - lo);
  const pad = span * 0.06;
  const min = lo - pad;
  const max = hi + pad;
  const x = (v: number) => Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));

  const markers: MarkerDef[] = [
    { key: 'invalid', label: t('失效'), icon: 'shield' as const, value: invalidation as number, iconCls: 'text-down-600' },
    { key: 'trigger', label: t('触发'), icon: 'flag' as const, value: trigger as number, iconCls: 'text-brand-600' },
    { key: 'target', label: t('目标'), icon: 'target' as const, value: target as number, iconCls: 'text-up-600' },
  ].filter((m) => fin(m.value));
  const currentPct = x(current);
  const nearbyMarker = [...markers]
    .sort((a, b) => Math.abs(x(a.value) - currentPct) - Math.abs(x(b.value) - currentPct))
    .find((marker) => Math.abs(x(marker.value) - currentPct) < 12);
  const labelMarkers = nearbyMarker
    ? markers.filter((marker) => marker.key !== nearbyMarker.key)
    : markers;

  return (
    <div
      className={cn('w-full', className)}
      aria-label={t('价格标尺：失效 {invalid}，触发 {trigger}，目标 {target}，现价 {current}', { invalid: fin(invalidation) ? fmtPrice(invalidation) : '—', trigger: fin(trigger) ? fmtPrice(trigger) : '—', target: fin(target) ? fmtPrice(target) : '—', current: fmtPrice(current) })}
    >
      {/* marker 图标行 */}
      <div className="relative h-4">
        {markers.map((m) => {
          const iconPx = large ? 15 : 12;
          return (
            <span
              key={m.key}
              className="absolute"
              style={{
                left: `${x(m.value)}%`,
                transform:
                  m.icon === 'flag'
                    ? `translateX(calc(-50% + ${(FLAG_POLE_ANCHOR * iconPx).toFixed(2)}px))`
                    : 'translateX(-50%)',
              }}
            >
              <Icon name={m.icon} size={iconPx} className={m.iconCls} />
            </span>
          );
        })}
      </div>
      {/* 轨道 + 游标 */}
      <div className="relative mt-1 h-1 rounded-pill shadow-zone" style={{ background: 'linear-gradient(90deg, var(--down-600), var(--ink-300) 50%, var(--up-600))', opacity: 0.85 }}>
        {markers.map((m) => (
          <span
            key={m.key}
            className="absolute top-1/2 size-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink-500"
            style={{ left: `${x(m.value)}%` }}
            aria-hidden="true"
          />
        ))}
        <motion.span
          initial={false}
          animate={{ left: `${x(current)}%` }}
          transition={{ duration: reduced ? 0 : DUR_UI, ease: EASE_PAPER }}
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
        >
          <span className="block size-2.5 rotate-45 rounded-[2px] bg-brand-600 shadow-[0_0_0_2px_#fff,0_1px_3px_rgba(16,24,40,.35)]" aria-hidden="true" />
        </motion.span>
      </div>
      {/* 标注行 */}
      {large ? (
        <div className="relative mt-2 h-8">
          {labelMarkers.map((m) => (
            <span key={m.key} className={cn('absolute whitespace-nowrap', edgeAnchor(x(m.value)))} style={{ left: `${x(m.value)}%` }}>
              <span className="block text-[10px] leading-[14px] text-ink-400">{m.label}</span>
              <span className="block font-mono text-micro leading-[14px] text-ink-600 tnum">{fmtPrice(m.value)}</span>
            </span>
          ))}
          <motion.span
            initial={false}
            animate={{ left: `${x(current)}%` }}
            transition={{ duration: reduced ? 0 : DUR_UI, ease: EASE_PAPER }}
            /* whitespace-nowrap：贴右缘时 abspos 盒的收缩宽度被「left 到容器右缘
               的剩余空间」卡住，-translate-x-full 是布局后才平移救不回宽度——
               「触发 / 现价」会被折成两行、价格顶到第三行，与对侧标注错层。 */
            className={cn('absolute whitespace-nowrap', edgeAnchor(x(current)))}
          >
            <span className="block text-[10px] leading-[14px] text-brand-600">
              {nearbyMarker ? t('{marker} / 现价', { marker: nearbyMarker.label }) : t('现价')}
            </span>
            <span
              className={cn(
                'tick-flash block rounded-xs px-0.5 font-mono text-micro font-semibold leading-[14px] text-brand-700 tnum',
                flash === 'up' && 'tick-flash-up',
                flash === 'down' && 'tick-flash-down',
              )}
            >
              {fmtPrice(current)}
            </span>
          </motion.span>
        </div>
      ) : (
        <div className="relative mt-1.5 h-4">
          <motion.span
            initial={false}
            animate={{ left: `${x(current)}%` }}
            transition={{ duration: reduced ? 0 : DUR_UI, ease: EASE_PAPER }}
            className={cn('absolute whitespace-nowrap', edgeAnchor(x(current)))}
          >
            <span
              className={cn(
                'tick-flash rounded-xs px-0.5 font-mono text-micro font-semibold leading-[16px] text-ink-800 tnum',
                flash === 'up' && 'tick-flash-up',
                flash === 'down' && 'tick-flash-down',
              )}
            >
              {fmtPrice(current)}
            </span>
          </motion.span>
        </div>
      )}
    </div>
  );
}
