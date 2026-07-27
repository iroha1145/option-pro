/**
 * B2 即将公布表（earnings.md）· 按日期分组
 * 行：TickerLogo+代码/名称 · 时间（sun-bmo 盘前 warn-600 / moon-amc 盘后 ai-600）
 *     EPS 迷你斜纹柱对（预估斜纹 ink-400 / 实际实心 brand-600）· 营收预期 · 市值
 *     预期波动仅在至少一条真实数值存在时出现 · AI 影响钮
 * days_until=0「今天」高亮 · 行 stagger 40ms · 斜纹柱对 grow 错峰 700ms · <md 转卡片流
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtCompact } from '@/lib/format';
import Icon from '@/components/icons';
import TickerLogo from '@/components/shared/TickerLogo';
import EmptyState from '@/components/shared/EmptyState';
import type { EarningsRow } from './types';
import { daysUntil, exBool, exNum, exStr, fmtMDCN, relativeDayCN, weekdayCN } from './types';
import { t } from '../../i18n/core.ts';

/* ---------------- 迷你斜纹柱对（48px，预估 45° 斜纹 / 实际实心） ---------------- */
function EpsPairBars({ est, act, index }: { est: number | null; act: number | null; index: number }) {
  const max = Math.max(Math.abs(est ?? 0), Math.abs(act ?? 0), 0.01);
  const h = (v: number | null) => (v == null ? 0 : Math.max(10, (Math.abs(v) / max) * 26));
  return (
    <span className="flex h-7 w-12 items-end justify-center gap-1" aria-hidden="true">
      <motion.span
        className="w-2.5 rounded-t-[2px] border border-ink-300/70"
        style={{
          height: h(est),
          backgroundImage: 'repeating-linear-gradient(45deg, rgba(138,148,176,.55) 0 1.2px, transparent 1.2px 4px)',
        }}
        initial={{ scaleY: 0 }}
        whileInView={{ scaleY: 1 }}
        viewport={{ once: true, amount: 0.5 }}
        transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: index * 0.05 }}
      />
      {act != null && (
        <motion.span
          className="w-2.5 origin-bottom rounded-t-[2px] bg-brand-600"
          style={{ height: h(act) }}
          initial={{ scaleY: 0 }}
          whileInView={{ scaleY: 1 }}
          viewport={{ once: true, amount: 0.5 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: index * 0.05 + 0.08 }}
        />
      )}
    </span>
  );
}

/* ---------------- 时间徽章（盘前太阳 / 盘后月亮） ---------------- */
export function TimingBadge({ timing, className }: { timing: EarningsRow['timing']; className?: string }) {
  if (timing == null) {
    return (
      <span className={cn('inline-flex items-center gap-1.5 text-ink-400', className)} aria-label={t("公布时间待定")}>
        <Icon name="clock-ny" size={14} />
        <span className="text-caption">{t('时间待定')}</span>
      </span>
    );
  }
  const bmo = timing === 'bmo';
  return (
    <span
      className={cn('inline-flex items-center gap-1.5', bmo ? 'text-warn-600' : 'text-ai-600', className)}
      aria-label={bmo ? t('盘前公布') : t('盘后公布')}
    >
      <Icon name={bmo ? 'sun-bmo' : 'moon-amc'} size={14} />
      <span className="text-caption">{bmo ? t('盘前') : t('盘后')}</span>
    </span>
  );
}

/* ---------------- 预期波动微条（0–15% 映射 ai-600） ---------------- */
function ExpectedMoveCell({ pct, index }: { pct: number | null; index: number }) {
  if (pct == null) return <span aria-hidden="true" />;
  return (
    <span className="block">
      <span className="font-mono text-data-m text-ink-800 tnum">±{pct.toFixed(1)}%</span>
      <span className="mt-1 block h-1 w-16 overflow-hidden rounded-pill bg-line" aria-hidden="true">
        <motion.span
          className="block h-full origin-left rounded-pill bg-ai-600"
          initial={{ scaleX: 0 }}
          whileInView={{ scaleX: 1 }}
          viewport={{ once: true, amount: 0.6 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: index * 0.05 }}
          style={{ width: `${Math.min(100, (pct / 15) * 100)}%` }}
        />
      </span>
    </span>
  );
}

