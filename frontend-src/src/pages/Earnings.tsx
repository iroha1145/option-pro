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
import DensityStrip from '@/components/earnings/DensityStrip';
import PulseDot from '@/components/earnings/PulseDot';
import type { EarningsRow } from '@/components/earnings/types';
import { daysUntil, etToday, fmtMDCN, weekStartMonday } from '@/components/earnings/types';

const REFRESH_COOLDOWN_S = 60;
type RefreshStatus = 'refreshed' | 'cooldown' | 'failed_stale' | null;

export default function Earnings() {
  const { isOwner, aiEnabled } = useAccess();
  const toast = useToast();
  const now = useNow(1000);

  /* 数据（契约 TTL：earnings 1800s） */
  const q = usePolling(() => earningsApi.upcoming(), 1_800_000);
  const items = useMemo(() => (q.data ?? []) as unknown as EarningsRow[], [q.data]);

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
    const firstUpcoming = items.find((it) => daysUntil(it.date) >= 0) ?? items[0];
    setSelectedTicker(firstUpcoming.ticker);
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
      setCooldownUntil(Date.now() + REFRESH_COOLDOWN_S * 1000);
      setRefreshStatus('refreshed');
      setFlashSignal((s) => s + 1);
      toast.success(`日历已更新 · ${fresh.length} 条`);
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

  /* B2「即将公布」口径保持未来数据：选中日过滤到当日（可为历史日）；未选日期仅展示今天起的排期 */
  const filteredItems = useMemo(
    () => (selectedDay ? items.filter((it) => it.date === selectedDay) : items.filter((it) => daysUntil(it.date) >= 0)),
    [items, selectedDay],
  );

  const loading = q.loading;
  const error503 = q.error && !q.data;

  /* 页头右侧：AI 状态点 + owner 刷新 */
  const headerMeta = (
    <>
      <span className="flex items-center gap-2" aria-label={aiEnabled ? 'AI 已启用' : 'AI 已关闭'}>
        {aiEnabled ? (
          <>
            <PulseDot className="bg-ai-600" size={8} />
            <Icon name="spark-ai" size={15} className="text-ai-600" />
            <span className="text-caption text-ai-600">AI 已启用</span>
          </>
        ) : (
          <>
            <span className="size-2 rounded-full bg-ink-300" aria-hidden="true" />
            <Icon name="spark-ai" size={15} className="text-ink-300" />
            <span className="text-caption text-ink-400">AI 已关闭</span>
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

      {/* B2 + 图表（左 7 列） · B3（右 5 列吸顶） */}
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-7">
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
                items={filteredItems}
                selectedTicker={selectedTicker}
                onSelectTicker={onSelectTickerFromRow}
                onNextWeek={() => onWeekChange(1)}
                filteredByDay={selectedDay != null}
              />
              <EpsHatchChart items={filteredItems} />
            </>
          )}
        </div>

        {/* B3 AI 影响（右 5 列，吸顶） */}
        <div className="lg:col-span-5">
          {loading ? (
            <SkeletonCard className="lg:sticky lg:top-[116px]" />
          ) : (
            <ImpactCard ticker={selectedTicker} onAnalyzed={() => q.refresh()} className="lg:sticky lg:top-[116px]" />
          )}
        </div>
      </div>

      {/* B5 补充带：本月密度条（503 时隐藏） */}
      {!loading && !error503 && (
        <div className="mt-6">
          <DensityStrip items={items} onJumpDay={onJumpDay} />
        </div>
      )}
    </div>
  );
}
