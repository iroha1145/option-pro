/**
 * Optix 宏观环境面板（/market 页 B4 区块）
 *
 * 轮询 15 分钟（宏观数据低频，页面隐藏时 usePolling 自动暂停）。
 * 状态语义直出后端：active / degraded / stale / unavailable / disabled /
 * insufficient_history —— 刷新失败保留旧面板、显示 Warning，不清空图表、不把旧值改成 0。
 * 手动刷新只对 Owner 显示；访客可读全部数据，但看不到也触发不了 Worker 动作。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError } from '@/api/client';
import { macroApi, type MacroConditionsResponse } from '@/api/modules/macro';
import { usePolling } from '@/hooks/usePolling';
import { useAccess } from '@/hooks/useAccess';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import { SkeletonBlock, SkeletonCard } from '@/components/shared/Skeleton';
import CompositeCard from './CompositeCard';
import DriverList from './DriverList';
import FactorDetails from './FactorDetails';
import MacroHistoryChart, { HISTORY_RANGES, type HistoryRangeKey } from './MacroHistoryChart';
import ModuleGrid from './ModuleGrid';
import MacroTechnicalMatrix from '@/components/market/MacroTechnicalMatrix';

const POLL_MS = 15 * 60 * 1000;
/* 手动刷新期间的跟进节奏与放弃跟进的上限（审计 P2-26）。 */
const REFRESH_FOLLOW_INTERVAL_MS = 5_000;
const REFRESH_FOLLOW_TIMEOUT_MS = 3 * 60_000;

export const MACRO_SOURCE_NOTE =
  '宏观数据来自 FRED、纽约联储、联储理事会、芝加哥联储和 Cboe；跨资产代理使用 Option Pro 当前股票日线数据源。分数为过去 5 年历史分位，不是预测。';

const STATUS_CHIP: Record<
  MacroConditionsResponse['status'],
  { label: string; tone: string } | null
> = {
  active: null,
  degraded: { label: '部分数据缺失', tone: 'border-warn-600 bg-warn-50 text-warn-600' },
  stale: { label: '数据陈旧', tone: 'border-warn-600 bg-warn-50 text-warn-600' },
  unavailable: { label: '暂无快照', tone: 'border-line bg-paper-2 text-ink-500' },
  disabled: { label: '未启用', tone: 'border-line bg-paper-2 text-ink-500' },
  insufficient_history: { label: '历史不足', tone: 'border-line bg-paper-2 text-ink-500' },
};

type RefreshPhase = 'idle' | 'sending' | 'queued' | 'cooldown' | 'in_progress' | 'failed';

const REFRESH_LABEL: Record<RefreshPhase, string> = {
  idle: '刷新宏观数据',
  sending: '正在提交…',
  queued: '已排入刷新队列',
  cooldown: '冷却中',
  in_progress: '正在刷新',
  failed: '刷新未成功',
};

function DisabledNotice({ reason }: { reason: string | null }) {
  const missingKey = reason === 'fred_api_key_missing';
  return (
    <EmptyState
      icon="doc-quote"
      title={missingKey ? '宏观数据源尚未配置' : '宏观环境未启用'}
      description={
        missingKey
          ? '需要在服务器上配置 FRED 数据源密钥后，宏观环境才会开始积累快照。'
          : '本功能在配置中处于关闭状态。'
      }
      footnote="配置只能在服务器端完成；页面不显示任何密钥信息。"
    />
  );
}

/**
 * 技术侧分数由 /market 页传入，而不是本组件自己再拉一次 /strength/market：
 * 那会为了一张展示卡多发一次请求，而调用方本来就已经有这个数。
 */
interface MacroConditionsPanelProps {
  technicalScore?: number | null;
}

