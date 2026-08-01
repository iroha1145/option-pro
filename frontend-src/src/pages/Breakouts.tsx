/**
 * §03 突破雷达（原版布局 · Paper Terminal 皮肤）
 * 页头带：§03 眉题 + 衬线大标 + 副标（仅流程句）+ 右侧紧凑状态条
 *        （扫描启用 LED / 快照与活跃条数 / 最近扫描 / 市场时段 chip / Worker / 下次扫描倒计时 · 只看自选）
 * 筛选行：状态 fchip 胶囊组 + 评分胶囊组 + ticker 聚焦 chip + owner「刷新快照」
 * 当日信号：左 7/12 lead 压缩大卡（不动）+ 右 5/12 吸顶 HistoryRail「历史事件回溯」压缩面板
 * 其下：SignalCards 个股小卡网格（当日其余事件，3 列 / 移动单列，V3 小卡结构恢复）
 * 事件详情模态保留 · status/current 30s 轮询 · 空态/骨架/503/移动端单列
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { breakoutsApi } from '@/api/modules/breakouts';
import { runtimeApi } from '@/api/modules/runtime';
import { stocksApi } from '@/api/modules/stocks';
import { usePolling } from '@/hooks/usePolling';
import { useTickFlash } from '@/hooks/useTickFlash';
import { useNow } from '@/hooks/useNow';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { cn } from '@/lib/utils';
import { fmtTimeHHMMSS } from '@/lib/format';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import { SkeletonCard } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import LeadBigCard from '@/components/breakouts/LeadBigCard';
import HistoryRail from '@/components/breakouts/HistoryRail';
import SignalCards from '@/components/breakouts/SignalCards';
import EventDetail from '@/components/breakouts/EventDetail';
import { asCurrentEvents, asFullDetail, asFullEvent, asFullStatus } from '@/components/breakouts/types';
import type {
  BreakoutCurrentEvent,
  BreakoutEventFull,
  BreakoutSession,
  LifecycleState,
} from '@/components/breakouts/types';
import { t as __t } from '../i18n/core.ts';

/* ---------------- 筛选维度（fchip 胶囊组） ---------------- */
type StatusFilter = 'ALL' | LifecycleState;
const STATUS_CAPS: { value: StatusFilter; label: string }[] = [
  { value: 'ALL', label: __t('全部') },
  { value: 'WATCHING', label: __t('观察中') },
  { value: 'TRIGGERED', label: __t('已触发') },
  { value: 'CONFIRMED', label: __t('已确认') },
  { value: 'HOLDING', label: __t('保持中') },
  { value: 'RETESTING', label: __t('回踩中') },
  { value: 'FAILED', label: __t('突破失败') },
];
const SCORE_CAPS = [
  { value: 0, label: __t('不限') },
  { value: 65, label: __t('65 分以上') },
  { value: 80, label: __t('80 分以上') },
];

/* ---------------- fchip 胶囊（选中 spring-pop 1.04） ---------------- */
function FChip({ active, onClick, children, ariaLabel }: { active: boolean; onClick: () => void; children: string; ariaLabel?: string }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      aria-label={ariaLabel}
      className={cn(
        'rounded-pill border px-2.5 py-1 text-micro font-medium transition-[transform,color,background-color,border-color] duration-fast',
        active
          ? 'scale-[1.04] border-brand-600 bg-brand-600 text-white shadow-chip'
          : 'border-line bg-card text-ink-500 hover:border-brand-400 hover:text-brand-600',
      )}
    >
      {children}
    </button>
  );
}

/* ---------------- 只看自选开关（toggle，knob 260ms） ---------------- */
function WatchOnlyToggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      className="flex items-center gap-2 text-caption text-ink-500 transition-colors hover:text-ink-800"
    >
      <motion.span
        whileTap={{ scale: 1.06 }}
        transition={{ type: 'spring', stiffness: 520, damping: 32 }}
        /* shrink-0：flex 行挤压下轨道不得缩窄（缩到 21px 时 knob 直接飞出压住标签） */
        className={cn('relative h-5 w-9 shrink-0 rounded-pill shadow-track transition-colors duration-ui', value ? 'bg-brand-600' : 'bg-line-strong')}
      >
        {/* left 显式锚定（与 ManagePanel/FilterBar 同范式）：绝对定位不写 left 时
            静态位置生效，而 button 的 UA text-align:center 会把基准推到轨道正中，
            translate 再叠加就双重偏移 */}
        <span
          className={cn(
            'absolute top-0.5 size-4 rounded-full bg-white shadow-knob transition-[left] duration-ui ease-paper',
            value ? 'left-[18px]' : 'left-0.5',
          )}
          aria-hidden="true"
        />
      </motion.span>
      {__t('只看自选')}
    </button>
  );
}

