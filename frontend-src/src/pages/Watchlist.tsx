/**
 * §01 自选观察（watchlist.md 完整实现）
 * B0 页头带 · B1 概览统计（count-up）· B2 可排序表格/卡片（tick-flash）· B3 侧栏（信号/强度分布/市场时钟）
 * 轮询 60s · 空态 / 骨架 / 503 · 响应式
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { motion } from 'framer-motion';
import { stocksApi } from '@/api/modules/stocks';
import { signalsApi } from '@/api/modules/signals';
import { strengthApi } from '@/api/modules/strength';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { useAccess } from '@/hooks/useAccess';
import { useNow } from '@/hooks/useNow';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { cn } from '@/lib/utils';
import { fmtCountdown, fmtNyTime, fmtPrice, fmtTimeHHMMSS } from '@/lib/format';
import type { MarketSignalsSummary, WatchlistItem } from '@/api/types';
import PageHeader from '@/components/shared/PageHeader';
import StatCard from '@/components/shared/StatCard';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import StrengthBar from '@/components/shared/StrengthBar';
import SignalChip from '@/components/shared/SignalChip';
import Segmented from '@/components/shared/Segmented';
import DataTable, { type Column, type SortState } from '@/components/shared/DataTable';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import SessionLED, { SessionDot } from '@/components/shared/SessionLED';
import HatchLegend from '@/components/shared/HatchLegend';
import { SkeletonCard, SkeletonRows } from '@/components/shared/Skeleton';
import Sparkline from '@/components/charts/Sparkline';
import Icon from '@/components/icons';

/* ---------------- B1 小件：涨跌宽度比条 ---------------- */
function AdvanceDeclineBar({ advancers, decliners }: { advancers: number; decliners: number }) {
  const total = Math.max(1, advancers + decliners);
  return (
    <div className="mt-2">
      <p className="font-mono text-data-xl tnum">
        <span className="text-up-700">{advancers}</span>
        <span className="mx-1.5 text-ink-300">/</span>
        <span className="text-down-700">{decliners}</span>
      </p>
      <div className="mt-2 flex h-1.5 w-full overflow-hidden rounded-pill bg-line" aria-hidden="true">
        <div className="h-full origin-left animate-grow-bar bg-up-600" style={{ width: `${(advancers / total) * 100}%` }} />
        <div className="h-full origin-right bg-down-600" style={{ width: `${(decliners / total) * 100}%` }} />
      </div>
    </div>
  );
}

/* ---------------- B1 小件：平均强度 donut（72px，draw-line） ---------------- */
function ScoreDonut({ score }: { score: number }) {
  const R = 28;
  const C = 2 * Math.PI * R;
  const target = C * (1 - score / 100);
  return (
    <div className="flex items-center gap-4">
      <p className="font-mono text-data-xl text-ink-900 tnum">{score.toFixed(1)}</p>
      <svg width="72" height="72" viewBox="0 0 72 72" aria-label={`平均强度分 ${score.toFixed(1)}`}>
        <circle cx="36" cy="36" r={R} fill="none" stroke="var(--line)" strokeWidth="6" />
        <motion.circle
          cx="36"
          cy="36"
          r={R}
          fill="none"
          stroke="var(--brand-600)"
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={C}
          initial={{ strokeDashoffset: C }}
          animate={{ strokeDashoffset: target }}
          transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
          transform="rotate(-90 36 36)"
        />
        <text x="36" y="40" textAnchor="middle" className="fill-ink-500 font-mono" fontSize="12">
          均值
        </text>
      </svg>
    </div>
  );
}

/* ---------------- 页头带右侧：强制刷新 ---------------- */
function ForceRefreshButton({ onRefresh, spinning }: { onRefresh: () => void; spinning: boolean }) {
  const { isOwner } = useAccess();
  return (
    <button
      onClick={isOwner ? onRefresh : undefined}
      disabled={!isOwner}
      title={isOwner ? '强制刷新自选快照' : '登录 Owner 后可强制刷新'}
      className={cn(
        'flex h-9 items-center gap-2 rounded-md border px-3 text-caption transition-colors duration-fast',
        isOwner
          ? 'border-line bg-card text-ink-600 hover:border-brand-400 hover:text-brand-600'
          : 'cursor-not-allowed border-line bg-card-warm text-ink-300',
      )}
    >
      <Icon name="refresh" size={15} className={spinning ? 'animate-spin-once' : ''} />
      强制刷新
    </button>
  );
}