export default function MacroConditionsPanel({
  technicalScore = null,
}: MacroConditionsPanelProps = {}) {
  const { isOwner } = useAccess();
  const [range, setRange] = useState<HistoryRangeKey>('1Y');
  const [refreshPhase, setRefreshPhase] = useState<RefreshPhase>('idle');
  const [refreshNote, setRefreshNote] = useState<string | null>(null);

  const days = useMemo(
    () => HISTORY_RANGES.find((item) => item.key === range)?.days ?? 365,
    [range],
  );

  const conditionsQ = usePolling(() => macroApi.conditions(), POLL_MS);
  const historyQ = usePolling(() => macroApi.history(days), POLL_MS, [days]);

  /**
   * 手动刷新后短周期跟进（审计 P2-26）。
   *
   * 旧实现点完只记录动作的初始状态，既不轮询完成情况也不复位；而常规宏观轮询
   * 间隔是 15 分钟，于是「队列中」「处理中」可以长时间挂在那里，看起来像卡住了。
   * 这里在动作未完成期间每 5 秒读一次快照版本，版本一变即认定完成并停表。
   */
  const [refreshBaseline, setRefreshBaseline] = useState<string | null>(null);
  useEffect(() => {
    if (refreshPhase !== 'queued' && refreshPhase !== 'in_progress') return;
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - started > REFRESH_FOLLOW_TIMEOUT_MS) {
        window.clearInterval(timer);
        setRefreshPhase('idle');
        setRefreshNote('刷新仍未在预期时间内完成，面板会在下一次轮询更新。');
        return;
      }
      conditionsQ.refresh();
    }, REFRESH_FOLLOW_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refreshPhase, conditionsQ]);

  /* 快照版本变化即表示刷新已落地，明确复位而不是让状态一直挂着。 */
  const snapshotStamp = conditionsQ.data?.dataThrough ?? conditionsQ.data?.asOf ?? null;
  useEffect(() => {
    if (refreshPhase !== 'queued' && refreshPhase !== 'in_progress') return;
    if (refreshBaseline === null || snapshotStamp === null) return;
    if (snapshotStamp !== refreshBaseline) {
      setRefreshPhase('idle');
      setRefreshNote('宏观快照已更新。');
    }
  }, [snapshotStamp, refreshBaseline, refreshPhase]);

  const data = conditionsQ.data;
  const status = data?.status ?? 'unavailable';

  const onRefresh = useCallback(async () => {
    setRefreshPhase('sending');
    setRefreshNote(null);
    setRefreshBaseline(snapshotStamp);
    try {
      const result = await macroApi.refresh();
      if (result.reason === 'cooldown') {
        setRefreshPhase('cooldown');
        setRefreshNote(
          result.cooldownSeconds
            ? `刷新冷却中，${Math.round(result.cooldownSeconds)} 秒内只允许一次。`
            : '刷新冷却中。',
        );
      } else if (result.reason === 'already_running') {
        setRefreshPhase('in_progress');
        setRefreshNote('已有一次宏观刷新在进行，本次复用同一任务。');
      } else {
        setRefreshPhase('queued');
        setRefreshNote('已排入 Worker 队列，完成后面板会在下一次轮询更新。');
      }
    } catch (error) {
      setRefreshPhase('failed');
      const code = error instanceof ApiError ? error.bizCode : undefined;
      setRefreshNote(
        code === 'fred_api_key_missing'
          ? '服务器尚未配置宏观数据源密钥。'
          : code === 'worker_unavailable'
            ? 'Worker 当前不可用，稍后再试。'
            : error instanceof ApiError
              ? error.message
              : '刷新请求未成功。',
      );
    }
  }, [snapshotStamp]);

  /* 首次加载：骨架屏；已有数据时刷新失败保留旧面板。 */
  if (conditionsQ.loading && !data) {
    return (
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <SkeletonCard />
        </div>
        <div className="lg:col-span-7">
          <div className="card-surface p-5">
            <SkeletonBlock className="h-3 w-32" />
            <SkeletonBlock className="mt-4 h-[220px] w-full" />
          </div>
        </div>
      </div>
    );
  }

  if (!data || status === 'disabled') {
    return (
      <div className="card-surface">
        {conditionsQ.error && !data ? (
          <EmptyState
            variant="error"
            icon="doc-quote"
            title="宏观环境暂不可用"
            description={conditionsQ.error.message}
            action={
              <button
                type="button"
                onClick={conditionsQ.refresh}
                disabled={conditionsQ.refreshing}
                className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
              >
                重试
              </button>
            }
          />
        ) : (
          <DisabledNotice reason={data?.reason ?? null} />
        )}
      </div>
    );
  }

  const chip = STATUS_CHIP[status];
  const hasComposite = data.composite !== null && data.composite.score !== null;

  return (
    <div className="flex flex-col gap-4">
      {/* A. 标题行 */}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <p className="eyebrow">宏观环境 · MACRO CONDITIONS</p>
          <p className="mt-1 text-body-s text-ink-500">
            联储流动性、融资、国债、利率、信用、风险与外部冲击的 5 年历史分位。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-caption text-ink-400 tnum">
            数据截止 {data.dataThrough ?? '—'}
          </span>
          {chip && (
            <span
              className={cn(
                'rounded-xs border px-2 py-0.5 text-micro',
                chip.tone,
              )}
            >
              {chip.label}
            </span>
          )}
          {isOwner ? (
            <button
              type="button"
              onClick={() => void onRefresh()}
              disabled={refreshPhase === 'sending'}
              className={cn(
                'flex items-center gap-1.5 rounded-md border border-line bg-card px-3 py-1.5 text-caption text-ink-700 outline-none transition-colors duration-fast',
                'hover:border-line-strong hover:text-ink-900 focus-visible:border-brand-400 disabled:opacity-60',
              )}
            >
              <Icon
                name="refresh"
                size={13}
                className={refreshPhase === 'sending' ? 'animate-spin-once' : undefined}
              />
              {REFRESH_LABEL[refreshPhase]}
            </button>
          ) : (
            <span className="text-micro text-ink-400">登录后可手动刷新</span>
          )}
        </div>
      </div>

      {refreshNote && (
        <p
          role="status"
          className="rounded-md border border-line bg-paper-2 px-3 py-2 text-micro text-ink-600"
        >
          {refreshNote}
        </p>
      )}

      {data.warnings.length > 0 && status !== 'active' && (
        <p className="rounded-md border border-warn-600 bg-warn-50 px-3 py-2 text-micro leading-relaxed text-warn-600">
          上游告警：{data.warnings.slice(0, 4).join('、')}
          {data.warnings.length > 4 ? ` 等 ${data.warnings.length} 项` : ''}。
          面板继续显示上一份有效快照。
        </p>
      )}

      {/* B. 综合卡 + C. 历史图 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-5">
          {hasComposite && data.composite ? (
            <CompositeCard
              composite={data.composite}
              historyBasis={data.historyBasis}
              dataThrough={data.dataThrough}
            />
          ) : (
            <div className="card-surface h-full">
              <EmptyState
                icon="doc-quote"
                title="暂无正式综合分"
                description="有效模块不足 5 个时不输出正式综合分，也不会用 50 或上一次的分数顶替。"
              />
            </div>
          )}
        </div>
        <div className="lg:col-span-7">
          <MacroHistoryChart
            points={historyQ.data?.points ?? []}
            error={historyQ.error}
            onRetry={historyQ.refresh}
            modules={data.modules}
            loading={historyQ.loading}
            range={range}
            onRangeChange={setRange}
          />
        </div>
      </div>

      {/* D. 七模块网格 */}
      <ModuleGrid modules={data.modules} />

      {/* D2. 技术 × 结构性宏观二维状态（增量任务 Phase 1，仅展示） */}
      <MacroTechnicalMatrix
        technical={technicalScore ?? null}
        structural={data.structuralScore}
        structuralModules={data.structuralModules}
      />

      {/* E. 驱动因素 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <DriverList
          eyebrow="改善最多 · IMPROVING"
          title="7 日分数改善最多"
          drivers={data.drivers.improving}
          emptyText="暂无可比的 7 日历史快照，或本期没有分数上升的因子。缺少比较对象时这里留空，不显示 0。"
        />
        <DriverList
          eyebrow="恶化最多 · DETERIORATING"
          title="7 日分数恶化最多"
          drivers={data.drivers.deteriorating}
          emptyText="暂无可比的 7 日历史快照，或本期没有分数下降的因子。缺少比较对象时这里留空，不显示 0。"
        />
      </div>

      {/* F. 因子详情 */}
      <FactorDetails modules={data.modules} snapshotKey={snapshotStamp ?? ''} />

      {/* G. 来源说明 */}
      <SourceNote text={MACRO_SOURCE_NOTE} />
      <p className="-mt-2 text-micro leading-relaxed text-ink-400">
        「按当前修订值回算」的历史区间使用今天能看到的最新修订数据，不代表当时市场已知的分数；
        本地部署后每次实际抓取形成的快照才具备真实的点时语义。
        {data.scoringVersion ? ` 评分版本 ${data.scoringVersion}。` : ''}
      </p>
    </div>
  );
}
