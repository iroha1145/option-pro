/**
 * B1 美东周历 scrubber（earnings.md）
 * 周一→周日 7 日格 · 每日财报数量 + 前 3 个 ticker chips（sun-bmo/moon-amc 12px）
 * 点击日格过滤当天；点击 chip 联动 B2/B3；‹ › 周切换整带 slide 300ms
 * 动效：日格 stagger 35ms rise-in · chips pop 错峰 20ms · 刷新 tick-flash
 */
import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import type { EarningsRow } from './types';
import { etToday, fmtMDCN, fmtMMDD, weekDays, weekdayCN } from './types';
import { t as __t } from '../../i18n/core.ts';

interface WeekScrubberProps {
  items: EarningsRow[];
  monday: string;
  weekDir: number;
  onWeekChange: (dir: -1 | 1) => void;
  selectedDay: string | null;
  selectedTicker: string | null;
  onSelectDay: (date: string | null) => void;
  onSelectTicker: (ticker: string, date: string) => void;
  flashSignal: number;
}

const MAX_CHIPS = 3;

const timingLabel = (timing: EarningsRow['timing']) => (
  timing === 'bmo' ? __t('盘前') : timing === 'amc' ? __t('盘后') : __t('时间待定')
);

export default function WeekScrubber({
  items,
  monday,
  weekDir,
  onWeekChange,
  selectedDay,
  selectedTicker,
  onSelectDay,
  onSelectTicker,
  flashSignal,
}: WeekScrubberProps) {
  const days = useMemo(() => weekDays(monday), [monday]);
  const today = etToday();

  const byDate = useMemo(() => {
    const m = new Map<string, EarningsRow[]>();
    for (const it of items) {
      const arr = m.get(it.date) ?? [];
      arr.push(it);
      m.set(it.date, arr);
    }
    return m;
  }, [items]);

  /* 刷新成功 → B1 tick-flash 600ms（信号变化渲染期置位，定时器异步复位） */
  const [flashing, setFlashing] = useState(false);
  const [prevFlashSignal, setPrevFlashSignal] = useState(flashSignal);
  if (flashSignal !== prevFlashSignal) {
    setPrevFlashSignal(flashSignal);
    if (flashSignal > 0) setFlashing(true);
  }
  useEffect(() => {
    if (!flashing) return;
    const t = setTimeout(() => setFlashing(false), 700);
    return () => clearTimeout(t);
  }, [flashing]);

  return (
    <section className="card-surface overflow-hidden" aria-label={__t("周历")}>
      {/* 周切换条 */}
      <div className="flex h-11 items-center justify-between border-b border-line px-4">
        <button
          onClick={() => onWeekChange(-1)}
          className="flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ink-500 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          aria-label={__t("上一周")}
        >
          <Icon name="chevron-right" size={14} className="rotate-180" />
        </button>
        <p className="font-mono text-caption text-ink-600 tnum" aria-live="polite">
          {fmtMDCN(days[0])} – {fmtMDCN(days[6])}
          <span className="ml-2 font-sans text-micro text-ink-400">{__t('美东 ET')}</span>
        </p>
        <button
          onClick={() => onWeekChange(1)}
          className="flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ink-500 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          aria-label={__t("下一周")}
        >
          <Icon name="chevron-right" size={14} />
        </button>
      </div>

      {/* 7 日格：移动横滑 snap（每格 72px+）+ 边缘渐隐；桌面 7 等分 */}
      <div className="relative">
        <div
          className="pointer-events-none absolute inset-y-0 left-0 z-10 w-6 bg-gradient-to-r from-card to-transparent sm:hidden"
          aria-hidden="true"
        />
        <div
          className="pointer-events-none absolute inset-y-0 right-0 z-10 w-6 bg-gradient-to-l from-card to-transparent sm:hidden"
          aria-hidden="true"
        />
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.div
            key={monday}
            initial={{ x: weekDir * 44, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: weekDir * -44, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="flex snap-x snap-mandatory overflow-x-auto no-scrollbar sm:grid sm:grid-cols-7 sm:overflow-visible"
          >
            {days.map((date, di) => {
              const dayItems = byDate.get(date) ?? [];
              const isToday = date === today;
              const isSelected = selectedDay === date;
              const shown = dayItems.slice(0, MAX_CHIPS);
              const extra = dayItems.length - shown.length;
              return (
                <motion.div
                  key={date}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                  aria-label={__t('{weekday} {md}，{n} 条财报', { weekday: weekdayCN(date), md: fmtMMDD(date), n: dayItems.length })}
                  onClick={() => onSelectDay(isSelected ? null : date)}
                  onKeyDown={(e) => {
                    if (e.target !== e.currentTarget) return; // 格内代码 chip 的键盘激活不拦截
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelectDay(isSelected ? null : date);
                    }
                  }}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: di * 0.035 }}
                  className={cn(
                    'flex min-h-[148px] w-[86px] shrink-0 cursor-pointer snap-start flex-col border-r border-line px-2 py-2.5 text-left transition-colors duration-fast last:border-r-0 sm:w-auto sm:min-w-0',
                    isSelected ? 'bg-brand-50' : 'hover:bg-paper-2',
                    flashing && dayItems.length > 0 && 'animate-tick-flash-up',
                  )}
                >
                  {/* 日期头 */}
                  <div className="flex items-center justify-between">
                    <span className={cn('text-caption', isToday ? 'font-semibold text-brand-600' : 'text-ink-500')}>
                      {weekdayCN(date)}
                    </span>
                    {isToday && <span className="size-1.5 rounded-full bg-brand-600" aria-label={__t("今天")} />}
                  </div>
                  <span className={cn('font-mono text-micro tnum', isToday ? 'text-brand-600' : 'text-ink-400')}>
                    {fmtMMDD(date)}
                  </span>

                  {/* 财报 chips 竖堆（前 3 个 + 折叠 +N） */}
                  <div className="mt-2 flex flex-1 flex-col gap-1">
                    {dayItems.length === 0 ? (
                      <span className="pt-1 font-mono text-micro text-ink-300" aria-hidden="true">
                        —
                      </span>
                    ) : (
                      <>
                        {shown.map((it, ci) => {
                          const active = selectedTicker === it.ticker && selectedDay === date;
                          return (
                            <motion.button
                              key={it.ticker}
                              initial={{ opacity: 0, scale: 0.9 }}
                              animate={{ opacity: 1, scale: 1 }}
                              transition={{ type: 'spring', stiffness: 520, damping: 32, delay: di * 0.035 + ci * 0.02 }}
                              onClick={(e) => {
                                e.stopPropagation();
                                onSelectTicker(it.ticker, date);
                              }}
                              aria-label={__t('{ticker} {timing}财报，查看 AI 影响', { ticker: it.ticker, timing: timingLabel(it.timing) })}
                              className={cn(
                                'flex h-6 items-center gap-1 rounded-xs border-l-2 px-1 transition-[transform,background-color] duration-fast hover:-translate-y-px',
                                active
                                  ? 'border-brand-600 bg-brand-100'
                                  : 'border-transparent bg-paper-2 hover:bg-brand-50',
                              )}
                            >
                              <Icon
                                name={it.timing === 'bmo' ? 'sun-bmo' : it.timing === 'amc' ? 'moon-amc' : 'clock-ny'}
                                size={12}
                                className={it.timing === 'bmo' ? 'text-warn-600' : it.timing === 'amc' ? 'text-ai-600' : 'text-ink-400'}
                              />
                              <span className="font-mono text-micro font-medium text-ink-800">{it.ticker}</span>
                            </motion.button>
                          );
                        })}
                        {extra > 0 && (
                          <span className="px-1 font-mono text-[10px] leading-4 text-ink-400">+{extra}</span>
                        )}
                      </>
                    )}
                  </div>

                  {/* 每日数量 */}
                  <span className="mt-1 font-mono text-[10px] leading-4 text-ink-300 tnum">
                    {dayItems.length > 0 ? __t('{n} 条', { n: dayItems.length }) : ''}
                  </span>
                </motion.div>
              );
            })}
          </motion.div>
        </AnimatePresence>
      </div>
    </section>
  );
}
