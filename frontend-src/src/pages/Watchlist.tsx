/**
 * §01 自选观察（watchlist.md 完整实现）
 * B0 页头带 · B1 概览统计（count-up）· B2 可排序表格/卡片（tick-flash）· B3 侧栏（信号/强度分布/市场时钟）
 * 轮询 60s · 空态 / 骨架 / 503 · 响应式
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { motion } from 'framer-motion';
import { stocksApi } from '@/api/modules/stocks';
import { accountApi } from '@/api/modules/account';
import { signalsApi } from '@/api/modules/signals';
import { strengthApi } from '@/api/modules/strength';
import { marketApi } from '@/api/modules/market';
import { runtimeApi } from '@/api/modules/runtime';
import { ApiError } from '@/api/client';
import { usePolling } from '@/hooks/usePolling';
import { useProgressiveList } from '@/hooks/useProgressiveList';
import { sortWatchlistItems } from './watchlistSort';
import { useTickFlash } from '@/hooks/useTickFlash';
import { useAccess } from '@/hooks/useAccess';
import { useNow } from '@/hooks/useNow';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { cn } from '@/lib/utils';
import { fmtCountdown, fmtNyTime, fmtPrice, fmtTimeHHMMSS } from '@/lib/format';
import type { MarketSignalsSnapshot, WatchlistItem } from '@/api/types';
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
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import SessionLED, { SessionDot } from '@/components/shared/SessionLED';
import { SkeletonCard, SkeletonRows } from '@/components/shared/Skeleton';
import Sparkline from '@/components/charts/Sparkline';
import Icon from '@/components/icons';

/* ---------------- B1 小件：涨跌宽度比条 ---------------- */
function AdvanceDeclineBar({
  advancers,
  decliners,
  unchanged,
}: {
  advancers: number;
  decliners: number;
  unchanged: number;
}) {
  const total = Math.max(1, advancers + decliners + unchanged);
  return (
    <div className="mt-2">
      <p className="font-mono text-data-xl tnum">
        <span className="text-up-700">{advancers}</span>
        <span className="mx-1.5 text-ink-300">/</span>
        <span className="text-down-700">{decliners}</span>
        {unchanged > 0 && (
          <span className="ml-1.5 align-middle text-caption text-ink-400">
            · {unchanged} 平
          </span>
        )}
      </p>
      <div className="mt-2 flex h-[3px] w-full overflow-hidden rounded-pill bg-line" aria-hidden="true">
        <div className="h-full origin-left animate-grow-bar bg-up-600" style={{ width: `${(advancers / total) * 100}%` }} />
        <div className="h-full bg-ink-300" style={{ width: `${(unchanged / total) * 100}%` }} />
        <div className="h-full origin-right bg-down-600" style={{ width: `${(decliners / total) * 100}%` }} />
      </div>
    </div>
  );
}

const watchKey = (item: WatchlistItem) => item.ticker;
const watchPrice = (item: WatchlistItem) => item.price;

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
      disabled={!isOwner || spinning}
      title={isOwner ? '重新计算完整自选数据' : '登录 Owner 后可强制刷新'}
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

