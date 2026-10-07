/**
 * B5 补充带：本月财报密度条（earnings.md）
 * 30 天横向迷你柱（每日财报数，brand-400，grow-bar 错峰）
 * hover 日 → 当日代码列表 tooltip；点击日 → 跳转该周并选中日格
 */
import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { EASE_PAPER, GROW_Y } from '@/lib/motion';
import InfoHint from '@/components/shared/InfoHint';
import type { EarningsRow } from './types';
import { addDays, etToday, fmtMDCN, fmtMMDD, weekdayCN } from './types';
import { t } from '../../i18n/core.ts';

interface DensityStripProps {
  items: EarningsRow[];
  onJumpDay: (date: string) => void;
}

const DAYS = 30;
const MAX_TOOLTIP_TICKERS = 12;

export default function DensityStrip({ items, onJumpDay }: DensityStripProps) {
  const days = useMemo(() => {
    const today = etToday();
    const byDate = new Map<string, EarningsRow[]>();
    for (const it of items) {
      const arr = byDate.get(it.date) ?? [];
      arr.push(it);
      byDate.set(it.date, arr);
    }
    return Array.from({ length: DAYS }, (_, i) => {
      const date = addDays(today, i);
      return { date, rows: byDate.get(date) ?? [] };
    });
  }, [items]);

  const max = Math.max(...days.map((d) => d.rows.length), 1);

  return (
    <section className="card-surface p-5" aria-label={t("本月财报密度")}>
      <div className="min-w-0">
        <div className="w-full min-w-0">
          <p className="eyebrow">{t('本月财报密度 · 未来 30 天')}</p>
          <motion.div
            className="mt-3 flex h-16 items-end gap-[3px]"
            role="list"
            aria-label={t("每日财报数量")}
            initial="hidden"
            whileInView="shown"
            viewport={{ once: true, amount: 0.4 }}
          >
            {days.map((d, i) => {
              const n = d.rows.length;
              const isToday = i === 0;
              return (
                /* listitem 放包装节点：role 打在 <button> 上会把按钮语义整个覆盖，
                   读屏只报「列表项」不报「按钮」（审计 #59）。 */
                <span key={d.date} role="listitem" className="contents">
                  <InfoHint
                    side="bottom"
                    className="h-full min-w-0 flex-1"
                    triggerClassName="h-full w-full"
                    hint={{
                      title: `${fmtMMDD(d.date)} ${weekdayCN(d.date)}`,
                      body: n === 0 ? (
                        <span className="block text-micro text-ink-400">{t('无财报')}</span>
                      ) : (
                        <span className="mt-0.5 flex flex-wrap gap-1">
                          {d.rows.slice(0, MAX_TOOLTIP_TICKERS).map((r) => (
                            <span key={r.ticker} className="tnum text-micro font-medium text-ink-800">
                              {r.ticker}
                            </span>
                          ))}
                          {n > MAX_TOOLTIP_TICKERS && (
                            <span className="tnum text-micro text-ink-400">
                              +{n - MAX_TOOLTIP_TICKERS}
                            </span>
                          )}
                        </span>
                      ),
                    }}
                  >
                    <button
                      onClick={() => onJumpDay(d.date)}
                      onFocus={(event) => {
                        // 鼠标点选日期后只保留悬停提示，避免移到下一天时叠出两层。
                        if (!event.currentTarget.matches(':focus-visible')) event.stopPropagation();
                      }}
                      aria-label={t('{date} {weekday}，{n} 条财报，跳转', { date: fmtMDCN(d.date), weekday: weekdayCN(d.date), n })}
                      className="group relative flex h-full w-full min-w-0 items-end focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600"
                    >
                      <motion.span
                        className={cn(
                          'w-full rounded-t-[2px] transition-colors duration-fast',
                          n > 0 ? 'bg-brand-400 group-hover:bg-brand-600' : 'bg-line',
                          isToday && 'ring-1 ring-brand-600 ring-offset-1 ring-offset-card',
                        )}
                        style={{ height: n > 0 ? `${Math.max(12, (n / max) * 100)}%` : '2px', transformOrigin: 'bottom' }}
                        variants={GROW_Y}
                        transition={{ duration: 0.7, ease: EASE_PAPER, delay: i * 0.02 }}
                      />
                    </button>
                  </InfoHint>
                </span>
              );
            })}
          </motion.div>
          <div className="mt-1.5 flex justify-between tnum text-micro text-ink-400">
            <span>{t('今天')}</span>
            <span>{t('+15 天')}</span>
            <span>{t('+30 天')}</span>
          </div>
        </div>
      </div>
    </section>
  );
}
