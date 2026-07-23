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
import type { EarningsRow } from '@/components/earnings/types';
import { daysUntil, etToday, fmtMDCN, weekStartMonday } from '@/components/earnings/types';

const REFRESH_COOLDOWN_S = 60;
const LIST_PAGE_SIZE = 80;
type RefreshStatus = 'refreshed' | 'cooldown' | 'failed_stale' | null;

export default function Earnings() {
  const { isOwner, aiEnabled, aiAvailable, aiReason } = useAccess();
  const toast = useToast();
  const now = useNow(1000);

  /* 数据（契约 TTL：earnings 1800s） */
  const q = usePolling(() => earningsApi.upcoming(), 1_800_000);
  const items = useMemo(
    () => (q.data?.items ?? []) as unknown as EarningsRow[],
    [q.data],
  );
  const coverageLimited = q.data?.dataLimited === true
    || q.data?.sourceStatus === 'degraded';

  /* 周历状态 */
  const [monday, setMonday] = useState(() => weekStartMonday(etToday()));
  const [weekDir, setWeekDir] = useState(0);
  /* 日历视图：默认折叠 = 周历条；「月」展开月历大界面（上月/本月/下月） */
  const [calView, setCalView] = useState<'week' | 'month'>('week');
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [flashSignal, setFlashSignal] = useState(0);

  /* owner 刷新：60s 冷却 + refreshed/cooldown/failed_stale 三态 */
  const [refreshStatus, setRefreshStatus] = useState<RefreshStatus>(null);
  const [cooldownUntil, setCooldownUntil] = useState<number>(0);
  const [refreshing, setRefreshing] = useState(false);
  const cooldownRemain = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));

  /* 默认选中第一条「即将公布」（渲染期 adjust-state，避免 effect 级联；fixture 现含上月历史，跳过过去日期） */
  const [autoPicked, setAutoPicked] = useState(false);
  if (!autoPicked && !selectedTicker && items.length > 0) {
    setAutoPicked(true);
    const firstRelevant =
      items.find((it) => daysUntil(it.date) === 0 && typeof it.epsActual === 'number')
      ?? items.find((it) => daysUntil(it.date) === 0)
      ?? items.find((it) => daysUntil(it.date) > 0)
      ?? items[0];
    setSelectedTicker(firstRelevant.ticker);
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
      toast.info(`刷新冷却中，${cooldownRemain}s 后可再次刷新`);
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
        toast.info('上游刷新失败，继续使用上一次完整日历');
        return;
      }
      if (fresh.refreshStatus === 'cooldown') {
        setRefreshStatus('cooldown');
        toast.info(`刷新冷却中，${retrySeconds}s 后可再次刷新`);
        return;
      }
      setRefreshStatus('refreshed');
      setFlashSignal((s) => s + 1);
      toast.success(`日历已更新 · ${fresh.items.length} 条`);
    } catch (e) {
      // 失败且带缓存 → _stale 横幅（failed_stale）；无缓存走 503 空态
      if (q.data) {
        setRefreshStatus('failed_stale');
      } else {
        toast.error('刷新失败', e instanceof ApiError ? e.message : undefined);
      }
    } finally {
      setRefreshing(false);
    }
  }, [refreshing, cooldownRemain, q, toast]);

  /* 联动选择 */
  const onSelectDay = useCallback((date: string | null) => setSelectedDay(date), []);
  const onSelectTickerFromChip = useCallback((ticker: string, date: string) => {
    setSelectedDay(date);
    setSelectedTicker(ticker);
  }, []);
  const onSelectTickerFromRow = useCallback((ticker: string) => setSelectedTicker(ticker), []);
  const onJumpDay = useCallback(
    (date: string) => {
      setWeekDir(date >= monday ? 1 : -1);
      setMonday(weekStartMonday(date));
      setSelectedDay(date);
    },
    [monday],
  );

  /* 默认列表只挂载最近三天至未来 30 天，避免全市场日历生成数千行 DOM。
     用户在周/月历点选更远日期时仍会看到该日完整结果。 */
  const filteredItems = useMemo(
    () => (
      selectedDay
        ? items.filter((it) => it.date === selectedDay)
        : items.filter((it) => {
            const distance = daysUntil(it.date);
            return distance >= -3 && distance <= 30;
          })
    ),
    [items, selectedDay],
  );
  const listScope = selectedDay ?? 'rolling-window';
  const [visibleLimit, setVisibleLimit] = useState(LIST_PAGE_SIZE);
  const [previousListScope, setPreviousListScope] = useState(listScope);
  if (listScope !== previousListScope) {
    setPreviousListScope(listScope);
    setVisibleLimit(LIST_PAGE_SIZE);
  }
  const visibleItems = useMemo(
    () => filteredItems.slice(0, visibleLimit),
    [filteredItems, visibleLimit],
  );
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
            ? 'AI 分析可用'
            : aiEnabled
              ? 'AI 分析暂不可用'
              : isOwner
                ? 'AI 分析未开启'
                : '登录后可用 AI 分析'
        }
      >
        {aiAvailable ? (
          <>
            <PulseDot className="bg-ai-600" size={8} />
            <Icon name="spark-ai" size={15} className="text-ai-600" />
            <span className="text-caption text-ai-600">AI 可用</span>
          </>
        ) : aiEnabled ? (
          <>
            <span className="size-2 rounded-full bg-warn-600" aria-hidden="true" />
            <Icon name="spark-ai" size={15} className="text-warn-600" />
            <span className="text-caption text-warn-600">
              {['analysis_in_progress', 'global_concurrency_limit', 'queue_busy'].includes(aiReason ?? '')
                ? 'AI 处理中'
                : 'AI 暂不可用'}
            </span>
          </>
        ) : (
          <>
            <span className="size-2 rounded-full bg-ink-300" aria-hidden="true" />
            <Icon name="spark-ai" size={15} className="text-ink-300" />
            <span className="text-caption text-ink-400">{isOwner ? 'AI 未开启' : '登录后可用'}</span>
          </>
        )}
      </span>
      {isOwner && (
        <span className="flex items-center gap-2.5">
          {refreshStatus === 'failed_stale' && (
            <span className="font-mono text-micro text-warn-600">刷新失败 · 缓存快照</span>
          )}
          {refreshStatus === 'refreshed' && cooldownRemain <= 0 && q.lastUpdatedAt && (
            <span className="font-mono text-micro text-ink-400 tnum">已更新 {fmtTimeHHMMSS(q.lastUpdatedAt)}</span>
          )}
          <button
            onClick={() => void onRefresh()}
            disabled={refreshing || cooldownRemain > 0}
            title={cooldownRemain > 0 ? `冷却中，${cooldownRemain}s 后可刷新` : '手动刷新财报日历'}
            className={cn(
              'flex h-9 items-center gap-2 rounded-md border px-3 text-caption transition-colors duration-fast',
              refreshing || cooldownRemain > 0
                ? 'cursor-not-allowed border-line bg-card-warm text-ink-300'
                : 'border-line bg-card text-ink-600 hover:border-brand-400 hover:text-brand-600',
            )}
          >
            <Icon name="refresh" size={15} className={refreshing ? 'animate-spin-once' : ''} />
            {refreshing ? '刷新中' : cooldownRemain > 0 ? <span className="font-mono tnum">{cooldownRemain}s</span> : '刷新日历'}
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
        title="财报日历"
        description="一份财报落地，涟漪会沿着供应链传开。"
        meta={headerMeta}
      />

      {/* failed_stale：失败带缓存 → _stale 横幅 */}
      {refreshStatus === 'failed_stale' && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-md border border-warn-600/30 bg-warn-50 px-4 py-2.5">
          <p className="text-caption text-warn-600">刷新失败，当前展示缓存快照，数据可能已过期（_stale）。</p>
          <button
            onClick={() => void onRefresh()}
            className="shrink-0 rounded-sm border border-warn-600/40 px-2 py-1 text-caption text-warn-600 transition-colors hover:bg-warn-600 hover:text-white"
          >
            重试
          </button>
        </div>
      )}

      {coverageLimited && (
        <div
          className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-warn-600/30 bg-warn-50 px-4 py-2.5"
          role="status"
        >
          <div>
            <p className="text-caption font-medium text-warn-600">全市场财报源暂时不完整</p>
            <p className="mt-0.5 text-micro text-ink-500">
              当前仅显示已返回的 {items.length} 家公司，未返回的公司不会用热门名单或估算值补齐。
            </p>
          </div>
          <span className="font-mono text-micro text-ink-400">
            {q.data?.providers.length ? q.data.providers.join(' + ') : '上游来源未返回'}
          </span>
        </div>
      )}

      {/* B1 周历 scrubber */}
      <div className="mt-6">
        {loading ? (
          <div className="card-surface overflow-hidden" aria-label="周历加载中">
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
          <section className="card-surface" aria-label="周历快照不可用">
            <EmptyState
              variant="error"
              image="/empty-chart.svg"
              title="日历快照不可用"
              description={q.error?.message || '接口未覆盖此能力，留空而非编造'}
              action={
                <button
                  onClick={q.refresh}
                  disabled={q.refreshing}
                  className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                >
                  {q.refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
                  重试
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
                  { value: 'week' as const, label: '周' },
                  { value: 'month' as const, label: '月' },
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

      {/* B2 列表（左 7 列） · B3 分析与补充图表（右 5 列） */}
      <div
        className="mt-6 grid min-w-0 grid-cols-1 gap-6 xl:grid-cols-12"
        aria-label="财报主体"
      >
        <div className="min-w-0 space-y-6 xl:col-span-7">
          {loading ? (
            <div className="card-surface">
              <SkeletonRows rows={6} />
            </div>
          ) : error503 ? (
            <section className="card-surface" aria-label="财报列表快照不可用">
              <EmptyState
                variant="error"
                image="/empty-chart.svg"
                title="财报列表快照不可用"
                description="接口未覆盖此能力，留空而非编造"
                action={
                  <button
                    onClick={q.refresh}
                    disabled={q.refreshing}
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                  >
                    重试
                  </button>
                }
              />
            </section>
          ) : (
            <>
              {/* 日筛选提示 */}
              {selectedDay && (
                <div className="flex items-center justify-between">
                  <p className="text-caption text-ink-500">
                    已筛选 <span className="font-mono text-brand-600">{fmtMDCN(selectedDay)}</span> 当日财报
                  </p>
                  <button
                    onClick={() => setSelectedDay(null)}
                    className="flex items-center gap-1 rounded-sm border border-line bg-card px-2 py-1 text-caption text-ink-500 transition-colors hover:text-ink-800"
                  >
                    <Icon name="x" size={12} />
                    清除筛选
                  </button>
                </div>
              )}
              <EarningsList
                items={visibleItems}
                selectedTicker={selectedTicker}
                onSelectTicker={onSelectTickerFromRow}
                onNextWeek={() => onWeekChange(1)}
                filteredByDay={selectedDay != null}
              />
              {visibleItems.length < filteredItems.length && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-card px-4 py-3">
                  <p className="text-caption text-ink-500">
                    已显示 <span className="font-mono text-ink-800 tnum">{visibleItems.length}</span>
                    {' / '}
                    <span className="font-mono text-ink-800 tnum">{filteredItems.length}</span> 条
                  </p>
                  <button
                    onClick={() => setVisibleLimit((limit) => limit + LIST_PAGE_SIZE)}
                    className="h-8 rounded-md border border-line bg-card-warm px-3 text-caption text-ink-600 transition-colors hover:border-brand-400 hover:text-brand-600"
                  >
                    再显示 {Math.min(LIST_PAGE_SIZE, filteredItems.length - visibleItems.length)} 条
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* B3 AI 影响 + 低交互图表，集中在右栏，列表保持可读宽度。 */}
        <div className="min-w-0 space-y-6 xl:col-span-5">
          <EarningsAnalysisControls />
          {loading ? (
            <SkeletonCard />
          ) : (
            <ImpactCard row={selectedRow} ticker={selectedTicker} onAnalyzed={() => q.refresh()} />
          )}
          {!loading && !error503 && <EpsHatchChart items={visibleItems} />}
          {!loading && !error503 && <DensityStrip items={items} onJumpDay={onJumpDay} />}
        </div>
      </div>
    </div>
  );
}
