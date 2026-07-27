/**
 * §05 财报日历 × AI 影响（earnings.md 完整实现）
 * B0 页头带（AI 状态点 + owner 刷新日历 60s 冷却三态）
 * B1 美东周历 scrubber · B2 按日期分组即将公布表 · EPS 斜纹对照图
 * B3 AI 影响分析卡（缓存结果 / 409 生成 / 任务轮询 / 锁定态）· B5 本月密度条
 * 轮询 1800s（契约 TTL）· 空态 / 骨架 / 503 · 响应式（<md 卡片流 + 横滑 snap）
 */
import { useCallback, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { earningsApi } from '@/api/modules/earnings';
import type { EarningsReportAnalysis } from '@/api/modules/earnings';
import { accountApi } from '@/api/modules/account';
import { ApiError } from '@/api/client';
import { useAccess } from '@/hooks/useAccess';
import { useNow } from '@/hooks/useNow';
import { usePolling } from '@/hooks/usePolling';
import { useToast } from '@/components/Toast';
import { cn } from '@/lib/utils';
import { fmtTimeHHMMSS } from '@/lib/format';
import Icon from '@/components/icons';
import PageHeader from '@/components/shared/PageHeader';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonBlock, SkeletonCard, SkeletonRows } from '@/components/shared/Skeleton';
import Segmented from '@/components/shared/Segmented';
import WeekScrubber from '@/components/earnings/WeekScrubber';
import MonthCalendar from '@/components/earnings/MonthCalendar';
import EarningsList from '@/components/earnings/EarningsList';
import EpsHatchChart from '@/components/earnings/EpsHatchChart';
import ImpactCard from '@/components/earnings/ImpactCard';
import EarningsAnalysisControls from '@/components/earnings/EarningsAnalysisControls';
import DensityStrip from '@/components/earnings/DensityStrip';
import PulseDot from '@/components/earnings/PulseDot';
import type { EarningsListMode, EarningsRow } from '@/components/earnings/types';
import {
  computeEarningsListState,
  etToday,
  fmtMDCN,
  isFeaturedRow,
  pickDefaultEarningsRow,
  planTickerSelection,
  resolveFeaturedSelection,
  weekStartMonday,
} from '@/components/earnings/types';
import { t } from '../i18n/core.ts';

const REFRESH_COOLDOWN_S = 60;
const LIST_PAGE_SIZE = 24;
type RefreshStatus = 'refreshed' | 'cooldown' | 'failed_stale' | null;

function reportAnalysisKey(ticker: string, reportDate: string): string {
  return `${ticker.trim().toUpperCase()}|${reportDate}`;
}

