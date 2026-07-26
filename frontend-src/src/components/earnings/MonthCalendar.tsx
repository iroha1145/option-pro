/**
 * 月历大界面（周历条的可展开形态，类手机日历）
 * 头部：‹ › 上月/本月/下月连续切换（方向感知 slide 260ms ease-paper）· Serif 月标题 ·「今天」快捷钮
 * 网格：周一→周日 7 列 × 5–6 行；上/下月溢出日期 ink-300 淡显、本月 ink-800；
 *      今天 brand 圆底+圆点、选中 brand-50 底、周末淡底；无财报日期留白
 * 格内财报 chips ≤3（ticker + sun-bmo/moon-amc，盘前 warn-600 / 盘后 ai-600），超出「+N」；
 * <sm 收缩为色点 + 计数徽章（明细在下方列表查看）
 * 点击日期 → 选中并联动 B2 日过滤（再点取消）；格 stagger 25ms rise-in（仅首次进场）
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import type { EarningsRow } from './types';
import { addDays, etToday, fmtMDCN, weekStartMonday } from './types';
import { t } from '../../i18n/core.ts';

interface MonthCalendarProps {
  items: EarningsRow[];
  selectedDay: string | null;
  selectedTicker: string | null;
  onSelectDay: (date: string | null) => void;
  onSelectTicker: (ticker: string, date: string) => void;
}

/* 完整「周X」作为独立词条（而不是「周」+ 单字拼接）：英文 Mon–Sun、日文 月–日
   各自是自足的星期缩写，前缀拼接会拼出 "WeekMon"/"週月" 这种破损文案。 */
const WEEKDAYS = [t('周一'), t('周二'), t('周三'), t('周四'), t('周五'), t('周六'), t('周日')] as const;
const MAX_CHIPS = 3;
const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

const timingLabel = (timing: EarningsRow['timing']) => (
  timing === 'bmo' ? t('盘前') : timing === 'amc' ? t('盘后') : t('时间待定')
);

/** 'YYYY-MM' 加减月（UTC 锚定） */
function shiftMonth(key: string, delta: number): string {
  const y = Number(key.slice(0, 4));
  const m = Number(key.slice(5, 7)) - 1 + delta;
  return new Date(Date.UTC(y, m, 1)).toISOString().slice(0, 7);
}

/** 'YYYY-MM' → '2026年7月' */
function monthTitleCN(key: string): string {
  return `${Number(key.slice(0, 4))} 年 ${Number(key.slice(5, 7))} 月`;
}

