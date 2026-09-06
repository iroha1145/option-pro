import AnalysisIcon from '@/components/shared/AnalysisIcon';
import SoftBadge from '@/components/shared/SoftBadge';
import StatusNotice from '@/components/shared/StatusNotice';
/** Market focus summary and stock assessments share the common card surface. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useAccess } from '@/hooks/useAccess';
import { usePolling } from '@/hooks/usePolling';
import { useToast } from '@/hooks/useToast';
import { catalystsContract } from './api';
import type { FocusCycleJob, MarketFocusCycle, NewsClassification } from './api';
import { ImpactValue, Led } from './bits';
import ConfirmDialog from './ConfirmDialog';
import { SkeletonBlock, SkeletonText } from '@/components/shared/Skeleton';
import InfoHint from '@/components/shared/InfoHint';
import Icon from '@/components/icons';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { cn } from '@/lib/utils';
import { fmtLocaleDate, fmtLocaleDateTime } from '@/lib/format';
import { t } from '../../i18n/core.ts';

const STAGES = [t('萌芽'), t('发酵'), t('主升'), t('退潮')] as const;
const DIR_ARROW: Record<NewsClassification, { icon: 'arrow-up-right' | 'arrow-down-right' | 'minus'; cls: string }> = {
  bullish: { icon: 'arrow-up-right', cls: 'text-up-700 bg-up-50' },
  bearish: { icon: 'arrow-down-right', cls: 'text-down-700 bg-down-50' },
  neutral: { icon: 'minus', cls: 'text-ink-500 bg-paper-2' },
};

/* 后端 horizon 枚举原样映射，不推断也不合并档位。 */
const HORIZON_LABEL: Record<'intraday' | 'days' | 'weeks' | 'uncertain', string> = {
  intraday: t('日内'),
  days: t('数日'),
  weeks: t('数周'),
  uncertain: t('跨度未定'),
};

/* 周期阶段横向步进条：当前 brand 实心（静态，不脉冲）；已过实心灰；未来空心 */
function StageStepper({ stage }: { stage: number }) {
  return (
    <ol className="flex items-center" aria-label={t('周期阶段 {stage} / 4 · {name}', { stage, name: STAGES[stage - 1] })}>
      {STAGES.map((label, i) => {
        const idx = i + 1;
        const past = idx < stage;
        const current = idx === stage;
        return (
          <li key={label} className={cn('flex items-center', i > 0 && 'flex-1')}>
            {i > 0 && (
              <span className={cn('mx-1 h-[2px] min-w-3 flex-1 rounded-full sm:mx-2', past || current ? 'bg-ink-300' : 'bg-line')} aria-hidden="true" />
            )}
            <motion.span
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 520, damping: 32, delay: 0.15 + i * 0.1 }}
              className="flex flex-col items-center gap-1"
            >
              {current ? (
                /* 阶段位置是静态状态，不做循环脉冲（LED 脉冲只给真实进行中的任务） */
                <span className="size-3 rounded-full border-2 border-brand-600 bg-brand-600" />
              ) : (
                <span
                  className={cn(
                    'size-3 rounded-full border-2',
                    past ? 'border-ink-400 bg-ink-400' : 'border-ink-300 bg-card',
                  )}
                />
              )}
              <span className={cn('text-[10px] leading-[14px]', current ? 'font-semibold text-brand-600' : past ? 'text-ink-500' : 'text-ink-300')}>
                {label}
              </span>
            </motion.span>
          </li>
        );
      })}
    </ol>
  );
}

/** 无效/缺失 ISO 时间 → '—'（周期未完成时 completed_at 为空，不能渲染 Invalid Date） */
function fmtCycleDate(iso: string, withTime: boolean): string {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return '—';
  return withTime
    ? fmtLocaleDateTime(iso)
    : fmtLocaleDate(iso, { month: '2-digit', day: '2-digit' });
}

const CYCLE_STATUS_CN: Record<string, string> = {
  preparing: t('等待提交'),
  pending: t('排队中'),
  in_progress: t('计算中'),
  queued: t('排队中'),
  cancel_requested: t('取消中'),
  cancelled: t('已取消'),
  canceled: t('已取消'),
  failed: t('失败'),
};