/* ---------------- AI 影响操作钮 ---------------- */
function ImpactAction({ row, onSelect }: { row: EarningsRow; onSelect: () => void }) {
  const ready = exBool(row, 'impactReady');
  const locked = exBool(row, 'locked') === true || exBool(row, 'final') === true;
  // 只信后端算出的「确实有最终分析在跑」。原先只要阶段是 post_release_final
  // 就写「重分析中」，一条失败或被预算挡下的最终任务会让这一行永远显示在跑。
  const finalizing = exBool(row, 'finalizationInProgress') === true;
  /* 这一列只有 96px：标签必须短且单行，四个汉字会折行把 h-7 撑破。
     列头已经写着「AI 影响」，按钮不必再重复一遍，状态交给配色区分。 */
  const [label, title] = locked
    ? [t('最终'), t('查看最终分析')]
    : finalizing
      ? [t('分析中'), t('最终分析生成中')]
      : ready === true
        ? [t('查看'), t('查看 AI 影响分析')]
        : ready === false
          ? [t('分析'), t('生成 AI 影响分析')]
          : [t('AI 影响'), t('AI 影响分析')];
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      title={title}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border px-2 text-caption leading-none transition-colors duration-fast',
        locked
          ? 'border-ai-600 bg-ai-600 text-white hover:brightness-110'
          : ready === true || finalizing
            ? 'border-ai-600/40 bg-ai-50 text-ai-600 hover:bg-ai-600 hover:text-white'
            : 'border-line bg-card text-ink-500 hover:border-ai-600/50 hover:text-ai-600',
      )}
      aria-label={`${row.ticker} ${title}`}
    >
      <Icon name="spark-ai" size={12} />
      {label}
    </button>
  );
}

/* ---------------- 主组件 ---------------- */
interface EarningsListProps {
  items: EarningsRow[];
  selectedTicker: string | null;
  onSelectTicker: (ticker: string) => void;
  onNextWeek?: () => void;
  filteredByDay: boolean;
}

