/**
 * SignalCards · 当日其余事件小卡网格（V3 小卡结构重建，剥离 LeadSignalCard 引用）
 * 仅渲染除 lead 外的当日事件；3 列网格（md 2 列 / 移动单列），stagger 45ms
 * 小卡 = TickerLogo + 代码/名称 + 相对触发时间 · SETUP_CN+LIFECYCLE_CN chips · 现价/涨跌 tick-flash
 *       · rvol 量能徽标 · PriceScale 价格标尺 · 9 评分迷你条 · 强度条 + 证据 N 条
 * 交互：hover translateY(-3px)+sh-2（240ms ease-out，全站统一 card-lift 参数）· 点击小卡开 EventDetail 模态
 *      · 点击 ticker 走 useShell().openTicker 个股抽屉（非全屏纪律）
 */
import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { DUR_SECTION } from '@/lib/motion';
import { fmtPrice, fmtRelative } from '@/lib/format';
import { useShell } from '@/components/Layout';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import StrengthBar from '@/components/shared/StrengthBar';
import Icon from '@/components/icons';
import PriceScale from './PriceScale';
import { ScoreBarsMini } from './ScoreBars';
import { LIFECYCLE_CHIP_CLASS, LIFECYCLE_CN, LIFECYCLE_TONE, SETUP_CN } from './types';
import type { BreakoutCurrentEvent } from './types';
import { t } from '../../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

/* ---------------- chip 行（SETUP + LIFECYCLE + rvol 量能徽标）；live 可空字段显「—」 ---------------- */
function ChipRow({ ev }: { ev: BreakoutCurrentEvent }) {
  const rvol = typeof ev.rvol_time_of_day === 'number' && Number.isFinite(ev.rvol_time_of_day) ? ev.rvol_time_of_day : null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center whitespace-nowrap rounded-xs border border-brand-400/60 bg-brand-50 px-1.5 py-px text-micro font-medium leading-[16px] text-brand-600">
        {SETUP_CN[ev.setup_type] ?? ev.setup_type ?? '—'}
      </span>
      <span
        className={cn(
          'inline-flex items-center whitespace-nowrap rounded-xs border px-1.5 py-px text-micro font-medium leading-[16px]',
          LIFECYCLE_CHIP_CLASS[LIFECYCLE_TONE[ev.lifecycle_state] ?? 'ink'],
        )}
      >
        {LIFECYCLE_CN[ev.lifecycle_state] ?? ev.lifecycle_state ?? '—'}
      </span>
      <span className="ml-auto inline-flex items-center gap-1 rounded-xs border border-warn-600/40 bg-warn-50 px-1.5 py-px font-mono text-micro leading-[16px] text-warn-600 tnum">
        {t('量能')} {rvol !== null ? `${rvol.toFixed(1)}×` : '—'}
      </span>
    </div>
  );
}

/* ---------------- 个股小卡 ---------------- */
interface SignalCardProps {
  ev: BreakoutCurrentEvent;
  index: number;
  flash: 'up' | 'down' | null;
  locate: boolean;
  onOpen: (ev: BreakoutCurrentEvent) => void;
}

function SignalCard({ ev, index, flash, locate, onOpen }: SignalCardProps) {
  const { openTicker } = useShell();
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (locate) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [locate]);

  return (
    <motion.article
      ref={ref}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR_SECTION, ease: EASE_PAPER, delay: Math.min(index * 0.045, 0.5) }}
      className="relative"
    >
      {/* 整卡点击与 ticker 按钮是兄弟，避免 article[role=button] 内再嵌 button。 */}
      <button
        type="button"
        onClick={() => onOpen(ev)}
        aria-label={t('{ticker} {setup} 信号卡，打开事件详情', { ticker: ev.ticker, setup: SETUP_CN[ev.setup_type] ?? ev.setup_type ?? '' })}
        className="absolute inset-0 z-0 rounded-lg"
      />
      {/* hover 上浮 -3px + sh-2 走 card-lift（hover/pointer 门控 CSS，触屏不粘滞）。
          必须挂内层 div：framer 入场后在本元素留下内联 transform:none，
          会压掉同元素上的 CSS hover 位移（原 whileHover 方案因此存在）。 */}
      <div
        className={cn(
          'pointer-events-none card-surface card-lift relative z-10 h-full cursor-pointer p-4',
          locate && 'bk-locate',
        )}
      >
      {/* 顶行：Logo + 代码/名称 + 相对时间（ticker 点击开个股抽屉） */}
      <div className="flex items-center gap-2.5">
        <TickerLogo ticker={ev.ticker} />
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              openTicker(ev.ticker);
            }}
            aria-label={t('打开 {ticker} 个股详情抽屉', { ticker: ev.ticker })}
            className="pointer-events-auto font-mono text-body-s font-semibold text-ink-800 underline-offset-2 transition-colors hover:text-brand-600 hover:underline"
          >
            {ev.ticker}
          </button>
          <p className="truncate text-micro text-ink-400">
            {ev.name} · {ev.sector}
          </p>
        </div>
        <span className="shrink-0 font-mono text-micro text-ink-400 tnum">{fmtRelative(ev.triggered_at)}</span>
      </div>

      {/* chip 行 */}
      <div className="mt-2.5">
        <ChipRow ev={ev} />
      </div>

      {/* 现价行（tick-flash） */}
      <div className="mt-3 flex items-end justify-between gap-2">
        <span
          className={cn(
            'tick-flash rounded-xs px-1 font-mono text-data-l text-ink-900 tnum',
            flash === 'up' && 'tick-flash-up',
            flash === 'down' && 'tick-flash-down',
          )}
        >
          {typeof ev.current_price === 'number' && Number.isFinite(ev.current_price) ? fmtPrice(ev.current_price) : '—'}
        </span>
        <ChangeBadge value={ev.session_change_pct} />
      </div>

      {/* 价格标尺 */}
      <PriceScale
        className="mt-2"
        invalidation={ev.invalidation_price}
        trigger={ev.event_price}
        target={ev.target_price}
        current={ev.current_price}
        flash={flash}
      />

      {/* 评分套组迷你条 */}
      <ScoreBarsMini event={ev} className="mt-3 border-t border-line pt-3" />

      {/* 底行：后端未提供证据时不显示假 0。 */}
      <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
        <StrengthBar score={ev.intrinsic_strength_score} width={64} />
        {(ev.evidence ?? []).length > 0 && (
          <span className="inline-flex items-center gap-1 text-micro text-ink-400">
            <Icon name="doc-quote" size={12} />
            {t('证据')} {(ev.evidence ?? []).length} {t('条')}
          </span>
        )}
      </div>
      </div>
    </motion.article>
  );
}

/* ---------------- 网格（仅当日其余事件，lead 已由大卡承担） ---------------- */
interface SignalCardsProps {
  events: BreakoutCurrentEvent[];
  flashes: Record<string, 'up' | 'down'>;
  locateTicker: string | null;
  onOpen: (ev: BreakoutCurrentEvent) => void;
}

export default function SignalCards({ events, flashes, locateTicker, onOpen }: SignalCardsProps) {
  if (events.length === 0) return null;
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {events.map((ev, i) => (
        <SignalCard
          key={ev.event_id}
          ev={ev}
          index={i}
          flash={flashes[ev.ticker] ?? null}
          locate={locateTicker === ev.ticker}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}
