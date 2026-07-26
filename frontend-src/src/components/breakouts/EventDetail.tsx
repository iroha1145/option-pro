/**
 * B4 事件详情模态（breakouts.md；居中 720px，spring-pop 240ms，ESC/背板关闭）
 * 支撑/阻力区带可视化 · 区间持续指标 · transitions 生命周期轨迹
 * 评分条组 · 证据竖向时间线（stagger 30ms）· 内嵌该股催化剂摘要（catalystsApi.byTicker）
 */
import { useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { catalystsApi } from '@/api/modules/catalysts';
import type { NewsItem } from '@/api/types';
import { usePolling } from '@/hooks/usePolling';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { cn } from '@/lib/utils';
import { fmtPrice, fmtRelative } from '@/lib/format';
import Icon from '@/components/icons';
import TickerLogo from '@/components/shared/TickerLogo';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonText } from '@/components/shared/Skeleton';
import PriceScale from './PriceScale';
import { RangePersistenceBars, ScoreBarsFull } from './ScoreBars';
import {
  LIFECYCLE_CHIP_CLASS,
  LIFECYCLE_CN,
  LIFECYCLE_TONE,
  SESSION_CN,
  SETUP_CN,
} from './types';
import type { BreakoutEventFull } from './types';
import { t as __t } from '../../i18n/core.ts';

function hhmm(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/* ---------------- 支撑/阻力区带（live 区带可空 → 诚实空态） ---------------- */
function ZoneBand({ ev }: { ev: BreakoutEventFull }) {
  const sz = ev.support_zone as BreakoutEventFull['support_zone'] | null;
  const rz = ev.resistance_zone as BreakoutEventFull['resistance_zone'] | null;
  if (!sz || !rz || !fin(sz.low) || !fin(sz.high) || !fin(rz.low) || !fin(rz.high) || !fin(ev.current_price)) {
    return (
      <p className="flex h-12 items-center justify-center rounded-md border border-line bg-card-warm text-caption text-ink-400">
        {__t('暂无区带数据')}
      </p>
    );
  }
  const anchors = [sz.low, ev.invalidation_price, ev.current_price, rz.high, ev.target_price].filter(fin);
  const lo = Math.min(...anchors);
  const hi = Math.max(...anchors);
  const span = Math.max(0.0001, hi - lo);
  const pad = span * 0.08;
  const min = lo - pad;
  const max = hi + pad;
  const x = (v: number) => Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));
  const zw = (a: number, b: number) => Math.max(1.5, x(b) - x(a));

  return (
    <div aria-label={__t('支撑区 {szLow} 至 {szHigh}，阻力区 {rzLow} 至 {rzHigh}', { szLow: fmtPrice(sz.low), szHigh: fmtPrice(sz.high), rzLow: fmtPrice(rz.low), rzHigh: fmtPrice(rz.high) })}>
      <div className="relative h-12 rounded-md border border-line bg-card-warm">
        {/* 支撑区带 */}
        <div
          className="absolute inset-y-1 rounded-xs border border-up-600/30 bg-up-600/10"
          style={{ left: `${x(sz.low)}%`, width: `${zw(sz.low, sz.high)}%` }}
        />
        {/* 阻力区带 */}
        <div
          className="absolute inset-y-1 rounded-xs border border-down-600/30 bg-down-600/10"
          style={{ left: `${x(rz.low)}%`, width: `${zw(rz.low, rz.high)}%` }}
        />
        {/* 枢轴虚线 */}
        {fin(ev.pivot_price) && (
          <div className="absolute inset-y-0 border-l border-dashed border-ink-400" style={{ left: `${x(ev.pivot_price)}%` }} aria-hidden="true" />
        )}
        {/* 失效价 */}
        {fin(ev.invalidation_price) && (
          <div className="absolute inset-y-0 border-l border-down-600/70" style={{ left: `${x(ev.invalidation_price)}%` }} aria-hidden="true" />
        )}
        {/* 现价竖针 */}
        <div className="absolute inset-y-0 border-l-2 border-brand-600" style={{ left: `${x(ev.current_price)}%` }} aria-hidden="true">
          <span className="absolute -top-1 left-1/2 size-1.5 -translate-x-1/2 rotate-45 bg-brand-600" />
        </div>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-0.5 font-mono text-micro text-ink-500 tnum">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block size-2 rounded-[2px] bg-up-600/25 ring-1 ring-up-600/40" />
          {__t('支撑区')} {fmtPrice(sz.low)}–{fmtPrice(sz.high)}
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block size-2 rounded-[2px] bg-down-600/25 ring-1 ring-down-600/40" />
          {__t('阻力区')} {fmtPrice(rz.low)}–{fmtPrice(rz.high)}
        </span>
        <span className="text-ink-400">{__t('枢轴')} {fin(ev.pivot_price) ? fmtPrice(ev.pivot_price) : '—'}</span>
        <span className="ml-auto text-brand-600">{__t('现价')} {fmtPrice(ev.current_price)}</span>
      </div>
    </div>
  );
}