/* ---------------- B3 小件：真实市场信号指标 ---------------- */
function SignalDistribution({ data }: { data: MarketSignalsSnapshot }) {
  return (
    <div className="card-surface p-5">
      <p className="eyebrow">市场信号 · 实时指标</p>
      <div className="mt-4 space-y-3">
        {data.metrics.slice(0, 8).map((metric) => (
          <div key={metric.key} className="grid grid-cols-[minmax(0,1fr)_56px] items-center gap-2">
            <span className="truncate text-caption text-ink-500" title={metric.label}>{metric.label}</span>
            <span className="text-right font-mono text-caption text-ink-800 tnum">{metric.value}</span>
          </div>
        ))}
      </div>
      <p className="mt-4 text-micro text-ink-400">
        顶部风险
        <InfoHint hint={SCORE_HINTS.readingTop} align="start" size={11} className="mx-0.5" />
        {` ${data.topScore ?? '—'} · 底部修复`}
        <InfoHint hint={SCORE_HINTS.readingBottom} size={11} className="mx-0.5" />
        {` ${data.bottomScore ?? '—'} · 数据质量`}
        <InfoHint hint={SCORE_HINTS.readingDataQuality} align="end" size={11} className="mx-0.5" />
        {` ${data.dataQuality ?? '—'}`}
      </p>
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
function WatchCard({
  item,
  index,
  onClick,
  onRemove,
  showStrength,
  showSignals,
  animateIn,
}: {
  item: WatchlistItem;
  index: number;
  onClick: () => void;
  /** 仅登录客户可移除自己的自选 */
  onRemove?: () => void;
  showStrength: boolean;
  showSignals: boolean;
  /**
   * 只有首屏那批卡片做入场动画。滚动后续批次时再逐张淡入，既看不到（它们是
   * 在视口外挂载的），又要付出每张一次的动画与合成成本。
   */
  animateIn: boolean;
}) {
  return (
    <motion.button
      /* layout="position" 曾挂在每张卡上。它会为每个元素建一个 framer 投影节点并在
         每次布局变化时重新测量 —— 214 张卡时 Style & Layout 达 1,285ms，且把
         ~103K 的投影/拖拽代码拉进首屏包。入场淡入不需要它，hover 位移也不需要。 */
      initial={animateIn ? { opacity: 0, y: 14 } : false}
      animate={animateIn ? { opacity: 1, y: 0 } : undefined}
      transition={
        animateIn
          ? { duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: Math.min(index * 0.045, 0.5) }
          : undefined
      }
      onClick={onClick}
      /* hover 上浮 -3px/240ms 走 whileHover（framer 入场后内联 transform:none 会压掉 CSS hover 位移），阴影用 CSS */
      whileHover={{ y: -3, transition: { duration: 0.24, ease: 'easeOut' } }}
      className="group/card card-surface relative flex flex-col p-4 text-left transition-shadow duration-[240ms] ease-out hover:shadow-sh-2"
    >
      {onRemove && (
        /* 卡片本身是 button，移除键用 span[role=button] 以免非法嵌套 */
        <span
          role="button"
          tabIndex={0}
          aria-label={`将 ${item.ticker} 移出自选`}
          title="移出自选"
          onClick={(event) => {
            event.stopPropagation();
            event.preventDefault();
            onRemove();
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.stopPropagation();
              event.preventDefault();
              onRemove();
            }
          }}
          className="absolute right-2 top-2 z-10 inline-flex size-6 cursor-pointer items-center justify-center rounded-xs text-ink-300 opacity-0 outline-none transition-[opacity,color] duration-fast hover:bg-paper-2 hover:text-down-700 focus-visible:opacity-100 group-hover/card:opacity-100"
        >
          <Icon name="x" size={13} />
        </span>
      )}
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
      {(showStrength || showSignals) && (
        <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3">
          {showStrength && <StrengthBar score={item.strengthScore} width={64} />}
          {showSignals && (
            <span className="flex gap-1">
              {item.signals.slice(0, 2).map((s, i) => (
                <SignalChip key={i} type={s.type} label={s.label} />
              ))}
              {item.signals.length > 2 && <span className="font-mono text-micro text-ink-400">+{item.signals.length - 2}</span>}
            </span>
          )}
        </div>
      )}
    </motion.button>
  );
}

/* ================= 页面主体 ================= */
export default function Watchlist() {
  const { isVisitor, isOwner, canManageWatchlist, username } = useAccess();
  const { openPalette, openTicker } = useShell();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const [view, setView] = useState<'table' | 'cards'>('cards');
  const [sort, setSort] = useState<SortState | null>(null);
  const [forceRefreshing, setForceRefreshing] = useState(false);

  /* 登录客户读自己那份自选；未登录仍是站点默认列表。 */
  const [myTickers, setMyTickers] = useState<string[] | null>(null);
  /**
   * 个人自选的读取状态。
   *
   * 只有 'ready' 且 tickers 为空才是「真的没有自选」；'loading' 期间不能先摆出
   * 默认池冒充这个人的列表，'error' 也不能降级成空列表。
   */
  const [personalState, setPersonalState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [personalReloadToken, setPersonalReloadToken] = useState(0);
  const [maxTickers, setMaxTickers] = useState(50);
  const [addInput, setAddInput] = useState('');
  const [savingTicker, setSavingTicker] = useState(false);

  useEffect(() => {
    if (!canManageWatchlist) {
      setMyTickers(null);
      setPersonalState('idle');
      return;
    }
    let alive = true;
    setPersonalState('loading');
    accountApi
      .watchlist()
      .then((data) => {
        if (!alive) return;
        setMyTickers(data.tickers);
        setMaxTickers(data.maxTickers);
        setPersonalState('ready');
      })
      .catch(() => {
        // 失败不能写成空列表。旧写法 setMyTickers([]) 让一次请求失败长得和
        // 「这个人还没建过自选」一模一样：横幅会说「你还没有自己的自选」，
        // 下面照常摆出默认池 —— 把读不到说成了一个事实。
        if (!alive) return;
        setMyTickers(null);
        setPersonalState('error');
      });
    return () => {
      alive = false;
    };
  }, [canManageWatchlist, personalReloadToken]);

  /**
   * 始终读站点默认自选（已缓存、对访客免费），个人列表在前端做筛选。
   * 不能改用 ?tickers=：那条路径对非管理员只读已有快照，任意代码组合都拿不到
   * 快照而返回 503，而让它去实时抓取等于把供应商开销对所有注册用户敞开。
   */
  const fetchWatchlist = useCallback(() => stocksApi.watchlist(), []);

  const wl = usePolling(fetchWatchlist, 60_000);
  const refreshWatchlist = wl.refresh;

  const onAddTicker = useCallback(async () => {
    const symbol = addInput.trim().toUpperCase();
    if (!symbol || savingTicker) return;
    setSavingTicker(true);
    try {
      const next = await accountApi.add(symbol);
      setMyTickers(next.tickers);
      setMaxTickers(next.maxTickers);
      setAddInput('');
      toast.success('已加入自选', symbol);
    } catch (error) {
      toast.error('加入失败', error instanceof ApiError ? error.message : '请稍后再试');
    } finally {
      setSavingTicker(false);
    }
  }, [addInput, savingTicker, toast]);

  const onRemoveTicker = useCallback(
    async (symbol: string) => {
      try {
        const next = await accountApi.remove(symbol);
        setMyTickers(next.tickers);
        toast.info('已移出自选', symbol);
      } catch (error) {
        toast.error('移除失败', error instanceof ApiError ? error.message : '请稍后再试');
      }
    },
    [toast],
  );
  const signalsQ = usePolling(() => signalsApi.market(), 60_000);
  const strengthQ = usePolling(() => strengthApi.market(), 60_000);
  const statusQ = usePolling(() => marketApi.status(), 60_000);
  const now = useNow(1000);

  const onForceRefresh = useCallback(async () => {
    if (!isOwner || forceRefreshing) {
      if (!isOwner) toast.info('登录 Owner 后可强制刷新');
      return;
    }
    setForceRefreshing(true);
    toast.info('正在刷新', '正在重新计算完整自选数据');
    try {
      const action = await runtimeApi.workerAction('focus_refresh');
      if (action.status !== 'completed') {
        if (!action.requestId) throw new ApiError(502, '刷新任务未能启动');
        await runtimeApi.waitForWorkerAction(action.requestId);
      }
      // worker 原子写入后绕过浏览器短缓存读取一次同一路径；不再发送
      // 后端从未支持的 ?force=1 占位参数。
      await stocksApi.watchlist(true);
      refreshWatchlist();
      toast.success('自选已更新', '已读取最新行情数据');
    } catch (error) {
      toast.error(
        '自选刷新失败',
        error instanceof ApiError ? error.message : '刷新暂时不可用',
      );
    } finally {
      setForceRefreshing(false);
    }
  }, [forceRefreshing, isOwner, refreshWatchlist, toast]);

  /* 命令面板「强制刷新自选」→ 真实 worker focus_refresh */
  useEffect(() => {
    if (searchParams.get('force') !== '1') return;
    void onForceRefresh();
    // 只删除 force 键：setSearchParams({}) 会把页面上其他查询参数一并清掉（审计 P2-7）。
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('force');
        return next;
      },
      { replace: true },
    );
  }, [onForceRefresh, searchParams, setSearchParams]);

  /* 涨跌 tick-flash：定时器由 useTickFlash 单独持有，避免闪烁态永久残留（审计 P2-5） */
  const flashes = useTickFlash(wl.data, watchKey, watchPrice);

  /* 平盘单独计数：changePct >= 0 会把持平股票算进上涨家数（审计 P2-4）。
     涨跌幅缺失的行也不能算进任何一侧。 */
  const aggregates = useMemo(() => {
    const items = wl.data ?? [];
    const known = items.filter((x) => Number.isFinite(x.changePct));
    return {
      advancers: known.filter((x) => x.changePct > 0).length,
      decliners: known.filter((x) => x.changePct < 0).length,
      unchanged: known.filter((x) => x.changePct === 0).length,
    };
  }, [wl.data]);
  const rowStrengthAvailable = useMemo(
    () => (wl.data ?? []).some((item) => Number.isFinite(item.strengthScore)),
    [wl.data],
  );
  const rowSignalsAvailable = useMemo(
    () => (wl.data ?? []).some((item) => item.signals.length > 0),
    [wl.data],
  );

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
      ...(rowStrengthAvailable
        ? [{
            key: 'strength',
            title: (
              <>
                强度
                <InfoHint hint={SCORE_HINTS.strengthComposite} side="bottom" size={11} />
              </>
            ),
            sortable: true,
            sortValue: (r: WatchlistItem) => r.strengthScore,
            render: (r: WatchlistItem) => <StrengthBar score={r.strengthScore} width={80} />,
          }]
        : []),
      ...(rowSignalsAvailable
        ? [{
            key: 'signals',
            title: '信号',
            render: (r: WatchlistItem) => (
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
          }]
        : []),
      {
        key: 'actions',
        title: '',
        align: 'right',
        // 桌面表格此前只有「进入详情」箭头，删除入口只存在于卡片/移动布局，
        // 同一功能在不同断点下能力不一致（审计 P2-6）。
        width: canManageWatchlist ? '84px' : '48px',
        render: (r) => (
          <span className="inline-flex items-center justify-end gap-1">
            {canManageWatchlist && (
              <button
                type="button"
                title={`从自选移除 ${r.ticker}`}
                aria-label={`从自选移除 ${r.ticker}`}
                onClick={(event) => {
                  // 行本身是「打开详情」的点击目标，删除必须先拦住冒泡。
                  event.stopPropagation();
                  void onRemoveTicker(r.ticker);
                }}
                className="inline-flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ink-400 opacity-0 transition-[opacity,color] duration-fast hover:border-down-600/40 hover:text-down-600 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30 group-hover:opacity-100"
              >
                <Icon name="x" size={13} />
              </button>
            )}
            <span className="inline-flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ink-400 opacity-0 transition-opacity duration-fast group-hover:opacity-100">
              <Icon name="arrow-up-right" size={14} />
            </span>
          </span>
        ),
      },
    ],
    [flashes, rowSignalsAvailable, rowStrengthAvailable, canManageWatchlist, onRemoveTicker],
  );

  // 个人自选还没读回来时同样算加载中：先摆出默认池、再换成个人列表，
  // 等于让登录用户短暂看到一份不属于他的清单。
  const loading = wl.loading || (canManageWatchlist && personalState === 'loading');
  const personalFailed = canManageWatchlist && personalState === 'error';
  const err = wl.error;
  /**
   * 个人自选为空时展示站点默认关注池，并由横幅明说这是默认池。
   *
   * 空列表直接渲染成空白页会让「还没添加过」和「加载失败」长得一模一样；
   * 而不加标注地展示默认池，又等于把系统的池子冒充成用户自己的选择。
   */
  // 只有成功读到空列表才是「还没建过自选」。读失败时 myTickers 为 null，
  // 这里必须为 false，否则失败会被写成一句关于用户的事实。
  const showingDefaultPool =
    canManageWatchlist && personalState === 'ready' && myTickers !== null && myTickers.length === 0;
  const items = useMemo(() => {
    const all = wl.data ?? [];
    if (!canManageWatchlist || !myTickers || myTickers.length === 0) return all;
    const bySymbol = new Map(all.map((row) => [row.ticker, row]));
    // 按用户自己的排序输出；覆盖范围外的代码留到下方单独提示，不静默丢弃
    return myTickers.map((symbol) => bySymbol.get(symbol)).filter((row): row is WatchlistItem => !!row);
  }, [wl.data, canManageWatchlist, myTickers]);

  /** 用户加了但默认行情覆盖不到的代码——如实说明，不假装它不存在 */
  const uncoveredTickers = useMemo(() => {
    if (!canManageWatchlist || !myTickers) return [];
    const covered = new Set((wl.data ?? []).map((row) => row.ticker));
    return myTickers.filter((symbol) => !covered.has(symbol));
  }, [wl.data, canManageWatchlist, myTickers]);
  // 唯一排序实现见 watchlistSort：卡片、表格与渐进切片必须消费同一份排序结果，
  // 否则「先切片再排序」会把局部样本冒充成完整结果。
  const cardItems = useMemo(() => sortWatchlistItems(items, sort), [items, sort]);
  const statsLoading = signalsQ.loading || strengthQ.loading;

  /**
   * 渲染分批，数据不分批。
   *
   * cardItems 已经是**完整且排好序**的列表，上面的涨跌家数、平均强度、覆盖提示
   * 也都基于它 —— 这里只决定一次挂载多少个 DOM 节点。滚到底部自动接着挂，
   * 因此「这就是全部」的语义没有改变。
   */
  const FIRST_BATCH = 24;
  const progressive = useProgressiveList(cardItems, { initial: FIRST_BATCH, step: 24 });
  const renderedCards = progressive.visible;
  // 桌面表格与移动卡片流是同一份数据的两种呈现，用同一个切片。
  //
  // 这里必须是**排好序之后**的切片。之前我传的是 items.slice(...) —— 未排序的
  // 前 N 条，而 DataTable 内部还会再排一次，于是选「涨幅优先」看到的是
  // 「任意前 24 只里涨得最多的」而不是「涨得最多的 24 只」。那正是把局部样本
  // 冒充成完整结果，和这次审计要修的是同一类错误。
  const renderedRows = renderedCards;

  /* 打印前挂载全部，打印后还回去。
     前者：否则打出来的只有当前已经滚动加载的那几十张卡（而且必须同步提交，
     浏览器可能在 beforeprint 返回后立刻截取打印文档）。
     后者：不还的话两百多张卡会一直挂在 DOM 里，这一轮渐进挂载的收益到下次整页
     刷新前都作废。
     浏览器原生查找的同类限制没有可靠拦截点，不在这里假装解决。 */
  const { prepareForPrint, restoreAfterPrint } = progressive;
  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.addEventListener('beforeprint', prepareForPrint);
    window.addEventListener('afterprint', restoreAfterPrint);
    return () => {
      window.removeEventListener('beforeprint', prepareForPrint);
      window.removeEventListener('afterprint', restoreAfterPrint);
    };
  }, [prepareForPrint, restoreAfterPrint]);

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
            {username && (
              <span className="hidden items-center gap-1.5 rounded-pill border border-line-strong bg-card px-2.5 py-1 text-caption text-ink-600 sm:inline-flex">
                <Icon name="command" size={12} className="text-brand-600" />
                {username}
              </span>
            )}
            <SessionLED session={statusQ.data?.session ?? 'closed'} label={statusQ.data?.label} />
            <span className="hidden font-mono text-data-m text-ink-600 tnum sm:inline" suppressHydrationWarning>
              {fmtNyTime(new Date(now))}
            </span>
            <ForceRefreshButton
              onRefresh={() => void onForceRefresh()}
              spinning={forceRefreshing || wl.refreshing}
            />
          </>
        }
      />

      {/* B1 概览统计条 */}
      <section className="mt-6" aria-label="市场概览">
        {statsLoading ? (
          /* 占位必须和真实内容占同样的空间。
             旧写法在移动端是 grid-cols-1 —— 四张卡竖着堆起来，而真实内容是一行
             横向滚动条（130px）。两者高度差直接产生 CLS 0.200，是这个页面最差的
             一项指标。这里用与下方 motion.div 完全相同的布局类。 */
          <div className="flex snap-x snap-mandatory gap-4 overflow-x-auto pb-1 no-scrollbar sm:grid sm:grid-cols-2 sm:overflow-visible xl:grid-cols-4">
            {Array.from({ length: 4 }, (_, i) => (
              <SkeletonCard key={i} className="min-w-[220px] shrink-0 snap-start sm:min-w-0" />
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
              ...(signalsQ.data?.topScore !== null && signalsQ.data?.topScore !== undefined
                ? [<StatCard key="top-risk" label="顶部风险分" icon="flag" value={signalsQ.data.topScore} sub={signalsQ.data.topLabel ?? '市场信号模型'} className="card-lift" />]
                : []),
              ...(signalsQ.data?.bottomScore !== null && signalsQ.data?.bottomScore !== undefined
                ? [<StatCard key="bottom-repair" label="底部修复分" icon="target" value={signalsQ.data.bottomScore} sub={signalsQ.data.bottomLabel ?? '市场信号模型'} className="card-lift" />]
                : []),
              <div key="ad" className="card-surface min-w-[220px] snap-start p-5 sm:min-w-0">
                <div className="flex items-start justify-between">
                  <p className="eyebrow">上涨 / 下跌</p>
                  <Icon name="candle" size={18} className="text-ink-400" />
                </div>
                <AdvanceDeclineBar
                  advancers={aggregates.advancers}
                  decliners={aggregates.decliners}
                  unchanged={aggregates.unchanged}
                />
              </div>,
              ...(strengthQ.data?.aggregateAvailable
                ? [
                    <div key="avg" className="card-surface min-w-[220px] snap-start p-5 sm:min-w-0">
                      <div className="flex items-start justify-between">
                        <p className="eyebrow">
                          全市场平均强度
                          <InfoHint hint={SCORE_HINTS.avgStrength} side="bottom" size={12} className="ml-1" />
                        </p>
                        <Icon name="wallet-gauge" size={18} className="text-ink-400" />
                      </div>
                      <ScoreDonut score={strengthQ.data.avgScore} />
                    </div>,
                  ]
                : []),
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
          <div className="flex min-h-11 flex-wrap items-center justify-between gap-2 border-b border-line py-1.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2.5">
              <Segmented
                options={[
                  { value: 'table', label: '表格' },
                  { value: 'cards', label: '卡片' },
                ]}
                value={view}
                onChange={setView}
              />
              <SortDropdown sort={sort} onChange={setSort} />
              {canManageWatchlist && (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void onAddTicker();
                  }}
                  className="flex items-center gap-1.5"
                >
                  <input
                    value={addInput}
                    onChange={(event) => setAddInput(event.target.value)}
                    placeholder="加自选"
                    maxLength={12}
                    aria-label="添加自选股票代码"
                    className="h-8 w-[92px] rounded-sm border border-line-strong bg-card px-2 font-mono text-caption uppercase text-ink-800 outline-none transition-[border-color,box-shadow] duration-fast placeholder:font-sans placeholder:normal-case placeholder:text-ink-300 focus:border-brand-600 focus:shadow-focus-ring"
                  />
                  <button
                    type="submit"
                    disabled={savingTicker || !addInput.trim()}
                    className="flex h-8 items-center gap-1 rounded-sm border border-line-strong bg-card px-2.5 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Icon name="plus" size={13} />
                    添加
                  </button>
                </form>
              )}
            </div>
            <p className="w-full text-right text-caption text-ink-400 sm:w-auto">
              <span className="font-mono tnum">{items.length}</span> 只标的
              {canManageWatchlist && <span className="ml-1 text-ink-300">/ 上限 {maxTickers}</span>}
              {wl.lastUpdatedAt && (
                <span className="ml-2 hidden font-mono text-micro tnum sm:inline">更新 {fmtTimeHHMMSS(wl.lastUpdatedAt)}</span>
              )}
            </p>
          </div>

          {uncoveredTickers.length > 0 && (
            <p
              className="mt-3 rounded-md border border-warn-600/25 bg-warn-50 px-3 py-2 text-caption text-warn-600"
              role="status"
            >
              暂无行情：{uncoveredTickers.join('、')}
              <span className="ml-1 text-ink-500">（不在当前覆盖范围内，可在个股页手动获取）</span>
            </p>
          )}

          {personalFailed && (
            <p
              className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-warn-600/25 bg-warn-50 px-3 py-2 text-caption text-warn-600"
              role="status"
            >
              读不到你的自选列表，下面显示的是系统默认关注池。
              <button
                type="button"
                onClick={() => setPersonalReloadToken((n) => n + 1)}
                className="rounded-xs border border-warn-600/30 px-2 py-0.5 font-medium text-warn-600 transition-colors hover:bg-warn-600/10"
              >
                重试
              </button>
            </p>
          )}

          {showingDefaultPool && (
            <p
              className="mt-3 rounded-md border border-line bg-paper-2 px-3 py-2 text-caption text-ink-500"
              role="status"
            >
              你还没有自己的自选，下面是系统默认关注池。
              <span className="ml-1 text-ink-400">上方输入代码即可开始建立自己的列表。</span>
            </p>
          )}

          {/* 高度保留放在所有状态共用的外壳上。
              之前只加在 loading 分支：接口一旦返回错误、空列表，或个人自选很短，
              那个 70vh 会被一张很矮的状态卡取代，侧栏和页脚整体上移 —— 同一种
              位移换了个触发条件。真正的路由级 CLS 由 PageFallback 处理，这里
              负责的是数据状态之间的切换。 */}
          <div className="mt-4 min-h-[70vh]">
            {loading ? (
              <div className="card-surface">
                <SkeletonRows rows={10} />
              </div>
            ) : err ? (
              <div className="card-surface">
                <EmptyState
                  variant="error"
                  image="/empty-chart.svg"
                  title={err.code === 503 ? '数据暂不可用' : '加载失败'}
                  description={err.code === 503 ? '稍后刷新再试' : err.message}
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
                  description={
                    canManageWatchlist
                      ? '在上方输入股票代码，加入你的第一只自选'
                      : '登录后可以把自选股保存在账号里，换设备也还在'
                  }
                  footnote={
                    canManageWatchlist
                      ? `自选保存在账号 ${username} 下`
                      : isVisitor
                        ? '当前为访客只读模式'
                        : undefined
                  }
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
                    rows={renderedRows}
                    rowKey={(r) => r.ticker}
                    onRowClick={(r) => openTicker(r.ticker)}
                    sort={sort}
                    onSortChange={setSort}
                  />
                </div>
                {/* 移动：表格转卡片流 */}
                <div className="grid grid-cols-1 gap-3 md:hidden">
                  {renderedCards.map((it, i) => (
                    <WatchCard
                      key={it.ticker}
                      item={it}
                      index={i}
                      animateIn={i < FIRST_BATCH}
                      onClick={() => openTicker(it.ticker)}
                      onRemove={canManageWatchlist ? () => void onRemoveTicker(it.ticker) : undefined}
                      showStrength={rowStrengthAvailable}
                      showSignals={rowSignalsAvailable}
                    />
                  ))}
                </div>
              </>
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {renderedCards.map((it, i) => (
                  <WatchCard
                    key={it.ticker}
                    item={it}
                    index={i}
                    animateIn={i < FIRST_BATCH}
                    onClick={() => openTicker(it.ticker)}
                    onRemove={canManageWatchlist ? () => void onRemoveTicker(it.ticker) : undefined}
                    showStrength={rowStrengthAvailable}
                    showSignals={rowSignalsAvailable}
                  />
                ))}
              </div>
            )}
            {progressive.hasMore && (
              <div ref={progressive.sentinelRef} className="mt-4 flex justify-center">
                <button
                  type="button"
                  onClick={progressive.loadMore}
                  className="inline-flex min-h-11 items-center gap-2 rounded-md border border-line-strong bg-card px-4 py-2 text-caption text-ink-600 transition-colors hover:bg-paper-2"
                >
                  加载更多
                  <span className="font-mono text-micro text-ink-400 tnum">
                    还有 {progressive.remaining} 只
                  </span>
                </button>
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
          {strengthQ.data?.aggregateAvailable && strengthQ.data.histogram.length > 0 ? (
            <StrengthHistogram histogram={strengthQ.data.histogram} />
          ) : strengthQ.loading ? (
            <SkeletonCard />
          ) : null}
          <MarketClockCard />
          <SourceNote />
        </aside>
      </div>
    </div>
  );
}