export default function MonthCalendar({
  items,
  selectedDay,
  selectedTicker,
  onSelectDay,
  onSelectTicker,
}: MonthCalendarProps) {
  const today = etToday();
  const currentMonth = today.slice(0, 7);
  const minMonth = shiftMonth(currentMonth, -1);
  const maxMonth = shiftMonth(currentMonth, 1);

  const [cursor, setCursor] = useState(currentMonth);
  const [dir, setDir] = useState(0);

  const byDate = useMemo(() => {
    const m = new Map<string, EarningsRow[]>();
    for (const it of items) {
      const arr = m.get(it.date) ?? [];
      arr.push(it);
      m.set(it.date, arr);
    }
    return m;
  }, [items]);

  /* 网格：月首所在周的周一起，5–6 行 × 7 列 */
  const cells = useMemo(() => {
    const first = `${cursor}-01`;
    const start = weekStartMonday(first);
    const y = Number(cursor.slice(0, 4));
    const m = Number(cursor.slice(5, 7));
    const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const offset = Math.round((Date.parse(`${first}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000);
    const rows = Math.ceil((offset + daysInMonth) / 7);
    return Array.from({ length: rows * 7 }, (_, i) => addDays(start, i));
  }, [cursor]);

  /* 格 stagger 25ms 仅首次进场；月份切换时整带 slide，不再逐格错峰 */
  const enteredRef = useRef(false);
  useEffect(() => {
    enteredRef.current = true;
  }, []);

  const goMonth = (delta: -1 | 1) => {
    const next = shiftMonth(cursor, delta);
    if (next < minMonth || next > maxMonth) return;
    setDir(delta);
    setCursor(next);
  };

  const goToday = () => {
    if (cursor !== currentMonth) {
      setDir(cursor < currentMonth ? 1 : -1);
      setCursor(currentMonth);
    }
    onSelectDay(today);
  };

  return (
    <section className="card-surface overflow-hidden" aria-label={t("月历")}>
      {/* 头部：‹ › + Serif 月标题 + 今天 */}
      <div className="flex h-12 items-center justify-between border-b border-line px-4">
        <button
          onClick={() => goMonth(-1)}
          disabled={cursor <= minMonth}
          className="flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ink-500 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-line disabled:hover:text-ink-500"
          aria-label={t("上个月")}
        >
          <Icon name="chevron-right" size={14} className="rotate-180" />
        </button>
        <div className="flex items-center gap-3">
          <p className="font-display text-h3 text-ink-900" aria-live="polite">
            {monthTitleCN(cursor)}
          </p>
          <button
            onClick={goToday}
            className="rounded-sm border border-line bg-card px-2 py-1 text-caption text-ink-500 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          >
            {t('今天')}
          </button>
        </div>
        <button
          onClick={() => goMonth(1)}
          disabled={cursor >= maxMonth}
          className="flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ink-500 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-line disabled:hover:text-ink-500"
          aria-label={t("下个月")}
        >
          <Icon name="chevron-right" size={14} />
        </button>
      </div>

      {/* 星期头（周一→周日，周末淡显） */}
      <div className="grid grid-cols-7 border-b border-line bg-card-warm/60">
        {WEEKDAYS.map((w, i) => (
          <span
            key={w}
            className={cn('py-1.5 text-center font-mono text-micro', i >= 5 ? 'text-ink-300' : 'text-ink-400')}
          >
            {t(w)}
          </span>
        ))}
      </div>

      {/* 月网格：方向感知 slide 260ms ease-paper */}
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.div
          key={cursor}
          initial={{ x: dir * 36, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: dir * -36, opacity: 0 }}
          transition={{ duration: 0.26, ease: EASE_PAPER }}
          className="grid grid-cols-7"
        >
          {cells.map((date, ci) => {
            const inMonth = date.slice(0, 7) === cursor;
            const isToday = date === today;
            const isSelected = selectedDay === date;
            const dow = new Date(`${date}T12:00:00Z`).getUTCDay();
            const isWeekend = dow === 0 || dow === 6;
            const isLastRow = ci >= cells.length - 7;
            const dayItems = byDate.get(date) ?? [];
            const shown = dayItems.slice(0, MAX_CHIPS);
            const extra = dayItems.length - shown.length;
            const dayNum = Number(date.slice(8, 10));
            const label = date.slice(8, 10) === '01' ? `${Number(date.slice(5, 7))} 月 ${dayNum} 日` : String(dayNum);
            return (
              <motion.div
                key={date}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                aria-label={`${fmtMDCN(date)}，${dayItems.length} 条财报`}
                onClick={() => onSelectDay(isSelected ? null : date)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelectDay(isSelected ? null : date);
                  }
                }}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.48, ease: EASE_PAPER, delay: enteredRef.current ? 0 : ci * 0.025 }}
                className={cn(
                  'flex min-h-[64px] cursor-pointer flex-col border-r border-line p-1.5 text-left transition-colors duration-fast sm:min-h-[104px] sm:p-2',
                  '[&:nth-child(7n)]:border-r-0',
                  !isLastRow && 'border-b',
                  isSelected ? 'bg-brand-50' : isWeekend ? 'bg-paper-2/70 hover:bg-paper-2' : 'hover:bg-paper-2',
                )}
              >
                {/* 日期号：今天 brand 圆底+圆点；1 日带月份前缀；溢出月淡显 */}
                <div className="flex items-center justify-between">
                  <span
                    className={cn(
                      'font-mono text-micro tnum',
                      isToday
                        ? 'flex h-6 min-w-6 items-center justify-center rounded-full bg-brand-600 px-1 font-semibold text-white'
                        : inMonth
                          ? 'text-ink-800'
                          : 'text-ink-300',
                    )}
                  >
                    {label}
                  </span>
                  {isToday && <span className="size-1.5 rounded-full bg-brand-600" aria-label={t("今天")} />}
                </div>

                {/* 桌面 chips（≤3 + 折叠 +N），无财报留白 */}
                <div className="mt-1.5 hidden flex-1 flex-col gap-1 sm:flex">
                  {shown.map((it) => {
                    const active = selectedTicker === it.ticker && selectedDay === date;
                    return (
                      <motion.button
                        key={it.ticker}
                        whileTap={{ scale: 0.96 }}
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectTicker(it.ticker, date);
                        }}
                        aria-label={`${it.ticker} ${timingLabel(it.timing)}财报，查看 AI 影响`}
                        className={cn(
                          'flex h-5 items-center gap-1 rounded-xs px-1 transition-colors duration-fast',
                          active ? 'bg-brand-100' : 'hover:bg-brand-50',
                        )}
                      >
                        <Icon
                          name={it.timing === 'bmo' ? 'sun-bmo' : it.timing === 'amc' ? 'moon-amc' : 'clock-ny'}
                          size={11}
                          className={it.timing === 'bmo' ? 'text-warn-600' : it.timing === 'amc' ? 'text-ai-600' : 'text-ink-400'}
                        />
                        <span className="truncate font-mono text-[10px] font-medium leading-4 text-ink-800">
                          {it.ticker}
                        </span>
                      </motion.button>
                    );
                  })}
                  {extra > 0 && <span className="px-1 font-mono text-[10px] leading-4 text-ink-400 tnum">+{extra}</span>}
                </div>

                {/* 移动端：色点 + 计数徽章（类手机日历） */}
                <div className="mt-1 flex flex-1 items-end sm:hidden">
                  {dayItems.length > 0 && (
                    <span className="flex items-center gap-1" aria-hidden="true">
                      <span className="flex gap-0.5">
                        {shown.map((it, i) => (
                          <span
                            key={`${it.ticker}-${i}`}
                            className={cn(
                              'size-1.5 rounded-full',
                              it.timing === 'bmo' ? 'bg-warn-600' : it.timing === 'amc' ? 'bg-ai-600' : 'bg-ink-300',
                            )}
                          />
                        ))}
                      </span>
                      <span className="rounded-pill bg-line/70 px-1 font-mono text-[10px] leading-4 text-ink-600 tnum">
                        {dayItems.length}
                      </span>
                    </span>
                  )}
                </div>
              </motion.div>
            );
          })}
        </motion.div>
      </AnimatePresence>
    </section>
  );
}