/* ---------------- 生命周期轨迹（时间轴） ---------------- */
function LifecycleTrack({ ev }: { ev: BreakoutEventFull }) {
  const list = ev.transitions ?? [];
  if (!list.length) return <p className="text-caption text-ink-400">{__t('暂无轨迹数据')}</p>;
  return (
    <ol className="no-scrollbar flex items-start gap-0 overflow-x-auto pb-1" aria-label={__t("生命周期轨迹")}>
      {list.map((t, i) => {
        const last = i === list.length - 1;
        const tone = LIFECYCLE_TONE[t.state] ?? 'ink';
        return (
          <li key={`${t.state}-${i}`} className="flex min-w-[76px] flex-1 items-start last:flex-none">
            <div className="flex flex-col items-center">
              <span
                className={cn(
                  'mt-1 size-2.5 rounded-full ring-2',
                  last
                    ? tone === 'down'
                      ? 'bg-down-600 ring-down-600/25'
                      : tone === 'up'
                        ? 'bg-up-600 ring-up-600/25'
                        : 'bg-brand-600 ring-brand-600/25'
                    : 'bg-ink-300 ring-line',
                )}
                aria-hidden="true"
              />
              <span className={cn('mt-1.5 whitespace-nowrap text-[10px] leading-[14px]', last ? 'font-semibold text-ink-800' : 'text-ink-400')}>
                {LIFECYCLE_CN[t.state] ?? t.state}
              </span>
              <span className="whitespace-nowrap font-mono text-[10px] leading-[14px] text-ink-300 tnum">{hhmm(t.at)}</span>
            </div>
            {!last && <span className="mx-1 mt-[9px] h-px min-w-4 flex-1 bg-line-strong" aria-hidden="true" />}
          </li>
        );
      })}
    </ol>
  );
}

/* ---------------- 催化剂摘要 ---------------- */
function CatalystDigest({ ticker }: { ticker: string }) {
  const q = usePolling(() => catalystsApi.byTicker(ticker), null, [ticker]);
  const items: NewsItem[] | null = q.data ? q.data.slice(0, 3) : null;
  const failed = q.error !== null;

  const DOT: Record<NewsItem['sentiment'], string> = {
    positive: 'bg-up-600',
    neutral: 'bg-ink-300',
    negative: 'bg-down-600',
  };

  return (
    <div>
      {items === null && !failed && <SkeletonText lines={3} />}
      {failed && <p className="text-caption text-ink-400">{__t('催化剂数据暂不可用')}</p>}
      {items !== null && items.length === 0 && <p className="text-caption text-ink-400">{__t('该股近 24h 暂无相关催化剂')}</p>}
      {items !== null &&
        items.map((n) => (
          <div key={n.id} className="flex items-start gap-2 border-b border-line py-2 last:border-b-0">
            <span className={cn('mt-1.5 size-1.5 shrink-0 rounded-full', DOT[n.sentiment])} aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-caption text-ink-800">{n.title}</p>
              <p className="mt-0.5 font-mono text-micro text-ink-400 tnum">
                {n.source} · {fmtRelative(n.publishedAt)}
              </p>
            </div>
          </div>
        ))}
    </div>
  );
}

