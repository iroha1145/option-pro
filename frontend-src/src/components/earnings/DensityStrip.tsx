/**
 * B5 补充带：本月财报密度条（earnings.md）
 * 30 天横向迷你柱（每日财报数，brand-400，grow-bar 错峰）
 * hover 日 → 当日代码列表 tooltip；点击日 → 跳转该周并选中日格
 * 右侧 SourceNote 一行说明数据口径。
 */
import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import SourceNote from '@/components/shared/SourceNote';
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
          <div className="mt-3 flex h-16 items-end gap-[3px]" role="list" aria-label={t("每日财报数量")}>
            {days.map((d, i) => {
              const n = d.rows.length;
              const isToday = i === 0;
              return (
                /* listitem 放包装节点：role 打在 <button> 上会把按钮语义整个覆盖，
                   读屏只报「列表项」不报「按钮」（审计 #59）。 */
                <span key={d.date} role="listitem" className="contents">
                <button
                  onClick={() => onJumpDay(d.date)}
                  aria-label={t('{date} {weekday}，{n} 条财报，跳转', { date: fmtMDCN(d.date), weekday: weekdayCN(d.date), n })}
                  className="group relative flex h-full min-w-0 flex-1 items-end focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-400/60"
                >
                  {/* tooltip：当日代码列表。首/尾三分之一改为贴边对齐（审计 2.4.6）：
                      纯居中在窄屏上会把浮层伸出视口，Layout 的 overflow-x-clip
                      只裁不滚，看不全当天的代码。 */}
                  <span
                    className={cn(
                      'cloud-popover pointer-events-none absolute -top-2 z-20 hidden w-max max-w-[180px] -translate-y-full px-2.5 py-1.5 text-left group-hover:block group-focus-visible:block',
                      i < days.length / 3
                        ? 'left-0'
                        : i >= (days.length * 2) / 3
                          ? 'right-0'
                          : 'left-1/2 -translate-x-1/2',
                    )}
                  >
                    <span className="block font-mono text-[10px] text-ink-500">
                      {fmtMMDD(d.date)} {weekdayCN(d.date)}
                    </span>
                    {n === 0 ? (
                      <span className="block text-[10px] text-ink-300">{t('无财报')}</span>
                    ) : (
                      <span className="mt-0.5 flex flex-wrap gap-1">
                        {d.rows.slice(0, MAX_TOOLTIP_TICKERS).map((r) => (
                          <span key={r.ticker} className="font-mono text-[10px] font-semibold text-ink-800">
                            {r.ticker}
                          </span>
                        ))}
                        {n > MAX_TOOLTIP_TICKERS && (
                          <span className="font-mono text-[10px] text-ink-400">
                            +{n - MAX_TOOLTIP_TICKERS}
                          </span>
                        )}
                      </span>
                    )}
                  </span>
                  <motion.span
                    className={cn(
                      'w-full rounded-t-[2px] transition-colors duration-fast',
                      n > 0 ? 'bg-brand-400 group-hover:bg-brand-600' : 'bg-line',
                      isToday && 'ring-1 ring-brand-600 ring-offset-1',
                    )}
                    style={{ height: n > 0 ? `${Math.max(12, (n / max) * 100)}%` : '2px' }}
                    initial={{ scaleY: 0, transformOrigin: 'bottom' }}
                    whileInView={{ scaleY: 1 }}
                    viewport={{ once: true, amount: 0.4 }}
                    transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.02 }}
                  />
                </button>
                </span>
              );
            })}
          </div>
          <div className="mt-1.5 flex justify-between font-mono text-[9px] text-ink-300">
            <span>{t('今天')}</span>
            <span>{t('+15 天')}</span>
            <span>{t('+30 天')}</span>
          </div>
        </div>
        <SourceNote className="mt-4" text={t("财报日程 · 以公司公告为准")} />
      </div>
    </section>
  );
}