function CycleSummary({ cycle, compact = false }: { cycle: MarketFocusCycle; compact?: boolean }) {
  const statusCn = cycle.status && cycle.status !== 'completed' ? CYCLE_STATUS_CN[cycle.status] ?? cycle.status : null;
  return (
    <div>
      {cycle.latestAttempt && (
        <StatusNotice className="mb-4">
          <p>{t('最近一次更新失败，当前展示上次成功结果')}</p>
          <p className="mt-1 break-all font-mono text-micro text-ink-400 tnum">
            {cycle.latestAttempt.cycleId} · {fmtCycleDate(cycle.latestAttempt.startedAt, true)}
          </p>
        </StatusNotice>
      )}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className={cn('font-display font-semibold text-ink-900', compact ? 'text-[16px]' : 'text-[20px] leading-[28px]')}>
          {cycle.dominantEvent}
        </h3>
        {statusCn && (
          <SoftBadge tone="warn">
            <Led tone="warn" pulse={cycle.status === 'in_progress' || cycle.status === 'cancel_requested'} className="size-1.5" />
            {statusCn}
          </SoftBadge>
        )}
      </div>
      <p className="mt-2 font-mono text-micro leading-5 text-ink-400 tnum">
        {t('启动')} {fmtCycleDate(cycle.startedAt, false)} {t('· 生成')} {fmtCycleDate(cycle.generatedAt, true)} {t('· 样本')} {cycle.newsCount}{' '}
        {cycle.sampleLabel ?? t('条')}
      </p>
      <div className={cn('mt-5 grid min-w-0 gap-5', !compact && cycle.assessments.length > 0 && 'lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-6')}>
        <div className="min-w-0">
          {!compact && cycle.stage !== null && (
            <div className="mb-4 rounded-lg bg-paper-2 px-4 py-3">
              <StageStepper stage={cycle.stage} />
            </div>
          )}
          {cycle.headline && <p className="mb-3 text-body-s font-semibold leading-6 text-ink-800">{cycle.headline}</p>}
          <p className={cn('text-ink-600', compact ? 'text-caption leading-6' : 'text-body-s leading-7')}>
            {cycle.summary}
          </p>
          {!!cycle.uncertainties?.length && (
            <ul className="mt-4 space-y-2 border-t border-line pt-3">
              {cycle.uncertainties.slice(0, 4).map((u, i) => (
                <li key={i} className="flex items-start gap-2 text-caption leading-5 text-ink-500">
                  <Icon name="flag" size={12} className="mt-1 shrink-0 text-warn-600" />
                  <span>{u}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-4 break-all font-mono text-micro leading-5 text-ink-400 tnum">
            {cycle.cycleId} · {cycle.trigger === 'manual' ? t('手动触发') : t('定时生成')} · {cycle.model}
          </p>
        </div>
        {cycle.assessments.length > 0 && <section
          aria-label={t('逐股评估')}
          className={cn('min-w-0 border-t border-line pt-4', !compact && 'lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0')}
        >
          <h4 className="mb-3 text-caption font-semibold text-ink-700">{t('逐股评估')}</h4>
          <div className="divide-y divide-line">
            {cycle.assessments.map((a, i) => {
              const d = DIR_ARROW[a.direction];
              return (
                <motion.div
                  key={a.ticker}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: 0.2 + i * 0.05 }}
                  className="flex min-w-0 flex-col gap-2 py-3 first:pt-0 last:pb-0"
                >
                  <span className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className={cn('flex size-6 shrink-0 items-center justify-center rounded-md', d.cls)}>
                      <Icon name={d.icon} size={12} />
                    </span>
                    <span className="shrink-0 font-mono text-caption font-semibold text-ink-800">{a.ticker}</span>
                    {a.insufficientEvidence ? (
                      /* 后端在证据不足时强制 catalyst_bias 为 null。说「证据不足」，而不是画一个 0。 */
                      <SoftBadge tone="warn" className="shrink-0">
                        {t('证据不足')}
                      </SoftBadge>
                    ) : (
                      <ImpactValue value={a.catalystBias} bare />
                    )}
                    {a.confidence !== null && (
                      /* 偏向与置信共用一条说明（见 SCORE_HINTS.focusCycleAssessment）。
                         原先两个读数后面各挂一句常驻免责声明（「· 非收益」「· 非胜率」），
                         每行重复、又解释不了自己。声明留着，但收进这一个 ⓘ。 */
                      <SoftBadge className="shrink-0 font-mono">
                        {t('置信')} {Math.round(a.confidence * 100)}
                        <InfoHint hint={SCORE_HINTS.focusCycleAssessment} size={11} className="ml-1" />
                      </SoftBadge>
                    )}
                    {a.horizon && (
                      <SoftBadge className="shrink-0">
                        {HORIZON_LABEL[a.horizon]}
                      </SoftBadge>
                    )}
                  </span>
                  <span
                    className="min-w-0 break-words text-caption leading-5 text-ink-500"
                    title={a.note}
                  >
                    {a.note}
                  </span>
                </motion.div>
              );
            })}
          </div>
        </section>}
      </div>
    </div>
  );
}

export default function FocusCycleCard({ refreshToken = 0 }: { refreshToken?: number } = {}) {
  const { isOwner } = useAccess();
  const toast = useToast();
  /* refreshToken 参与依赖：页头「刷新」必须真的刷新焦点周期（审计 P2-21）。 */
  const latestQ = usePolling(() => catalystsContract.latestFocusCycle(), null, [refreshToken]);
  const prevQ = usePolling(() => catalystsContract.previousFocusCycle(), null, [refreshToken]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [job, setJob] = useState<FocusCycleJob | null>(null);
  const pollRef = useRef<number | null>(null);
  const pollGenRef = useRef(0);

  /* stopPoll 只杀计时器；换代（作废在途响应与收尾）只发生在真正「弃链」的
     场合：卸载、或新一次提交顶掉旧链。终态分支里调 stopPoll 后收尾定时器
     仍要跑——若把换代塞进 stopPoll，完成横幅的清理守卫永假、永不消失。 */
  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);
  const abandonPoll = useCallback(() => {
    pollGenRef.current += 1;
    stopPoll();
  }, [stopPoll]);

  useEffect(() => abandonPoll, [abandonPoll]);

  const submittingRef = useRef(false);
  const startJob = useCallback(async () => {
    if (submittingRef.current) return; // POST 在途时 running 仍为 false，会重复提交
    submittingRef.current = true;
    setConfirmOpen(false);
    try {
      const failedCycleId =
        latestQ.data?.latestAttempt?.cycleId
        ?? (
          latestQ.data?.status
          && ['failed', 'cancelled', 'canceled', 'budget_blocked'].includes(latestQ.data.status)
            ? latestQ.data.cycleId
            : null
        );
      const j = await catalystsContract.triggerFocusCycle(failedCycleId);
      setJob(j);
      toast.info(t('焦点周期计算已提交'), t('完成后自动刷新'));
      abandonPoll();
      if (!j.cycleId) {
        // 202 已受理但响应未携带周期编号：延迟拉取 latest 兜底，不误报失败
        window.setTimeout(() => {
          latestQ.refresh();
          setJob(null);
        }, 12_000);
        return;
      }
      const pollDeadline = Date.now() + 5 * 60_000;
      const BACKOFF = [2000, 3000, 5000, 8000, 10000];
      const generation = pollGenRef.current;
      let attempt = 0;
      const stillThisPoll = () => generation === pollGenRef.current;
      const tick = async () => {
        if (!stillThisPoll()) return;
        if (Date.now() >= pollDeadline) {
          stopPoll();
          latestQ.refresh();
          setJob(null);
          toast.error(t('焦点周期仍在处理中'), t('稍后刷新页面可继续查看结果'));
          return;
        }
        try {
          const next = await catalystsContract.focusCycleJob(j.cycleId!);
          if (!stillThisPoll()) return;
          setJob({ ...next });
          /* nFocusJob 已把状态归一到 queued|in_progress|completed|failed（cancelled
             归 failed、cancel_requested 归 in_progress），这里只认这四值——此前多写的
             cancelled/cancel_requested 分支永远不可达，还被 grep 测试钉住了死字串。 */
          if (next.status === 'completed' || next.status === 'failed') {
            stopPoll();
            if (next.status === 'completed') {
              toast.success(t('新焦点周期已生成'));
            } else {
              toast.error(t('焦点周期计算失败'), t('请稍后重试'));
            }
            // 成败都重读 latest：失败态（含被取消的）也要让卡片回到最新可用周期。
            latestQ.refresh();
            window.setTimeout(() => {
              // 按 cycleId 函数式清理：1200ms 内再提交不误伤新 job。
              setJob((cur) => (cur && cur.cycleId === j.cycleId ? null : cur));
            }, 1200);
            return;
          }
        } catch (error) {
          if (!stillThisPoll()) return;
          stopPoll();
          setJob(null);
          latestQ.refresh();
          toast.error(t('焦点周期状态读取失败'), error instanceof Error ? error.message : t('请稍后刷新页面'));
          return;
        }
        const delay = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
        attempt += 1;
        pollRef.current = window.setTimeout(() => void tick(), delay);
      };
      pollRef.current = window.setTimeout(() => void tick(), BACKOFF[0]);
    } catch (e) {
      toast.error(t('提交失败'), e instanceof Error ? e.message : undefined);
    } finally {
      submittingRef.current = false;
    }
  }, [abandonPoll, latestQ, stopPoll, toast]);

  const running = job && (job.status === 'queued' || job.status === 'in_progress');

  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <section
      aria-label={t("市场焦点周期")}
      className="card-surface p-4 sm:p-5 lg:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
        <div className="flex items-center gap-2.5">
          <span className="flex size-8 items-center justify-center rounded-md bg-ai-50 text-ai-600" aria-hidden="true">
            <AnalysisIcon size={17} />
          </span>
          <h2 className="text-h3 text-ink-800">{t('市场焦点周期')}</h2>
        </div>
        {isOwner ? (
          <button
            onClick={() => setConfirmOpen(true)}
            disabled={!!running}
            className={cn(
              'btn-primary',
              running
                ? 'cursor-wait opacity-60'
                : '',
            )}
          >
            {running ? (
              <>
                <Led tone="ai" pulse className="size-1.5 bg-white" />
                {t('周期计算中')}{job.progress !== null ? ` ${job.progress}%` : ''}
              </>
            ) : (
              <>
                <AnalysisIcon size={14} />
                {latestQ.data?.latestAttempt || latestQ.data?.status === 'failed'
                  ? t('重试焦点周期')
                  : t('触发新周期')}
              </>
            )}
          </button>
        ) : (
          <span className="text-micro text-ink-400">{t('登录后可手动触发新周期')}</span>
        )}
      </div>

      {/* 任务进度条 */}
      <AnimatePresence>
        {running && job.progress !== null && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 h-1 overflow-hidden rounded-pill bg-brand-100">
              <div className="h-full rounded-pill bg-brand-600 transition-[width] duration-500" style={{ width: `${job.progress}%` }} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="mt-4">
        {latestQ.loading && !latestQ.data ? (
          <div>
            <SkeletonBlock className="h-6 w-56" />
            <SkeletonText lines={3} className="mt-3" />
          </div>
        ) : latestQ.error ? (
          <p className="text-body-s text-ink-500">{t('暂无焦点周期数据')}</p>
        ) : latestQ.data ? (
          <CycleSummary cycle={latestQ.data} />
        ) : null}
      </div>

      {/* 与上一成功周期历史对照（accordion 折叠） */}
      {prevQ.data && (
        <div className="mt-5 border-t border-line pt-3">
          <button
            onClick={() => setHistoryOpen((v) => !v)}
            aria-expanded={historyOpen}
            className="flex min-h-10 w-full items-center justify-between gap-3 text-left text-caption text-ink-500 transition-colors hover:text-ink-800"
          >
            <span className="flex min-w-0 items-start gap-2">
              <Icon name="doc-quote" size={14} className="mt-0.5 shrink-0 text-ink-400" />
              <span>{t('与上一成功周期对照 ·')} {prevQ.data.dominantEvent}</span>
            </span>
            <Icon name="chevron-down" size={14} className={cn('shrink-0 transition-transform duration-ui', historyOpen && 'rotate-180')} />
          </button>
          <AnimatePresence initial={false}>
            {historyOpen && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
                className="overflow-hidden"
              >
                <div className="mt-3 rounded-lg bg-paper-2 p-4">
                  <CycleSummary cycle={prevQ.data} compact />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title={t("触发新的市场焦点周期？")}
        description={t("将基于当前热点准备区生成一次综合分析，消耗模型预算并计入每日额度；若当前版本已分析过，将明确重算一次。")}
        confirmLabel={t("开始计算")}
        onConfirm={() => void startJob()}
        onCancel={() => setConfirmOpen(false)}
      />
    </section>
  );
}