/* ---------------- 模态主体 ---------------- */
interface EventDetailProps {
  event: BreakoutEventFull | null;
  onClose: () => void;
  onOpenTicker: (ticker: string) => void;
  onShowTickerEvents: (ticker: string) => void;
  /**
   * 补充详情加载失败（审计 P2-20）。
   * 打开的是列表里已有的那条事件，因此这不是致命错误：如实说明补充字段没取到，
   * 并给一次重试，而不是静默地把它当成「详情就是这样」。
   */
  detailError?: ApiError | null;
  onRetryDetail?: () => void;
}

export default function EventDetail({
  event,
  onClose,
  onOpenTicker,
  onShowTickerEvents,
  detailError = null,
  onRetryDetail,
}: EventDetailProps) {
  /* 焦点圈定 + 关闭后归还焦点（审计 P3-2 同一口径） */
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, event !== null);

  /* ESC 关闭 + 锁定滚动 */
  useEffect(() => {
    if (!event) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [event, onClose]);

  return (
    <AnimatePresence>
      {event && (
        <div ref={panelRef} className="fixed inset-0 z-[80] flex items-center justify-center p-3 sm:p-6" role="dialog" aria-modal="true" aria-label={__t('{ticker} 突破事件详情', { ticker: event.ticker })}>
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
            className="absolute inset-0 bg-[rgba(13,22,38,.28)] backdrop-blur-[2px]"
            aria-hidden="true"
          />
          <motion.div
            key="panel"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.16 } }}
            transition={{ type: 'spring', stiffness: 520, damping: 32 }}
            className="relative flex max-h-[88dvh] w-full max-w-[720px] flex-col overflow-hidden rounded-xl border border-line bg-card shadow-sh-3"
          >
            {detailError && (
              <div className="flex flex-wrap items-center gap-2 border-b border-warn-600/25 bg-warn-50 px-5 py-2 text-caption text-warn-600">
                <Icon name="flag" size={13} />
                <span>{__t('补充详情未能加载，以下为列表已有字段。')}</span>
                {onRetryDetail && (
                  <button
                    type="button"
                    onClick={onRetryDetail}
                    className="font-medium underline underline-offset-2"
                  >
                    {__t('重试')}
                  </button>
                )}
              </div>
            )}
            {/* 头 */}
            <div className="flex items-center gap-3 border-b border-line px-5 py-4">
              <TickerLogo ticker={event.ticker} size={36} />
              <div className="min-w-0 flex-1">
                <p className="flex items-baseline gap-2">
                  <span className="font-mono text-[17px] font-semibold leading-[24px] text-ink-900">{event.ticker}</span>
                  <span className="truncate text-body-s text-ink-500">{event.name}</span>
                </p>
                <p className="mt-0.5 font-mono text-micro text-ink-400 tnum">
                  {__t('触发')} {hhmm(event.triggered_at)} · {SESSION_CN[event.session] ?? '—'} · {SETUP_CN[event.setup_type] ?? event.setup_type ?? '—'}
                </p>
              </div>
              <span
                className={cn(
                  'inline-flex items-center whitespace-nowrap rounded-xs border px-2 py-0.5 text-caption font-medium',
                  LIFECYCLE_CHIP_CLASS[LIFECYCLE_TONE[event.lifecycle_state] ?? 'ink'],
                )}
              >
                {LIFECYCLE_CN[event.lifecycle_state] ?? event.lifecycle_state ?? '—'}
              </span>
              <button
                onClick={onClose}
                className="rounded-sm p-1.5 text-ink-400 transition-colors hover:bg-paper-2 hover:text-ink-600"
                aria-label={__t("关闭详情")}
              >
                <Icon name="x" size={16} />
              </button>
            </div>

            {/* 内容 */}
            <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-5">
              {/* 价格标尺放大版 */}
              <section>
                <p className="eyebrow mb-2">{__t('价格标尺')}</p>
                <PriceScale large invalidation={event.invalidation_price} trigger={event.event_price} target={event.target_price} current={event.current_price} />
                <div className="mt-1 flex flex-wrap gap-x-4 font-mono text-micro text-ink-400 tnum">
                  <span>{__t('跳空')} {fin(event.gap_pct) ? `${event.gap_pct >= 0 ? '+' : ''}${event.gap_pct.toFixed(2)}%` : '—'}</span>
                  <span>{__t('量能')} {fin(event.rvol_time_of_day) ? `${event.rvol_time_of_day.toFixed(1)}×` : '—'}</span>
                  <span>{__t('时段涨跌')} {fin(event.session_change_pct) ? `${event.session_change_pct >= 0 ? '+' : ''}${event.session_change_pct.toFixed(2)}%` : '—'}</span>
                </div>
              </section>

              {/* 支撑/阻力区带 */}
              <section>
                <p className="eyebrow mb-2">{__t('支撑 / 阻力区带')}</p>
                <ZoneBand ev={event} />
              </section>

              {/* 区间持续 + 评分 */}
              <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                <section>
                  <p className="eyebrow mb-2">{__t('区间持续指标')}</p>
                  <RangePersistenceBars event={event} />
                </section>
                <section>
                  <p className="eyebrow mb-2">
                    {__t('评分套组')}
                    <InfoHint hint={SCORE_HINTS.breakoutPriority} size={12} className="ml-1" />
                  </p>
                  <ScoreBarsFull event={event} />
                </section>
              </div>

              {/* 生命周期轨迹 */}
              <section>
                <p className="eyebrow mb-2">{__t('生命周期轨迹')}</p>
                <LifecycleTrack ev={event} />
              </section>

              {/* 证据列表：后端未提供时整段隐藏，不显示假 0。 */}
              {(event.evidence ?? []).length > 0 && (
                <section>
                  <p className="eyebrow mb-2">{__t('证据列表 ·')} {(event.evidence ?? []).length} {__t('条')}</p>
                  <ol className="relative ml-1.5 border-l-2 border-line pl-4">
                    {(event.evidence ?? []).map((line, i) => (
                    <motion.li
                      key={i}
                      initial={{ opacity: 0, x: 8 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1], delay: 0.12 + i * 0.03 }}
                      className="relative py-1.5"
                    >
                      <span className="absolute -left-[21px] top-[11px] size-2 rounded-full bg-brand-500 ring-2 ring-brand-100" aria-hidden="true" />
                      <span className="mr-2 font-mono text-micro text-ink-400 tnum">{line.slice(0, 5)}</span>
                      <span className="text-body-s text-ink-600">{line.slice(6)}</span>
                    </motion.li>
                    ))}
                  </ol>
                </section>
              )}

              {/* 催化剂摘要 */}
              <section>
                <p className="eyebrow mb-2 flex items-center gap-1.5">
                  <Icon name="bolt" size={13} className="text-warn-600" />
                  {__t('相关催化剂')}
                </p>
                <CatalystDigest ticker={event.ticker} />
              </section>
            </div>

            {/* 底部操作 */}
            <div className="flex items-center gap-2 border-t border-line bg-card-warm px-5 py-3">
              <button
                onClick={() => onOpenTicker(event.ticker)}
                className="flex items-center gap-1.5 rounded-md bg-brand-600 px-3.5 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
              >
                {__t('查看个股详情')}
                <Icon name="arrow-up-right" size={13} />
              </button>
              <button
                onClick={() => onShowTickerEvents(event.ticker)}
                className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3.5 py-2 text-caption font-medium text-ink-600 transition-colors hover:border-brand-400 hover:text-brand-600"
              >
                {__t('该代码全部事件')}
              </button>
              <span className="ml-auto font-mono text-micro text-ink-300 tnum">{event.event_id}</span>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