export default function Earnings() {
  const { isOwner, aiEnabled, aiAvailable, aiReason, canManageWatchlist } = useAccess();
  const toast = useToast();
  const now = useNow(1000);

  /* 数据（契约 TTL：earnings 1800s） */
  const q = usePolling(() => earningsApi.upcoming(), 1_800_000);
  /* 账号自选进入重点公司（账号上下文，只在本端合并，不进公共快照）。
     访客/未登录 resolve(null)：不发任何请求。 */
  const watchlistQ = usePolling(
    () => (canManageWatchlist ? accountApi.watchlist() : Promise.resolve(null)),
    null,
    [canManageWatchlist],
  );
  const personalTickers = useMemo(
    () => new Set((watchlistQ.data?.tickers ?? []).map((v) => v.toUpperCase())),
    [watchlistQ.data],
  );
  const [reportAnalysisStates, setReportAnalysisStates] = useState<Record<string, EarningsReportAnalysis>>({});
  const items = useMemo(
    () => ((q.data?.items ?? []) as unknown as EarningsRow[]).map((row) => {
      const analysis = reportAnalysisStates[reportAnalysisKey(row.ticker, row.date)];
      if (!analysis) return row;
      return {
        ...row,
        impactReady: analysis.result != null,
        analysisStage: analysis.analysisStage,
        locked: analysis.locked,
        final: analysis.final,
        finalizationInProgress: analysis.finalizationInProgress,
        reportId: analysis.reportId,
      };
    }),
    [q.data, reportAnalysisStates],
  );
  // sourceStatus can be "degraded" for a fresh, complete public snapshot
  // because guests consume the persisted read-only copy. Coverage is limited
  // only when the backend explicitly says the Finnhub full-market calendar is
  // incomplete; optional/stale enrichment must not trigger a false warning.
  const coverageLimited = q.data?.dataLimited === true;

  /* 周历状态 */
  const [monday, setMonday] = useState(() => weekStartMonday(etToday()));
  const [weekDir, setWeekDir] = useState(0);
  /* 日历视图：默认折叠 = 周历条；「月」展开月历大界面（上月/本月/下月） */
  const [calView, setCalView] = useState<'week' | 'month'>('week');
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [flashSignal, setFlashSignal] = useState(0);
  /* 列表模式：重点公司（默认）/ 全部公司。只过滤「即将公布」列表；
     周历/月历/密度条/统计恒用完整 allItems。 */
  const [listMode, setListMode] = useState<EarningsListMode>('featured');

  /* owner 刷新：60s 冷却 + refreshed/cooldown/failed_stale 三态 */
  const [refreshStatus, setRefreshStatus] = useState<RefreshStatus>(null);
  const [cooldownUntil, setCooldownUntil] = useState<number>(0);
  const [refreshing, setRefreshing] = useState(false);
  const cooldownRemain = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));

  /* 默认选中第一条「即将公布」（渲染期 adjust-state，避免 effect 级联；fixture 现含上月历史，跳过过去日期）。
     重点模式下默认候选来自重点列表——刚打开页面不该选中一只看不见的票。 */
  const [autoPicked, setAutoPicked] = useState(false);
  const defaultCandidates = useMemo(() => {
    if (listMode !== 'featured') return items;
    const featured = items.filter((it) => isFeaturedRow(it, personalTickers));
    return featured.length > 0 ? featured : items;
  }, [items, listMode, personalTickers]);
  const pickDefault = () => pickDefaultEarningsRow(defaultCandidates) ?? items[0];
  if (!autoPicked && !selectedTicker && items.length > 0) {
    setAutoPicked(true);
    setSelectedTicker(pickDefault().ticker);
  }
  /**
   * 自动选中的股票在刷新后可能不再存在（审计 P2-29）。
   *
   * 旧实现只自动选一次：autoPicked 保持 true、selectedTicker 保留旧值，而对应行
   * 已经变成 null，右侧分析卡于是收到「有股票代码、没有财报行」的不一致输入。
   * 只在「这一条是我们自动选的」时重选，用户手动选的代码不会被悄悄改掉。
   */
  const [autoPickedTicker, setAutoPickedTicker] = useState<string | null>(null);
  if (
    autoPicked
    && items.length > 0
    && selectedTicker !== null
    && selectedTicker === autoPickedTicker
    && !items.some((it) => it.ticker === selectedTicker)
  ) {
    const replacement = pickDefault().ticker;
    setAutoPickedTicker(replacement);
    setSelectedTicker(replacement);
  }
  if (autoPicked && autoPickedTicker === null && selectedTicker !== null) {
    setAutoPickedTicker(selectedTicker);
  }

  const onWeekChange = useCallback((dir: -1 | 1) => {
    setWeekDir(dir);
    setMonday((m) => {
      const d = new Date(`${m}T12:00:00Z`);
      d.setUTCDate(d.getUTCDate() + dir * 7);
      return d.toISOString().slice(0, 10);
    });
  }, []);

  const onRefresh = useCallback(async () => {
    if (refreshing) return;
    if (cooldownRemain > 0) {
      setRefreshStatus('cooldown');
      toast.info(t('刷新冷却中，{n}s 后可再次刷新', { n: cooldownRemain }));
      return;
    }
    setRefreshing(true);
    try {
      const fresh = await earningsApi.refresh();
      q.refresh();
      const retrySeconds = fresh.refreshRetryAfterSeconds ?? REFRESH_COOLDOWN_S;
      setCooldownUntil(Date.now() + retrySeconds * 1000);
      if (fresh.refreshStatus === 'failed_stale') {
        setRefreshStatus('failed_stale');
        toast.info(t('上游刷新失败，继续使用上一次完整日历'));
        return;
      }
      if (fresh.refreshStatus === 'cooldown') {
        setRefreshStatus('cooldown');
        toast.info(t('刷新冷却中，{n}s 后可再次刷新', { n: retrySeconds }));
        return;
      }
      setRefreshStatus('refreshed');
      setFlashSignal((s) => s + 1);
      toast.success(t('日历已更新 · {n} 条', { n: fresh.items.length }));
    } catch (e) {
      // 失败且带缓存 → _stale 横幅（failed_stale）；无缓存走 503 空态
      if (q.data) {
        setRefreshStatus('failed_stale');
      } else {
        toast.error(t('刷新失败'), e instanceof ApiError ? e.message : undefined);
      }
    } finally {
      setRefreshing(false);
    }
  }, [refreshing, cooldownRemain, q, toast]);

  /* 联动选择。从月历/周历/任意入口点到非重点公司时：自动切「全部公司」、
     保留并选中日期与代码、重置渐进额度（listScope 变化自动归位），选中行
     由 computeEarningsListState 强制挂载可见。 */
  const onSelectDay = useCallback((date: string | null) => setSelectedDay(date), []);
  const selectTicker = useCallback(
    (ticker: string) => {
      const plan = planTickerSelection({
        items,
        mode: listMode,
        personalTickers,
        ticker,
      });
      if (plan.switched) setListMode(plan.mode);
      setSelectedTicker(ticker);
    },
    [items, listMode, personalTickers],
  );
  const onSelectTickerFromChip = useCallback(
    (ticker: string, date: string) => {
      setSelectedDay(date);
      selectTicker(ticker);
    },
    [selectTicker],
  );
  const onSelectTickerFromRow = useCallback(
    (ticker: string) => selectTicker(ticker),
    [selectTicker],
  );
  const onListModeChange = useCallback(
    (mode: EarningsListMode) => {
      setListMode(mode);
      if (mode === 'featured') {
        /* 切回重点时不留下孤儿选中：非重点的选中换成重点默认候选 */
        setSelectedTicker((current) =>
          resolveFeaturedSelection({
            items,
            selectedDay,
            personalTickers,
            selectedTicker: current,
          }),
        );
      }
    },
    [items, selectedDay, personalTickers],
  );
  const onReportAnalysis = useCallback((ticker: string, analysis: EarningsReportAnalysis) => {
    if (!analysis.reportDate) return;
    const key = reportAnalysisKey(ticker, analysis.reportDate);
    setReportAnalysisStates((current) => (
      current[key] === analysis
        ? current
        : { ...current, [key]: analysis }
    ));
  }, []);
  const onJumpDay = useCallback(
    (date: string) => {
      setWeekDir(date >= monday ? 1 : -1);
      setMonday(weekStartMonday(date));
      setSelectedDay(date);
    },
    [monday],
  );

  /* 默认列表只挂载最近三天至未来 30 天，并按 24 条收纳。
     点选单日后也保持同一上限，避免财报密集日一次生成数百行 DOM。
     切换列表模式同样重置渐进额度（listScope 含 mode）。 */
  const listScope = `${listMode}|${selectedDay ?? 'rolling-window'}`;
  const [visibleLimit, setVisibleLimit] = useState(LIST_PAGE_SIZE);
  const [previousListScope, setPreviousListScope] = useState(listScope);
  if (listScope !== previousListScope) {
    setPreviousListScope(listScope);
    setVisibleLimit(LIST_PAGE_SIZE);
  }
  const listState = useMemo(
    () =>
      computeEarningsListState({
        items,
        selectedDay,
        mode: listMode,
        personalTickers,
        visibleLimit,
        selectedTicker,
      }),
    [items, selectedDay, listMode, personalTickers, visibleLimit, selectedTicker],
  );
  const filteredItems = listState.listItems;
  const visibleItems = listState.visibleItems;
  /* selectedRow 恒从完整 allItems 解析：右侧影响卡必须拿到完整 EarningsRow，
     与列表筛选模式无关。 */
  const selectedRow = useMemo(
    () => items.find((item) => item.ticker === selectedTicker) ?? null,
    [items, selectedTicker],
  );

  const loading = q.loading;
  const error503 = q.error && !q.data;

  /* 页头右侧：AI 状态点 + owner 刷新 */
  const headerMeta = (
    <>
      <span
        className="flex items-center gap-2"
        aria-label={
          aiAvailable
            ? t('AI 分析可用')
            : aiEnabled
              ? t('AI 分析暂不可用')
              : isOwner
                ? t('AI 分析未开启')
                : t('单股分析可用')
        }
      >
        {aiAvailable ? (
          <>
            <PulseDot className="bg-ai-600" size={8} />
            <Icon name="spark-ai" size={15} className="text-ai-600" />
            <span className="text-caption text-ai-600">{t('AI 可用')}</span>
          </>
        ) : aiEnabled ? (
          <>
            <span className="size-2 rounded-full bg-warn-600" aria-hidden="true" />
            <Icon name="spark-ai" size={15} className="text-warn-600" />
            <span className="text-caption text-warn-600">
              {['analysis_in_progress', 'global_concurrency_limit', 'queue_busy'].includes(aiReason ?? '')
                ? t('AI 处理中')
                : t('AI 暂不可用')}
            </span>
          </>
        ) : (
          <>
            <span className="size-2 rounded-full bg-ink-300" aria-hidden="true" />
            <Icon name="spark-ai" size={15} className="text-ink-300" />
            <span className="text-caption text-ink-400">{isOwner ? t('AI 未开启') : t('单股分析可用')}</span>
          </>
        )}
      </span>
      {isOwner && (
        <span className="flex items-center gap-2.5">
          {refreshStatus === 'failed_stale' && (
            <span className="font-mono text-micro text-warn-600">{t('刷新失败 · 显示已有数据')}</span>
          )}
          {refreshStatus === 'refreshed' && cooldownRemain <= 0 && q.lastUpdatedAt && (
            <span className="font-mono text-micro text-ink-400 tnum">{t('已更新')} {fmtTimeHHMMSS(q.lastUpdatedAt)}</span>
          )}
          <button
            onClick={() => void onRefresh()}
            disabled={refreshing || cooldownRemain > 0}
            title={cooldownRemain > 0 ? t('冷却中，{n}s 后可刷新', { n: cooldownRemain }) : t('手动刷新财报日历')}
            className={cn(
              'flex h-9 items-center gap-2 rounded-md border px-3 text-caption transition-colors duration-fast',
              refreshing || cooldownRemain > 0
                ? 'cursor-not-allowed border-line bg-card-warm text-ink-300'
                : 'border-line bg-card text-ink-600 hover:border-brand-400 hover:text-brand-600',
            )}
          >
            <Icon name="refresh" size={15} className={refreshing ? 'animate-spin-once' : ''} />
            {refreshing ? t('刷新中') : cooldownRemain > 0 ? <span className="font-mono tnum">{cooldownRemain}s</span> : t('刷新日历')}
          </button>
        </span>
      )}
    </>
  );

  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="05"
        eyebrow="EARNINGS · AI IMPACT"
        title={t("财报日历")}
        description={t("一份财报落地，涟漪会沿着供应链传开。")}
        meta={headerMeta}
      />

      {/* 自动轮询失败但有旧数据时也要提示过期（审计 P2-30）：
          旧实现只有手动刷新失败会设 failed_stale，30 分钟轮询失败时页面照常显示，
          没有任何横幅，用户无从知道看到的是旧数据。 */}
      {refreshStatus !== 'failed_stale' && q.error && items.length > 0 && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-md border border-warn-600/30 bg-warn-50 px-4 py-2.5">
          <p className="text-caption text-warn-600">
            {t('自动刷新失败，当前显示的是上一次的数据，可能已经过时。')}
          </p>
          <button
            onClick={q.refresh}
            className="shrink-0 rounded-sm border border-warn-600/40 px-2 py-1 text-caption text-warn-600 transition-colors hover:bg-warn-600 hover:text-white"
          >
            {t('重试')}
          </button>
        </div>
      )}

      {/* failed_stale：失败带缓存 → _stale 横幅 */}
      {refreshStatus === 'failed_stale' && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-md border border-warn-600/30 bg-warn-50 px-4 py-2.5">
          <p className="text-caption text-warn-600">{t('刷新失败，当前显示的是上一次的数据，可能已经过时。')}</p>
          <button
            onClick={() => void onRefresh()}
            className="shrink-0 rounded-sm border border-warn-600/40 px-2 py-1 text-caption text-warn-600 transition-colors hover:bg-warn-600 hover:text-white"
          >
            {t('重试')}
          </button>
        </div>
      )}

      {coverageLimited && (
        <div
          className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-warn-600/30 bg-warn-50 px-4 py-2.5"
          role="status"
        >
          <div>
            <p className="text-caption font-medium text-warn-600">{t('财报数据暂时不完整')}</p>
            <p className="mt-0.5 text-micro text-ink-500">
              {t('当前只显示已取到的')} {items.length} {t('家公司；缺失的公司不会用估算值顶替。')}
            </p>
          </div>
        </div>
      )}

      {/* B1 周历 scrubber */}
      <div className="mt-6">
        {loading ? (
          <div className="card-surface overflow-hidden" aria-label={t("周历加载中")}>
            <div className="flex h-11 items-center justify-center border-b border-line">
              <SkeletonBlock className="h-3 w-40" />
            </div>
            <div className="grid grid-cols-7">
              {Array.from({ length: 7 }, (_, i) => (
                <div key={i} className="min-h-[148px] space-y-2 border-r border-line p-2.5 last:border-r-0">
                  <SkeletonBlock className="h-3 w-8" />
                  <SkeletonBlock className="h-2.5 w-10" />
                  <SkeletonBlock className="h-5 w-full" />
                  <SkeletonBlock className="h-5 w-full" />
                </div>
              ))}
            </div>
          </div>
        ) : error503 ? (
          <section className="card-surface" aria-label={t("周历数据不可用")}>
            <EmptyState
              variant="error"
              image="/empty-chart.svg"
              title={t("日历数据不可用")}
              description={q.error?.message || t('稍后刷新再试')}
              action={
                <button
                  onClick={q.refresh}
                  disabled={q.refreshing}
                  className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                >
                  {q.refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
                  {t('重试')}
                </button>
              }
            />
          </section>
        ) : (
          <div>
            {/* 周 / 月视图切换（Segmented 滑块 260ms ease-paper；月历 accordion 320ms 展开） */}
            <div className="mb-3 flex items-center justify-between">
              <p className="eyebrow">EARNINGS CALENDAR · ET</p>
              <Segmented
                options={[
                  { value: 'week' as const, label: t('周') },
                  { value: 'month' as const, label: t('月') },
                ]}
                value={calView}
                onChange={setCalView}
              />
            </div>
            <AnimatePresence mode="wait" initial={false}>
              {calView === 'week' ? (
                <motion.div
                  key="week"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
                  className="overflow-hidden"
                >
                  <WeekScrubber
                    items={items}
                    monday={monday}
                    weekDir={weekDir}
                    onWeekChange={onWeekChange}
                    selectedDay={selectedDay}
                    selectedTicker={selectedTicker}
                    onSelectDay={onSelectDay}
                    onSelectTicker={onSelectTickerFromChip}
                    flashSignal={flashSignal}
                  />
                </motion.div>
              ) : (
                <motion.div
                  key="month"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
                  className="overflow-hidden"
                >
                  <MonthCalendar
                    items={items}
                    selectedDay={selectedDay}
                    selectedTicker={selectedTicker}
                    onSelectDay={onSelectDay}
                    onSelectTicker={onSelectTickerFromChip}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}
      </div>

      {/* B2 列表（左 8 列） · B3 分析与补充图表（右 4 列）。
          完整桌面表有 7 个数据列，7/5 分栏会在 2xl 表格断点把 AI 操作列裁掉。 */}
      <div
        className="mt-6 grid min-w-0 grid-cols-1 gap-6 xl:grid-cols-12"
        aria-label={t("财报主体")}
      >
        <div className="min-w-0 space-y-6 xl:col-span-8">
          {loading ? (
            <div className="card-surface">
              <SkeletonRows rows={6} />
            </div>
          ) : error503 ? (
            <section className="card-surface" aria-label={t("财报列表不可用")}>
              <EmptyState
                variant="error"
                image="/empty-chart.svg"
                title={t("财报列表不可用")}
                description={t("稍后刷新再试")}
                action={
                  <button
                    onClick={q.refresh}
                    disabled={q.refreshing}
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                  >
                    {t('重试')}
                  </button>
                }
              />
            </section>
          ) : (
            <>
              {/* 列表工具区：重点/全部模式（计数随当前日期范围）+ 日筛选提示。
                  模式只过滤下方列表；周历/月历/密度条/统计恒用完整数据。 */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Segmented
                  options={[
                    {
                      value: 'featured' as const,
                      label: `${t('重点公司')} ${listState.featuredCount}`,
                    },
                    {
                      value: 'all' as const,
                      label: `${t('全部公司')} ${listState.allCount}`,
                    },
                  ]}
                  value={listMode}
                  onChange={onListModeChange}
                />
                {selectedDay && (
                  <div className="flex items-center gap-2">
                    <p className="text-caption text-ink-500">
                      {t('已筛选')} <span className="font-mono text-brand-600">{fmtMDCN(selectedDay)}</span> {t('当日财报')}
                    </p>
                    <button
                      onClick={() => setSelectedDay(null)}
                      className="flex items-center gap-1 rounded-sm border border-line bg-card px-2 py-1 text-caption text-ink-500 transition-colors hover:text-ink-800"
                    >
                      <Icon name="x" size={12} />
                      {t('清除筛选')}
                    </button>
                  </div>
                )}
              </div>
              <EarningsList
                items={visibleItems}
                selectedTicker={selectedTicker}
                onSelectTicker={onSelectTickerFromRow}
                onNextWeek={() => onWeekChange(1)}
                filteredByDay={selectedDay != null}
                featuredFilteredEmpty={listMode === 'featured' && listState.allCount > 0}
                onShowAll={() => onListModeChange('all')}
              />
              {visibleItems.length < filteredItems.length && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-card px-4 py-3">
                  <p className="text-caption text-ink-500">
                    {t('已显示')} <span className="font-mono text-ink-800 tnum">{visibleItems.length}</span>
                    {' / '}
                    <span className="font-mono text-ink-800 tnum">{filteredItems.length}</span> {t('条')}
                  </p>
                  <button
                    onClick={() => setVisibleLimit((limit) => limit + LIST_PAGE_SIZE)}
                    className="h-8 rounded-md border border-line bg-card-warm px-3 text-caption text-ink-600 transition-colors hover:border-brand-400 hover:text-brand-600"
                  >
                    {t('显示更多 ·')} {Math.min(LIST_PAGE_SIZE, filteredItems.length - visibleItems.length)} {t('条')}
                  </button>
                </div>
              )}
              {visibleLimit > LIST_PAGE_SIZE && (
                <div className="flex justify-end">
                  <button
                    onClick={() => setVisibleLimit(LIST_PAGE_SIZE)}
                    className="h-8 px-2 text-caption text-ink-400 transition-colors hover:text-brand-600"
                  >
                    {t('收起至前')} {LIST_PAGE_SIZE} {t('条')}
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* B3 AI 影响 + 低交互图表，集中在右栏，列表保持可读宽度。 */}
        <div className="min-w-0 space-y-6 xl:col-span-4">
          <EarningsAnalysisControls />
          {loading ? (
            <SkeletonCard />
          ) : (
            <ImpactCard row={selectedRow} ticker={selectedTicker} onAnalyzed={onReportAnalysis} />
          )}
          {!loading && !error503 && <EpsHatchChart items={visibleItems} />}
          {!loading && !error503 && <DensityStrip items={items} onJumpDay={onJumpDay} />}
        </div>
      </div>
    </div>
  );
}