/* ---------------- 市场时段 chip（§1.6 色） ---------------- */
const SESSION_DOT: Record<BreakoutSession, string> = {
  premarket: 'bg-warn-600',
  regular: 'bg-up-600',
  postmarket: 'bg-ai-600',
  closed: 'bg-ink-400',
};
const SESSION_TEXT: Record<BreakoutSession, string> = {
  premarket: __t('盘前'),
  regular: __t('盘中'),
  postmarket: __t('盘后'),
  closed: __t('休市'),
};

function SessionChip({ session }: { session: BreakoutSession }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-pill border border-line bg-card px-2.5 py-1 text-caption text-ink-600">
      <span className={cn('size-1.5 rounded-full', SESSION_DOT[session], session !== 'closed' && 'animate-led-pulse')} aria-hidden="true" />
      {SESSION_TEXT[session]}
    </span>
  );
}

/* ================= 页面主体 ================= */
const HISTORY_PAGE_SIZE = 100;

const breakoutKey = (ev: BreakoutCurrentEvent) => ev.ticker;
const breakoutPrice = (ev: BreakoutCurrentEvent) => ev.current_price;

export default function Breakouts() {
  const { isOwner } = useAccess();
  const { openTicker } = useShell();
  const toast = useToast();
  const now = useNow(1000);

  /* 数据轮询：status 30s / current 30s（§11） */
  const statusQ = usePolling(() => breakoutsApi.status(), 30_000);
  const currentQ = usePolling(() => breakoutsApi.currentEnvelope(), 30_000);
  const eventsQ = usePolling(() => breakoutsApi.events({ page: 1, pageSize: HISTORY_PAGE_SIZE }), null);
  /* 历史事件此前固定只读第一页 100 条，界面还显示一个拼出来的「共 N 条」
     （审计 P2-19）。现在按游标续读，并如实说明是否还有更多。 */
  const [extraEvents, setExtraEvents] = useState<BreakoutEventFull[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [historyMoreError, setHistoryMoreError] = useState<ApiError | null>(null);
  useEffect(() => {
    // 首页重新加载后丢弃已续读的部分，避免与新首页重复。
    setExtraEvents([]);
    setHistoryCursor(eventsQ.data?.nextCursor ?? null);
    setHistoryMoreError(null);
  }, [eventsQ.data]);
  const loadMoreHistory = useCallback(async () => {
    if (!historyCursor || historyLoadingMore) return;
    setHistoryLoadingMore(true);
    setHistoryMoreError(null);
    try {
      const next = await breakoutsApi.events({
        page: 1,
        pageSize: HISTORY_PAGE_SIZE,
        cursor: historyCursor,
      });
      setExtraEvents((prev) => [...prev, ...next.items.map(asFullEvent)]);
      setHistoryCursor(next.nextCursor);
    } catch (error) {
      // 加载更多失败此前被吞掉，用户只会觉得按钮没反应（审计 P2-24 同型）。
      setHistoryMoreError(error instanceof ApiError ? error : new ApiError(500, __t('加载更多失败')));
    } finally {
      setHistoryLoadingMore(false);
    }
  }, [historyCursor, historyLoadingMore]);
  const watchQ = usePolling(() => stocksApi.watchlist(), null);

  const status = asFullStatus(statusQ.data);
  /* 必须记忆化：useTickFlash 以这个数组为依赖，每次渲染都换新引用会让效应无限重跑。 */
  const currentAll = useMemo(
    () => asCurrentEvents(currentQ.data?.events ?? null),
    [currentQ.data],
  );
  const events = useMemo(
    () => [...(eventsQ.data?.items ?? []).map(asFullEvent), ...extraEvents],
    [eventsQ.data, extraEvents],
  );

  /* 筛选行状态：状态 / 评分 / ticker 聚焦 */
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('ALL');
  const [minScore, setMinScore] = useState(0);
  const [tickerFilter, setTickerFilter] = useState('');

  const matchFilters = (e: BreakoutEventFull) => {
    if (statusFilter !== 'ALL' && e.lifecycle_state !== statusFilter) return false;
    /* live 评分可空：无分事件仅在设了评分门槛时被过滤（?? 0 保证 undefined 不绕过筛选） */
    if ((e.intrinsic_strength_score ?? 0) < minScore) return false;
    if (tickerFilter && !(e.ticker ?? '').includes(tickerFilter)) return false;
    return true;
  };

  /* 只看自选 */
  const [onlyWatch, setOnlyWatch] = useState(false);
  /* 自选还没到位时不能把它当成空集合（审计 P2-18）：watchQ 的 loading 与 error
     被忽略、数据缺失回落为空 Set，开启「只看自选」会先给出一个假空态。 */
  const watchReady = watchQ.data !== null;
  const watchFailed = watchQ.error !== null && watchQ.data === null;
  const watchSet = useMemo(() => new Set((watchQ.data ?? []).map((w) => w.ticker)), [watchQ.data]);
  const watchFilterPending = onlyWatch && !watchReady;
  const matchWatch = (ticker: string) => !onlyWatch || !watchReady || watchSet.has(ticker);
  const current = useMemo(
    () => currentAll.filter((e) => matchFilters(e) && matchWatch(e.ticker)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentAll, onlyWatch, watchSet, watchReady, statusFilter, minScore, tickerFilter],
  );
  /* 「只看自选」此前只作用于当前突破卡片，历史事件仍展示全部股票 ——
     同一个开关在一个页面里有两种语义（审计 P2-17）。 */
  const filteredEvents = useMemo(
    () => events.filter((e) => matchFilters(e) && matchWatch(e.ticker)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [events, onlyWatch, watchSet, watchReady, statusFilter, minScore, tickerFilter],
  );

  /* 现价 tick-flash：定时器由 useTickFlash 单独持有（审计 P2-5） */
  const flashes = useTickFlash(currentAll, breakoutKey, breakoutPrice);

  /* 详情模态 */
  const [selected, setSelected] = useState<BreakoutEventFull | null>(null);
  const [locateTicker, setLocateTicker] = useState<string | null>(null);
  const locateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (locateTimer.current) clearTimeout(locateTimer.current); }, []);

  const openFromCard = (ev: BreakoutCurrentEvent) => {
    setSelected(ev);
    setLocateTicker(ev.ticker);
    if (locateTimer.current) clearTimeout(locateTimer.current);
    locateTimer.current = setTimeout(() => setLocateTicker(null), 2000);
  };
  const [detailError, setDetailError] = useState<ApiError | null>(null);
  /* 当前打开的事件 id：成功路径本有 prev.event_id 守卫，失败路径没有——
     快速连开 A、B 两个事件时，A 的失败会把错误横幅打在 B 的模态上。 */
  const detailForRef = useRef<string | null>(null);
  const openFromArchive = (ev: BreakoutEventFull) => {
    setSelected(ev);
    setDetailError(null);
    detailForRef.current = ev.event_id;
    /* 契约 GET /breakouts/events/{id}：详情到位后替换（mock 同形） */
    breakoutsApi
      .eventDetail(ev.event_id)
      .then((d) => {
        if (detailForRef.current !== ev.event_id) return;
        setSelected((prev) => (prev && prev.event_id === ev.event_id ? asFullDetail(d) : prev));
        setDetailError(null);
      })
      .catch((error: unknown) => {
        if (detailForRef.current !== ev.event_id) return;
        // 详情失败此前被完全吞掉：界面既不报错也不给重试（审计 P2-20）。
        setDetailError(error instanceof ApiError ? error : new ApiError(500, __t('详情加载失败')));
      });
  };

  /* ticker 聚焦（详情「该代码全部事件」回填筛选行 chip，并滚动到历史回溯栏） */
  const railRef = useRef<HTMLDivElement>(null);
  const showTickerEvents = (t: string) => {
    setSelected(null);
    setTickerFilter(t);
    setTimeout(() => railRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
  };

  /* owner 触发 worker breakout_refresh */
  const [scanning, setScanning] = useState(false);
  const onRefreshSnapshot = async () => {
    if (scanning) return;
    setScanning(true);
    try {
      await runtimeApi.workerAction('breakout_refresh');
      toast.success(__t('已请求刷新'), __t('扫描任务已受理，完成后自动更新'));
      statusQ.refresh();
      window.setTimeout(() => {
        statusQ.refresh();
        currentQ.refresh();
        eventsQ.refresh();
      }, 10_000);
    } catch {
      toast.error(__t('触发失败'), __t('扫描任务未被受理，请稍后重试'));
    } finally {
      window.setTimeout(() => setScanning(false), 700);
    }
  };

  /* 下次扫描倒计时 mm:ss */
  const nextCountdown = useMemo(() => {
    if (!status?.next_session_at) return null;
    const ms = Math.max(0, new Date(status.next_session_at).getTime() - now);
    const m = Math.floor(ms / 60_000);
    const s = Math.floor((ms % 60_000) / 1000);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }, [status?.next_session_at, now]);

  /* 快照时间取契约 as_of（数据截至时间），读取时间单独显示（审计 P2-16）：
     两者混用会把「重新读到一份旧快照」显示成「刚刚更新」。 */
  const snapshotAt = currentQ.data?.asOf
    ? fmtTimeHHMMSS(new Date(currentQ.data.asOf))
    : '—';
  const readAt = currentQ.lastUpdatedAt ? fmtTimeHHMMSS(currentQ.lastUpdatedAt) : '—';
  const currentError = currentQ.error && !currentQ.data ? currentQ.error : null;

  /* 历史事件回溯压缩面板（右栏吸顶 / 空态·错误态下整宽兜底，保持历史可访问） */
  const historyRailEl = (
    <HistoryRail
      filterKey={`${statusFilter}|${minScore}|${tickerFilter}|${onlyWatch}`}
      events={filteredEvents}
      loadedCount={events.length}
      total={eventsQ.data?.total ?? null}
      serverHasMore={historyCursor !== null}
      loadingServerMore={historyLoadingMore}
      serverMoreError={historyMoreError}
      onFetchMore={loadMoreHistory}
      stale={Boolean(eventsQ.error && events.length > 0)}
      loading={eventsQ.loading}
      error={events.length > 0 ? null : eventsQ.error}
      onRetry={eventsQ.refresh}
      onOpenDetail={openFromArchive}
    />
  );

  return (
    <div>
      {/* 页头带：§03 眉题 + 衬线大标 + 副标 · 右侧紧凑状态条 */}
      <motion.header
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1] }}
        className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 border-b border-line pb-5"
      >
        <div>
          <p className="flex items-baseline gap-2.5">
            <span className="font-mono text-caption font-semibold text-brand-600">§03</span>
            <span className="eyebrow">BREAKOUT RADAR · INTRADAY</span>
          </p>
          <h1 className="mt-2 font-display text-display-l text-ink-900">{__t('突破雷达')}</h1>
          <p className="mt-1.5 text-body-s text-ink-500">{__t('全市场粗筛 → 点时复核 → 生命周期跟踪')}</p>
        </div>
        {/* 紧凑状态条：启用 LED · 快照与活跃条数（副标合并至此去重）· 最近扫描 · 时段 chip · 扫描服务 · 下次扫描倒计时 · 只看自选 */}
        <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 pb-1 text-caption text-ink-500">
          <span className="inline-flex items-center gap-1.5">
            <span className={cn('size-2 rounded-full', status?.enabled ? 'bg-up-600 animate-led-pulse' : 'bg-ink-300')} aria-hidden="true" />
            {status ? (status.enabled ? __t('扫描已启用') : __t('扫描已暂停')) : __t('状态读取中…')}
          </span>
          <span className="font-mono tnum">
            {__t('数据截至')} {snapshotAt} {__t('· 读取')} {readAt} · <span className="text-ink-700">{currentAll.length}</span> {__t('条活跃')}
          </span>
          <span className="font-mono tnum">
            {__t('最近扫描')} {status?.lastScanAt ? fmtTimeHHMMSS(new Date(status.lastScanAt)) : '—'}
          </span>
          {status?.market_session && <SessionChip session={status.market_session} />}
          {/* 扫描服务三态：正常 / 异常 / 状态未知。healthy === null 表示后端没有报告
              worker（库不可用或 schema 不符），不能显示成正常，也不等于确认异常。 */}
          <span className="inline-flex items-center gap-1.5">
            <Icon
              name="command"
              size={13}
              className={
                status?.worker?.healthy === true
                  ? 'text-up-600'
                  : status?.worker?.healthy === false
                    ? 'text-warn-600'
                    : 'text-ink-400'
              }
            />
            {__t('扫描服务')}{' '}
            {!status
              ? '—'
              : status.worker?.healthy === true
                ? __t('正常')
                : status.worker?.healthy === false
                  ? __t('异常')
                  : __t('状态未知')}
          </span>
          {nextCountdown && (
            <span className="font-mono tnum">
              {__t('下次扫描')} <span className="text-brand-600">{nextCountdown}</span>
            </span>
          )}
          <span className="inline-flex items-center gap-1.5">
            <WatchOnlyToggle value={onlyWatch} onChange={setOnlyWatch} />
            {/* 自选未就绪时说清楚过滤还没生效，而不是先给一个假空态（审计 P2-18） */}
            {watchFilterPending && (
              <span className="text-micro text-ink-400">
                {watchFailed ? __t('自选读取失败 · 暂显示全部') : __t('自选加载中 · 暂显示全部')}
              </span>
            )}
          </span>
        </div>
      </motion.header>

      {/* 筛选行：状态 fchip 组 · 评分胶囊组 · ticker 聚焦 chip · owner 立即扫描 */}
      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line pb-4">
        <span className="flex items-center gap-1.5 text-caption text-ink-400">
          <Icon name="filter-funnel" size={13} />
          {__t('筛选')}
        </span>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label={__t("状态筛选")}>
          {STATUS_CAPS.map((c) => (
            <FChip key={c.value} active={statusFilter === c.value} onClick={() => setStatusFilter(c.value)}>
              {c.label}
            </FChip>
          ))}
        </div>
        <span className="hidden h-4 w-px bg-line-strong sm:block" aria-hidden="true" />
        <div className="flex items-center gap-1" role="group" aria-label={__t("评分筛选")}>
          <span className="mr-0.5 text-caption text-ink-400">{__t('评分')}</span>
          {SCORE_CAPS.map((c) => (
            <FChip key={c.value} active={minScore === c.value} onClick={() => setMinScore(c.value)} ariaLabel={__t('评分{label}', { label: c.label })}>
              {c.label}
            </FChip>
          ))}
        </div>
        {tickerFilter && (
          <button
            onClick={() => setTickerFilter('')}
            aria-label={__t('清除代码聚焦 {ticker}', { ticker: tickerFilter })}
            className="inline-flex items-center gap-1 rounded-pill border border-brand-400/60 bg-brand-50 px-2.5 py-1 font-mono text-micro font-medium text-brand-600 transition-colors tnum hover:border-brand-600"
          >
            {tickerFilter}
            <Icon name="x" size={12} />
          </button>
        )}
        <span className="ml-auto flex items-center gap-3">
          {statusQ.lastUpdatedAt && (
            <span className="font-mono text-micro text-ink-400 tnum">{__t('更新')} {fmtTimeHHMMSS(statusQ.lastUpdatedAt)}</span>
          )}
          {isOwner && (
            <button
              onClick={onRefreshSnapshot}
              disabled={scanning}
              title={__t("立即触发一次突破扫描")}
              className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3 py-1.5 text-caption font-medium text-ink-600 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:opacity-60"
            >
              <Icon name="refresh" size={14} className={scanning ? 'animate-spin-once' : ''} />
              {__t('立即扫描')}
            </button>
          )}
        </span>
      </div>

      {/* 当日信号：左大面板（lead 压缩大卡）+ 右吸顶栏（事件队列 + 生命周期分布） */}
      <section className="mt-8" aria-label={__t("当日信号")}>
        <div className="mb-4 flex items-end justify-between border-b border-line pb-3">
          <div>
            <p className="eyebrow">TODAY&apos;S SIGNALS</p>
            <h2 className="mt-1 text-h2 text-ink-900">{__t('当日信号')}</h2>
          </div>
          <p className="font-mono text-caption text-ink-400 tnum">
            {current.length} {__t('个活跃')}{onlyWatch ? __t(' · 只看自选') : ''}
          </p>
        </div>
        {currentQ.loading ? (
          <div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              <SkeletonCard className="h-[460px] lg:col-span-7" />
              <SkeletonCard className="h-[560px] lg:col-span-5" />
            </div>
            <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              <SkeletonCard className="h-[280px]" />
              <SkeletonCard className="h-[280px]" />
              <SkeletonCard className="h-[280px]" />
            </div>
          </div>
        ) : currentError ? (
          <div>
            <div className="card-surface">
              <EmptyState
                variant="error"
                image="/empty-radar.svg"
                title={currentError.code === 503 ? __t('扫描数据暂不可用') : __t('信号加载失败')}
                description={currentError.code === 503 ? __t('稍后刷新再试') : currentError.message}
                action={
                  <button
                    onClick={currentQ.refresh}
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105"
                  >
                    {__t('重试')}
                  </button>
                }
              />
            </div>
            <div className="mt-4" ref={railRef}>
              {historyRailEl}
            </div>
          </div>
        ) : current.length === 0 ? (
          <div>
            <div className="card-surface">
              <EmptyState
                image="/empty-radar.svg"
                title={statusFilter !== 'ALL' || minScore > 0 || tickerFilter ? __t('没有符合筛选的信号') : __t('雷达在转，信号还没来')}
                description={
                  statusFilter !== 'ALL' || minScore > 0 || tickerFilter
                    ? __t('放宽筛选条件，或清除代码聚焦试试')
                    : onlyWatch
                      ? __t('自选池本轮暂无触发，试试关闭「只看自选」')
                      : __t('下一轮扫描在冷却结束后自动开始')
                }
                action={
                  <button
                    onClick={() => railRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105"
                  >
                    <Icon name="clock-ny" size={14} />
                    {__t('看看历史事件')}
                  </button>
                }
              />
            </div>
            <div className="mt-4" ref={railRef}>
              {historyRailEl}
            </div>
          </div>
        ) : (
          <>
            {/* ≥lg 12 列：左 7 压缩大卡（不动）· 右 5 吸顶「历史事件回溯」压缩面板；移动端上下堆叠 */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              <div className="min-w-0 lg:col-span-7">
                <LeadBigCard
                  ev={current[0]}
                  flash={flashes[current[0].ticker] ?? null}
                  locate={locateTicker === current[0].ticker}
                  onOpen={openFromCard}
                />
              </div>
              <div className="min-w-0 lg:col-span-5" ref={railRef}>
                <div className="self-start lg:sticky lg:top-20">{historyRailEl}</div>
              </div>
              {/* 卡片定位闪烁：左缘 brand tick-flash（点击卡片定位联动） */}
              <style>{`
                @keyframes bk-locate-flash {
                  0% { box-shadow: inset 3px 0 0 var(--brand-600), 0 0 0 3px rgba(46,70,224,.18); }
                  100% { box-shadow: inset 3px 0 0 transparent, 0 0 0 0 rgba(46,70,224,0); }
                }
                .bk-locate { animation: bk-locate-flash 900ms cubic-bezier(.22,1,.36,1) 2; }
                @media (prefers-reduced-motion: reduce) { .bk-locate { animation: none; } }
              `}</style>
            </div>

            {/* 其余当日信号：V3 个股小卡网格（3 列 / 移动单列） */}
            {current.length > 1 && (
              <div className="mt-6">
                <div className="mb-3 flex items-baseline justify-between border-b border-line pb-2">
                  <p className="text-body-s font-semibold text-ink-800">
                    {__t('其余当日信号 ·')} <span className="font-mono tnum">{current.length - 1}</span>
                  </p>
                  <p className="text-micro text-ink-400">{__t('点击小卡查看事件详情 · 点击代码打开个股抽屉')}</p>
                </div>
                <SignalCards
                  events={current.slice(1)}
                  flashes={flashes}
                  locateTicker={locateTicker}
                  onOpen={openFromCard}
                />
              </div>
            )}
          </>
        )}
      </section>

      <SourceNote className="mt-8" text={__t("突破扫描结果 · 行情为延迟数据")} />

      {/* 事件详情模态（保留） */}
      <EventDetail
        event={selected}
        detailError={detailError}
        onRetryDetail={selected ? () => openFromArchive(selected) : undefined}
        onClose={() => {
          setSelected(null);
          setDetailError(null);
        }}
        onOpenTicker={(t) => {
          setSelected(null);
          openTicker(t);
        }}
        onShowTickerEvents={showTickerEvents}
      />
    </div>
  );
}