/* ---------------- B3 小件：信号分布（横向 hatch 柱） ---------------- */
function SignalDistribution({ data }: { data: MarketSignalsSummary }) {
  const max = Math.max(...data.byType.map((t) => Math.max(t.today, t.avg7d)), 1);
  return (
    <div className="card-surface p-5">
      <p className="eyebrow">信号分布 · 今日 vs 7日均值</p>
      <div className="mt-4 space-y-3">
        {data.byType.map((t, i) => (
          <div key={t.type} className="grid grid-cols-[52px_1fr_36px] items-center gap-2">
            <span className="text-caption text-ink-500">{t.label}</span>
            <div className="space-y-1">
              <motion.div
                className="h-2 origin-left rounded-[2px] bg-brand-600"
                initial={{ scaleX: 0 }}
                whileInView={{ scaleX: 1 }}
                viewport={{ once: true, amount: 0.4 }}
                transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.045 }}
                style={{ width: `${(t.today / max) * 100}%` }}
                title={`今日 ${t.today} 条`}
              />
              <motion.div
                className="h-2 origin-left rounded-[2px] border border-brand-400/50"
                initial={{ scaleX: 0 }}
                whileInView={{ scaleX: 1 }}
                viewport={{ once: true, amount: 0.4 }}
                transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.045 + 0.06 }}
                style={{
                  width: `${(t.avg7d / max) * 100}%`,
                  backgroundImage: 'repeating-linear-gradient(45deg, rgba(46,70,224,.5) 0 1.2px, transparent 1.2px 4px)',
                }}
                title={`7 日均值 ${t.avg7d} 条`}
              />
            </div>
            <span className="text-right font-mono text-micro text-ink-500 tnum">{t.today}</span>
          </div>
        ))}
      </div>
      <HatchLegend className="mt-4" actual="今日" estimate="7日均值" />
    </div>
  );
}

