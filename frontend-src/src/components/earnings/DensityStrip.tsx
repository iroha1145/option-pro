/**
 * B5 补充带：本月财报密度条（earnings.md）
 * 30 天横向迷你柱（每日财报数，brand-400，grow-bar 错峰）
 * hover 日 → 当日代码列表 tooltip；点击日 → 跳转该周并选中日格
 * 右侧 SourceNote「预期数据来源：Optix Research 共识汇总 ◆」
 */
import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import SourceNote from '@/components/shared/SourceNote';
import type { EarningsRow } from './types';
import { addDays, etToday, fmtMDCN, fmtMMDD, weekdayCN } from './types';

interface DensityStripProps {
  items: EarningsRow[];
  onJumpDay: (date: string) => void;
}

const DAYS = 30;

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
    <section className="card-surface p-5" aria-label="本月财报密度">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="min-w-0 flex-1">
          <p className="eyebrow">本月财报密度 · 未来 30 天</p>
          <div className="mt-3 flex h-16 items-end gap-[3px]" role="list" aria-label="每日财报数量">
            {days.map((d, i) => {
              const n = d.rows.length;
              const isToday = i === 0;
              return (
                <button
                  key={d.date}
                  role="listitem"
                  onClick={() => onJumpDay(d.date)}
                  aria-label={`${fmtMDCN(d.date)} ${weekdayCN(d.date)}，${n} 条财报，跳转`}
                  className="group relative flex h-full min-w-0 flex-1 items-end"
                >
                  {/* tooltip：当日代码列表 */}
                  <span className="glass pointer-events-none absolute -top-2 left-1/2 z-20 hidden w-max max-w-[180px] -translate-x-1/2 -translate-y-full rounded-md border border-line px-2.5 py-1.5 text-left shadow-sh-2 group-hover:block">
                    <span className="block font-mono text-[10px] text-ink-500">
                      {fmtMMDD(d.date)} {weekdayCN(d.date)}
                    </span>
                    {n === 0 ? (
                      <span className="block text-[10px] text-ink-300">无财报</span>
                    ) : (
                      <span className="mt-0.5 flex flex-wrap gap-1">
                        {d.rows.map((r) => (
                          <span key={r.ticker} className="font-mono text-[10px] font-semibold text-ink-800">
                            {r.ticker}
                          </span>
                        ))}
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
              );
            })}
          </div>
          <div className="mt-1.5 flex justify-between font-mono text-[9px] text-ink-300">
            <span>今天</span>
            <span>+15 天</span>
            <span>+30 天</span>
          </div>
        </div>
        <SourceNote className="shrink-0 border-t-0 pt-0" text="预期数据来源：Optix Research 共识汇总" />
      </div>
    </section>
  );
}