export default function EarningsList({ items, selectedTicker, onSelectTicker, onNextWeek, filteredByDay }: EarningsListProps) {
  if (items.length === 0) {
    return (
      <section className="card-surface" aria-label={t("即将公布")}>
        <EmptyState
          image="/empty-chart.svg"
          title={filteredByDay ? t('当日无财报') : t('本周清淡')}
          description={filteredByDay ? t('选中的日期没有财报安排，切换日格或查看下周。') : t('本周期没有财报安排，跳到下周看看。')}
          action={
            onNextWeek ? (
              <button
                onClick={onNextWeek}
                className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
              >
                {t('查看下周')}
                <Icon name="chevron-right" size={13} />
              </button>
            ) : undefined
          }
        />
      </section>
    );
  }

  const hasExpectedMove = items.some((row) => exNum(row, 'expectedMovePct') != null);
  /* 末列 96px：容得下「AI 影响」这一最宽标签且不折行（原 88px 会折） */
  const gridColumns = hasExpectedMove
    ? 'md:grid-cols-[minmax(150px,1.4fr)_84px_minmax(140px,1.2fr)_96px_96px] 2xl:grid-cols-[minmax(160px,1.4fr)_84px_minmax(150px,1.2fr)_96px_92px_96px_96px]'
    : 'md:grid-cols-[minmax(150px,1.4fr)_84px_minmax(140px,1.2fr)_96px] 2xl:grid-cols-[minmax(160px,1.4fr)_84px_minmax(150px,1.2fr)_96px_92px_96px]';

  /* 按日期分组（升序） */
  const groups: { date: string; rows: EarningsRow[] }[] = [];
  for (const it of items) {
    const g = groups[groups.length - 1];
    if (g && g.date === it.date) g.rows.push(it);
    else groups.push({ date: it.date, rows: [it] });
  }

  let rowIndex = -1;

  return (
    <section
      className="card-surface overflow-hidden md:max-h-[min(72vh,880px)] md:overflow-y-auto md:overscroll-contain [scrollbar-gutter:stable]"
      aria-label={t("即将公布")}
    >
      {/* 桌面列头（≥md） */}
      <div
        className={cn(
          'sticky top-0 z-20 hidden border-b border-line bg-card-warm px-4 py-2.5 md:grid md:gap-3',
          gridColumns,
        )}
      >
        <span className="eyebrow">{t('代码')}</span>
        <span className="eyebrow">{t('时间')}</span>
        <span className="eyebrow">{t('EPS 预期 vs 实际')}</span>
        <span className="eyebrow hidden 2xl:block">{t('营收预期')}</span>
        <span className="eyebrow hidden 2xl:block">{t('市值')}</span>
        {hasExpectedMove && <span className="eyebrow">{t('预期波动')}</span>}
        <span className="eyebrow text-right">{t('AI 影响')}</span>
      </div>

      {groups.map((g) => {
        const du = daysUntil(g.date);
        const isToday = du === 0;
        return (
          <div key={g.date}>
            {/* 日期分组头：Serif 日期 + 相对日 + 数量；今天高亮 */}
            <div
              className={cn(
                'flex items-baseline justify-between border-b border-line px-4 py-2.5',
                isToday ? 'bg-brand-50' : 'bg-card-warm/60',
              )}
            >
              <p className="flex items-baseline gap-2.5">
                <span className={cn('font-display text-[15px] leading-6', isToday ? 'text-brand-700' : 'text-ink-800')}>
                  {fmtMDCN(g.date)} · {weekdayCN(g.date)}
                </span>
                {isToday && (
                  <span className="rounded-xs bg-brand-600 px-1.5 py-px text-[10px] font-semibold leading-4 text-white">{t('今天')}</span>
                )}
              </p>
              <p className="font-mono text-micro text-ink-400 tnum">
                {relativeDayCN(g.date)} · {g.rows.length} {t('条')}
              </p>
            </div>

            {/* 行（≥md 表格行 / <md 卡片） */}
            {g.rows.map((row) => {
              rowIndex += 1;
              const i = rowIndex;
              const selected = selectedTicker === row.ticker;
              const est = row.epsEstimate;
              const act = row.epsActual;
              const sector = exStr(row, 'sector');
              // 市值必须是数据源提供的正数；0/负数是缺失占位，不展示成 $0。
              const marketCap = exNum(row, 'marketCap');
              const move = exNum(row, 'expectedMovePct');
              return (
                <div key={row.ticker}>
                  {/* 桌面行 */}
                  <motion.div
                    role="button"
                    tabIndex={0}
                    aria-pressed={selected}
                    onClick={() => onSelectTicker(row.ticker)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        onSelectTicker(row.ticker);
                      }
                    }}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: Math.min(i * 0.04, 0.6) }}
                    className={cn(
                      'hidden cursor-pointer items-center border-b border-line px-4 py-3 transition-colors duration-fast last:border-b-0 md:grid md:gap-3',
                      gridColumns,
                      selected ? 'bg-brand-50' : 'hover:bg-paper-2',
                    )}
                  >
                    {/* 代码 */}
                    <span className="flex min-w-0 items-center gap-2.5">
                      <TickerLogo ticker={row.ticker} />
                      <span className="min-w-0">
                        <span className="block font-mono text-body-s font-semibold text-ink-800">{row.ticker}</span>
                        <span className="block max-w-[180px] truncate text-micro text-ink-400">
                          {row.name}
                          {sector ? ` · ${sector}` : ''}
                        </span>
                      </span>
                    </span>
                    {/* 时间 */}
                    <TimingBadge timing={row.timing} />
                    {/* EPS 预期 vs 实际 */}
                    <span className="flex items-center gap-2">
                      <EpsPairBars est={est} act={act} index={i} />
                      <span className="font-mono text-data-m tnum">
                        <span className="text-ink-500">{est != null ? est.toFixed(2) : '—'}</span>
                        <span className="mx-1 text-ink-300">/</span>
                        <span className={act != null ? 'font-semibold text-ink-900' : 'text-ink-300'}>
                          {act != null ? act.toFixed(2) : t('未公布')}
                        </span>
                      </span>
                    </span>
                    {/* 营收预期 */}
                    <span className="hidden font-mono text-data-m text-ink-600 tnum 2xl:block">
                      {row.revEstimate != null ? `$${fmtCompact(row.revEstimate)}` : '—'}
                    </span>
                    {/* 市值 */}
                    <span className="hidden font-mono text-data-m text-ink-600 tnum 2xl:block">
                      {marketCap != null ? `$${fmtCompact(marketCap)}` : '—'}
                    </span>
                    {hasExpectedMove && <ExpectedMoveCell pct={move} index={i} />}
                    {/* AI 影响 */}
                    <span className="flex justify-end">
                      <ImpactAction row={row} onSelect={() => onSelectTicker(row.ticker)} />
                    </span>
                  </motion.div>

                  {/* 移动卡片（<md） */}
                  <motion.button
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: Math.min(i * 0.04, 0.6) }}
                    onClick={() => onSelectTicker(row.ticker)}
                    aria-pressed={selected}
                    className={cn(
                      'block w-full border-b border-line px-4 py-3 text-left transition-colors last:border-b-0 md:hidden',
                      selected ? 'bg-brand-50' : 'hover:bg-paper-2',
                    )}
                  >
                    <span className="flex items-center gap-2.5">
                      <TickerLogo ticker={row.ticker} size={28} />
                      <span className="min-w-0 flex-1">
                        <span className="block font-mono text-body-s font-semibold text-ink-800">{row.ticker}</span>
                        <span className="block truncate text-micro text-ink-400">
                          {row.name}
                          {sector ? ` · ${sector}` : ''}
                        </span>
                      </span>
                      <TimingBadge timing={row.timing} />
                    </span>
                    <span className="mt-2.5 flex items-center justify-between gap-3">
                      <span className="flex items-center gap-2">
                        <EpsPairBars est={est} act={act} index={i} />
                        <span className="font-mono text-micro tnum">
                          <span className="text-ink-500">{est != null ? est.toFixed(2) : '—'}</span>
                          <span className="mx-1 text-ink-300">/</span>
                          <span className={act != null ? 'text-ink-900' : 'text-ink-300'}>
                            {act != null ? act.toFixed(2) : t('未公布')}
                          </span>
                        </span>
                      </span>
                      {hasExpectedMove && <ExpectedMoveCell pct={move} index={i} />}
                    </span>
                  </motion.button>
                </div>
              );
            })}
          </div>
        );
      })}
    </section>
  );
}