/* ---------------- B3 小件：强度分布直方图 ---------------- */
function StrengthHistogram({ histogram }: { histogram: number[] }) {
  const max = Math.max(...histogram, 1);
  return (
    <div className="card-surface p-5">
      <p className="eyebrow">强度分布 · 全市场</p>
      <div className="mt-4 flex h-24 items-end gap-1.5">
        {histogram.map((n, i) => {
          const score = i * 10 + 5;
          const color = score >= 85 ? 'bg-up-600' : score >= 70 ? 'bg-brand-600' : score >= 50 ? 'bg-brand-400' : 'bg-ink-300';
          return (
            <div key={i} className="group relative flex-1">
              <div className="glass pointer-events-none absolute -top-7 left-1/2 z-10 hidden -translate-x-1/2 rounded-xs border border-line px-1.5 py-0.5 font-mono text-[10px] text-ink-600 shadow-sh-2 group-hover:block">
                {n}
              </div>
              <motion.div
                className={cn('w-full origin-bottom rounded-t-[3px]', color)}
                initial={{ scaleY: 0 }}
                whileInView={{ scaleY: 1 }}
                viewport={{ once: true, amount: 0.4 }}
                transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.04 }}
                style={{ height: `${Math.max(4, (n / max) * 88)}px` }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[9px] text-ink-300">
        <span>0</span><span>50</span><span>100</span>
      </div>
    </div>
  );
}

/* ---------------- B3 小件：市场时钟 ---------------- */
function MarketClockCard() {
  const { data: status } = usePolling(() => marketApi.status(), 60_000);
  const now = useNow(1000);
  const session = status?.session ?? 'closed';
  return (
    <div className="card-surface p-5">
      <p className="eyebrow">市场时钟 · 纽约</p>
      <div className="mt-3 flex items-center gap-2.5">
        <SessionDot session={session} />
        <span className="font-display text-[20px] leading-[26px] text-ink-900">{status?.label ?? '休市'}</span>
      </div>
      <p className="mt-2 font-mono text-data-l text-ink-800 tnum" suppressHydrationWarning>
        {fmtNyTime(new Date(now))}
      </p>
      <p className="mt-1 text-micro text-ink-400">美东时间 ET</p>
      {status?.nextEvent && (
        <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
          <span className="text-caption text-ink-500">距{status.nextEvent.kind === 'open' ? '开盘' : '收盘'}</span>
          <span className="font-mono text-data-m text-brand-600 tnum">{fmtCountdown(status.nextEvent.at, now)}</span>
        </div>
      )}
    </div>
  );
}


/* ---------------- 排序下拉 ---------------- */
const SORT_OPTIONS: { id: string; label: string; sort: SortState | null }[] = [
  { id: 'default', label: '默认排序', sort: null },
  { id: 'gain', label: '涨幅优先', sort: { key: 'changePct', desc: true } },
  { id: 'loss', label: '跌幅优先', sort: { key: 'changePct', desc: false } },
  { id: 'strength', label: '强度优先', sort: { key: 'strength', desc: true } },
  { id: 'ticker', label: '按代码 A–Z', sort: { key: 'ticker', desc: false } },
];

function SortDropdown({ sort, onChange }: { sort: SortState | null; onChange: (s: SortState | null) => void }) {
  const [open, setOpen] = useState(false);
  const current = SORT_OPTIONS.find((o) => o.sort?.key === sort?.key && o.sort?.desc === sort?.desc) ?? SORT_OPTIONS[0];
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-card px-2.5 text-caption text-ink-500 transition-colors hover:text-ink-800"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <Icon name="filter-funnel" size={13} />
        <span className="font-mono">{current.label}</span>
        <Icon name="chevron-down" size={12} className={cn('transition-transform duration-200', open && 'rotate-180')} />
      </button>
      {open && (
        <div role="listbox" className="absolute right-0 top-9 z-30 w-40 overflow-hidden rounded-md border border-line bg-card shadow-sh-2">
          {SORT_OPTIONS.map((o) => (
            <button
              key={o.id}
              role="option"
              aria-selected={current.id === o.id}
              onClick={() => {
                onChange(o.sort);
                setOpen(false);
              }}
              className={cn(
                'flex w-full items-center justify-between px-3 py-2 text-left font-mono text-caption transition-colors',
                current.id === o.id ? 'bg-brand-50 text-brand-600' : 'text-ink-500 hover:bg-paper-2',
              )}
            >
              {o.label}
              {current.id === o.id && <Icon name="check" size={12} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- 卡片模式单卡 ---------------- */
function WatchCard({ item, index, onClick }: { item: WatchlistItem; index: number; onClick: () => void }) {
  return (
    <motion.button
      layout="position"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: Math.min(index * 0.045, 0.5) }}
      onClick={onClick}
      className="card-surface card-hover flex flex-col p-4 text-left"
    >
      <div className="flex items-center gap-2.5">
        <TickerLogo ticker={item.ticker} />
        <div className="min-w-0 flex-1">
          <p className="font-mono text-body-s font-semibold text-ink-800">{item.ticker}</p>
          <p className="truncate text-micro text-ink-400">{item.name}</p>
        </div>
        <ChangeBadge value={item.changePct} size="sm" />
      </div>
      <p className="mt-3 font-mono text-data-l text-ink-900 tnum">{fmtPrice(item.price)}</p>
      <div className="mt-2">
        <Sparkline data={item.sparkline} width={230} height={56} change={item.changePct} variant="area" className="w-full" />
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3">
        <StrengthBar score={item.strengthScore} width={64} />
        <span className="flex gap-1">
          {item.signals.slice(0, 2).map((s, i) => (
            <SignalChip key={i} type={s.type} label={s.label} />
          ))}
          {item.signals.length > 2 && <span className="font-mono text-micro text-ink-400">+{item.signals.length - 2}</span>}
        </span>
      </div>
    </motion.button>
  );
}

/* ================= 页面主体 ================= */
export default function Watchlist() {
  const { isVisitor, isOwner } = useAccess();
  const { openPalette, openTicker } = useShell();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const [view, setView] = useState<'table' | 'cards'>('table');
  const [sort, setSort] = useState<SortState | null>(null);
  const [spinning, setSpinning] = useState(false);
  const forceRef = useRef(false);

  const fetchWatchlist = useCallback(() => {
    const f = forceRef.current;
    forceRef.current = false;
    return stocksApi.watchlist(f);
  }, []);

  const wl = usePolling(fetchWatchlist, 60_000);
  const signalsQ = usePolling(() => signalsApi.market(), 60_000);
  const strengthQ = usePolling(() => strengthApi.market(), 60_000);
  const statusQ = usePolling(() => marketApi.status(), 60_000);
  const now = useNow(1000);

  /* 命令面板「强制刷新自选」→ /watchlist?force=1 */
  useEffect(() => {
    if (searchParams.get('force') === '1') {
      if (isOwner) {
        forceRef.current = true;
        wl.refresh();
        toast.success('已强制刷新自选快照');
      } else {
        toast.info('登录 Owner 后可强制刷新');
      }
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, isOwner]);

  const onForceRefresh = () => {
    setSpinning(true);
    forceRef.current = true;
    wl.refresh();
    setTimeout(() => setSpinning(false), 650);
  };

  /* 涨跌 tick-flash 差异检测 */
  const [flashes, setFlashes] = useState<Record<string, 'up' | 'down'>>({});
  const prevPrices = useRef<Record<string, number>>({});
  useEffect(() => {
    if (!wl.data) return;
    const next: Record<string, 'up' | 'down'> = {};
    wl.data.forEach((it) => {
      const prev = prevPrices.current[it.ticker];
      if (prev !== undefined && prev !== it.price) next[it.ticker] = it.price > prev ? 'up' : 'down';
      prevPrices.current[it.ticker] = it.price;
    });
    if (Object.keys(next).length) {
      setFlashes(next);
      const t = setTimeout(() => setFlashes({}), 700);
      return () => clearTimeout(t);
    }
  }, [wl.data]);

  const aggregates = useMemo(() => {
    const items = wl.data ?? [];
    return {
      advancers: items.filter((x) => x.changePct >= 0).length,
      decliners: items.filter((x) => x.changePct < 0).length,
    };
  }, [wl.data]);

  /* 表格列（watchlist.md B2） */
  const columns = useMemo<Column<WatchlistItem>[]>(
    () => [
      {
        key: 'ticker',
        title: '代码',
        sortable: true,
        sortValue: (r) => r.ticker,
        render: (r) => (
          <span className="flex items-center gap-3">
            <TickerLogo ticker={r.ticker} />
            <span>
              <span className="block font-mono text-body-s font-semibold text-ink-800">{r.ticker}</span>
              <span className="block max-w-[140px] truncate text-micro text-ink-400">{r.name} · {r.sector}</span>
            </span>
          </span>
        ),
      },
      {
        key: 'price',
        title: '最新价',
        align: 'right',
        sortable: true,
        sortValue: (r) => r.price,
        render: (r) => (
          <span
            key={`${r.ticker}-${r.price}`}
            className={cn(
              'inline-block rounded-xs px-1 font-mono text-[15px] leading-6 text-ink-900 tnum',
              flashes[r.ticker] === 'up' && 'animate-tick-flash-up',
              flashes[r.ticker] === 'down' && 'animate-tick-flash-down',
            )}
          >
            {fmtPrice(r.price)}
          </span>
        ),
      },
      {
        key: 'changePct',
        title: '涨跌幅',
        align: 'right',
        sortable: true,
        sortValue: (r) => r.changePct,
        render: (r) => <ChangeBadge value={r.changePct} />,
      },
      {
        key: 'spark',
        title: '今日分时',
        render: (r) => <Sparkline data={r.sparkline} change={r.changePct} />,
      },
      {
        key: 'strength',
        title: '强度',
        sortable: true,
        sortValue: (r) => r.strengthScore,
        render: (r) => <StrengthBar score={r.strengthScore} width={80} />,
      },
      {
        key: 'signals',
        title: '信号',
        render: (r) => (
          <span className="flex items-center gap-1">
            {r.signals.slice(0, 2).map((s, i) => (
              <SignalChip key={i} type={s.type} label={s.label} />
            ))}
            {r.signals.length > 2 && (
              <span className="font-mono text-micro text-ink-400" title={r.signals.slice(2).map((s) => s.label).join('、')}>
                +{r.signals.length - 2}
              </span>
            )}
          </span>
        ),
      },
      {
        key: 'actions',
        title: '',
        align: 'right',
        width: '48px',
        render: () => (
          <span className="inline-flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ink-400 opacity-0 transition-opacity duration-fast group-hover:opacity-100">
            <Icon name="arrow-up-right" size={14} />
          </span>
        ),
      },
    ],
    [flashes],
  );

  const loading = wl.loading;
  const err = wl.error;
  const items = wl.data ?? [];
  const statsLoading = signalsQ.loading || strengthQ.loading;

  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="01"
        eyebrow="WATCHLIST · DELAYED 15MIN"
        title="自选观察"
        description="你盯住的票，今天谁在动。"
        meta={
          <>
            <SessionLED session={statusQ.data?.session ?? 'closed'} label={statusQ.data?.label} />
            <span className="hidden font-mono text-data-m text-ink-600 tnum sm:inline" suppressHydrationWarning>
              {fmtNyTime(new Date(now))}
            </span>
            <ForceRefreshButton onRefresh={onForceRefresh} spinning={spinning} />
          </>
        }
      />

      {/* B1 概览统计条 */}
      <section className="mt-6" aria-label="市场概览">
        {statsLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }, (_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : (
          <motion.div
            initial="hidden"
            animate="show"
            variants={{ show: { transition: { staggerChildren: 0.045 } } }}
            className="flex snap-x snap-mandatory gap-4 overflow-x-auto pb-1 no-scrollbar sm:grid sm:grid-cols-2 sm:overflow-visible xl:grid-cols-4"
          >
            {[
              <StatCard
                key="sig"
                label="今日信号"
                icon="flag"
                value={signalsQ.data?.totalToday ?? 0}
                sub={
                  <span className={cn('font-mono tnum', (signalsQ.data?.deltaVsYesterday ?? 0) >= 0 ? 'text-up-700' : 'text-down-700')}>
                    较昨日 {(signalsQ.data?.deltaVsYesterday ?? 0) >= 0 ? '+' : ''}
                    {signalsQ.data?.deltaVsYesterday ?? 0} 条
                  </span>
                }
              />,
              <StatCard
                key="ge85"
                label="高强度标的 ≥85"
                icon="target"
                value={strengthQ.data?.ge85Count ?? 0}
                sub={
                  <span className="mt-1 flex items-center gap-1.5">
                    <span className="h-1 w-10 rounded-pill bg-up-600" />
                    <span className="h-1 w-6 rounded-pill bg-brand-600" />
                    <span className="h-1 w-3 rounded-pill bg-brand-400" />
                  </span>
                }
              />,
              <div key="ad" className="card-surface min-w-[220px] snap-start p-5 sm:min-w-0">
                <div className="flex items-start justify-between">
                  <p className="eyebrow">上涨 / 下跌</p>
                  <Icon name="candle" size={18} className="text-ink-400" />
                </div>
                <AdvanceDeclineBar advancers={aggregates.advancers} decliners={aggregates.decliners} />
              </div>,
              <div key="avg" className="card-surface min-w-[220px] snap-start p-5 sm:min-w-0">
                <div className="flex items-start justify-between">
                  <p className="eyebrow">平均强度分</p>
                  <Icon name="wallet-gauge" size={18} className="text-ink-400" />
                </div>
                <ScoreDonut score={strengthQ.data?.avgScore ?? 0} />
              </div>,
            ].map((node, i) => (
              <motion.div
                key={i}
                variants={{ hidden: { opacity: 0, y: 14 }, show: { opacity: 1, y: 0, transition: { duration: 0.48, ease: [0.16, 1, 0.3, 1] } } }}
                className="min-w-[240px] snap-start sm:min-w-0"
              >
                {node}
              </motion.div>
            ))}
          </motion.div>
        )}
      </section>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* B2 自选主区（8 列） */}
        <section className="lg:col-span-8" aria-label="自选列表">
          {/* 工具行 */}
          <div className="flex h-11 items-center justify-between border-b border-line">
            <div className="flex items-center gap-2.5">
              <Segmented
                options={[
                  { value: 'table', label: '表格' },
                  { value: 'cards', label: '卡片' },
                ]}
                value={view}
                onChange={setView}
              />
              <SortDropdown sort={sort} onChange={setSort} />
            </div>
            <p className="text-caption text-ink-400">
              <span className="font-mono tnum">{items.length}</span> 只标的
              {wl.lastUpdatedAt && (
                <span className="ml-2 font-mono text-micro tnum">更新 {fmtTimeHHMMSS(wl.lastUpdatedAt)}</span>
              )}
            </p>
          </div>

          <div className="mt-4">
            {loading ? (
              <div className="card-surface">
                <SkeletonRows rows={6} />
              </div>
            ) : err ? (
              <div className="card-surface">
                <EmptyState
                  variant="error"
                  image="/empty-chart.svg"
                  title={err.code === 503 ? '快照暂不可用' : '加载失败'}
                  description={err.code === 503 ? '留空优于编造' : err.message}
                  action={
                    <button
                      onClick={wl.refresh}
                      disabled={wl.refreshing}
                      className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                    >
                      {wl.refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
                      重试
                    </button>
                  }
                />
              </div>
            ) : items.length === 0 ? (
              <div className="card-surface">
                <EmptyState
                  image="/empty-watchlist.svg"
                  title="清单还是空的"
                  description="按 ⌘K 搜索代码，加入你的第一只自选"
                  footnote={isVisitor ? '当前为访客只读模式' : undefined}
                  action={
                    <button
                      onClick={openPalette}
                      className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                    >
                      <Icon name="search" size={14} />
                      搜索代码
                    </button>
                  }
                />
              </div>
            ) : view === 'table' ? (
              <>
                {/* 桌面表格 */}
                <div className="hidden md:block">
                  <DataTable
                    columns={columns}
                    rows={items}
                    rowKey={(r) => r.ticker}
                    onRowClick={(r) => openTicker(r.ticker)}
                    sort={sort}
                    onSortChange={setSort}
                  />
                </div>
                {/* 移动：表格转卡片流 */}
                <div className="grid grid-cols-1 gap-3 md:hidden">
                  {items.map((it, i) => (
                    <WatchCard key={it.ticker} item={it} index={i} onClick={() => openTicker(it.ticker)} />
                  ))}
                </div>
              </>
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {items.map((it, i) => (
                  <WatchCard key={it.ticker} item={it} index={i} onClick={() => openTicker(it.ticker)} />
                ))}
              </div>
            )}
          </div>
        </section>

        {/* B3 右侧栏（4 列，吸顶 116px） */}
        <aside className="grid grid-cols-1 gap-4 self-start md:grid-cols-2 lg:sticky lg:top-[116px] lg:col-span-4 lg:grid-cols-1" aria-label="侧栏">
          {signalsQ.data ? (
            <SignalDistribution data={signalsQ.data} />
          ) : (
            <SkeletonCard />
          )}
          {strengthQ.data ? (
            <StrengthHistogram histogram={strengthQ.data.histogram} />
          ) : (
            <SkeletonCard />
          )}
          <MarketClockCard />
          <SourceNote />
        </aside>
      </div>
    </div>
  );
}
