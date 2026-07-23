/** B2 市场焦点周期卡：FOCUS CYCLE + 阶段步进条 + serif 摘要 + 逐股评估 + 历史对照折叠 + owner 手动触发 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useAccess } from '@/hooks/useAccess';
import { usePolling } from '@/hooks/usePolling';
import { useToast } from '@/components/Toast';
import { catalystsContract } from './api';
import type { FocusCycleJob, MarketFocusCycle, NewsClassification } from './api';
import { ImpactValue, Led } from './bits';
import ConfirmDialog from './ConfirmDialog';
import { SkeletonBlock, SkeletonText } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';

const STAGES = ['萌芽', '发酵', '主升', '退潮'] as const;
const DIR_ARROW: Record<NewsClassification, { icon: 'arrow-up-right' | 'arrow-down-right' | 'minus'; cls: string }> = {
  bullish: { icon: 'arrow-up-right', cls: 'text-up-700 bg-up-50' },
  bearish: { icon: 'arrow-down-right', cls: 'text-down-700 bg-down-50' },
  neutral: { icon: 'minus', cls: 'text-ink-500 bg-paper-2' },
};

/* 周期阶段横向步进条：当前 brand 实心 + led-pulse；已过实心灰；未来空心 */
function StageStepper({ stage }: { stage: number }) {
  return (
    <ol className="flex items-center" aria-label={`周期阶段 ${stage} / 4 · ${STAGES[stage - 1]}`}>
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
              initial={{ scale: 0, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 520, damping: 32, delay: 0.15 + i * 0.1 }}
              className="flex flex-col items-center gap-1"
            >
              {current ? (
                <motion.span
                  animate={{ boxShadow: ['0 0 0 0 rgba(46,70,224,.45)', '0 0 0 6px rgba(46,70,224,0)', '0 0 0 0 rgba(46,70,224,0)'] }}
                  transition={{ duration: 1.5, ease: [0.45, 0, 0.15, 1], repeat: Infinity }}
                  className="size-3 rounded-full border-2 border-brand-600 bg-brand-600"
                />
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
    ? d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

const CYCLE_STATUS_CN: Record<string, string> = {
  in_progress: '计算中',
  queued: '排队中',
  cancel_requested: '取消中',
  cancelled: '已取消',
  canceled: '已取消',
  failed: '失败',
};

function CycleSummary({ cycle, compact = false }: { cycle: MarketFocusCycle; compact?: boolean }) {
  const statusCn = cycle.status && cycle.status !== 'completed' ? CYCLE_STATUS_CN[cycle.status] ?? cycle.status : null;
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className={cn('font-display font-semibold text-ink-900', compact ? 'text-[16px]' : 'text-[20px] leading-[28px]')}>
          {cycle.dominantEvent}
        </h3>
        {statusCn && (
          <span className="inline-flex items-center gap-1.5 rounded-xs border border-warn-600/40 bg-warn-50 px-1.5 py-0.5 text-micro font-medium text-warn-600">
            <Led tone="warn" pulse={cycle.status === 'in_progress' || cycle.status === 'cancel_requested'} className="size-1.5" />
            {statusCn}
          </span>
        )}
        <span className="font-mono text-micro text-ink-400 tnum">
          {cycle.cycleId} · {cycle.trigger === 'manual' ? '手动触发' : '定时生成'} · {cycle.model}
        </span>
      </div>
      <p className="mt-1 font-mono text-micro text-ink-400 tnum">
        启动 {fmtCycleDate(cycle.startedAt, false)} · 生成 {fmtCycleDate(cycle.generatedAt, true)} · 样本 {cycle.newsCount}{' '}
        {cycle.sampleLabel ?? '条'}
      </p>
      {/* 一句话结论（live headline_summary） */}
      {cycle.headline && <p className="mt-2.5 text-body-s font-medium text-ink-800">{cycle.headline}</p>}
      {/* serif 引文摘要 */}
      <blockquote className="mt-3 border-l-[3px] border-line-strong pl-3.5">
        <p className={cn('font-display text-ink-800', compact ? 'text-[13px] leading-[22px]' : 'text-[15px] leading-[26px]')}>
          {cycle.summary}
        </p>
      </blockquote>
      {/* 不确定性（live market_uncertainties，如实罗列） */}
      {!!cycle.uncertainties?.length && (
        <ul className="mt-3 space-y-1">
          {cycle.uncertainties.slice(0, 4).map((u, i) => (
            <li key={i} className="flex items-start gap-1.5 text-micro text-ink-500">
              <Icon name="flag" size={11} className="mt-0.5 shrink-0 text-warn-600" />
              <span>{u}</span>
            </li>
          ))}
        </ul>
      )}
      {/* 逐股评估 */}
      <div className="mt-4 space-y-1.5">
        {cycle.assessments.map((a, i) => {
          const d = DIR_ARROW[a.direction];
          return (
            <motion.div
              key={a.ticker}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: 0.2 + i * 0.05 }}
              className="flex items-center gap-2.5 rounded-sm border border-line bg-card px-2.5 py-2"
            >
              <span className={cn('flex size-5 shrink-0 items-center justify-center rounded-xs', d.cls)}>
                <Icon name={d.icon} size={12} />
              </span>
              <span className="font-mono text-caption font-semibold text-ink-800">{a.ticker}</span>
              <ImpactValue value={a.impactScore} />
              <span className="min-w-0 flex-1 truncate text-micro text-ink-500" title={a.note}>
                {a.note}
              </span>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

export default function FocusCycleCard() {
  const { isOwner } = useAccess();
  const toast = useToast();
  const latestQ = usePolling(() => catalystsContract.latestFocusCycle(), null);
  const prevQ = usePolling(() => catalystsContract.previousFocusCycle(), null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [job, setJob] = useState<FocusCycleJob | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPoll, [stopPoll]);

  const startJob = useCallback(async () => {
    setConfirmOpen(false);
    try {
      const j = await catalystsContract.triggerFocusCycle();
      setJob(j);
      toast.info('焦点周期计算已提交', '完成后自动刷新');
      stopPoll();
      if (!j.cycleId) {
        // 202 已受理但响应未携带周期编号：延迟拉取 latest 兜底，不误报失败
        window.setTimeout(() => {
          latestQ.refresh();
          setJob(null);
        }, 12_000);
        return;
      }
      const pollDeadline = Date.now() + 5 * 60_000;
      pollRef.current = window.setInterval(async () => {
        if (Date.now() >= pollDeadline) {
          stopPoll();
          latestQ.refresh();
          setJob(null);
          toast.error('焦点周期仍在处理中', '稍后刷新页面可继续查看结果');
          return;
        }
        try {
          const next = await catalystsContract.focusCycleJob(j.cycleId!);
          setJob({ ...next });
          if (next.status === 'completed' || next.status === 'failed') {
            stopPoll();
            if (next.status === 'completed') {
              toast.success('新焦点周期已生成');
              latestQ.refresh();
            } else {
              toast.error('焦点周期计算失败', '请稍后重试');
            }
            window.setTimeout(() => setJob(null), 1200);
          }
        } catch (error) {
          stopPoll();
          setJob(null);
          latestQ.refresh();
          toast.error('焦点周期状态读取失败', error instanceof Error ? error.message : '请稍后刷新页面');
        }
      }, 2000);
    } catch (e) {
      toast.error('提交失败', e instanceof Error ? e.message : undefined);
    }
  }, [latestQ, stopPoll, toast]);

  const running = job && (job.status === 'queued' || job.status === 'in_progress');

  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1] }}
      aria-label="市场焦点周期"
      className="rounded-lg border border-brand-100 bg-brand-50/60 p-5 shadow-sh-1"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="eyebrow">FOCUS CYCLE · 市场焦点周期</p>
        {isOwner ? (
          <button
            onClick={() => setConfirmOpen(true)}
            disabled={!!running}
            className={cn(
              'flex items-center gap-2 rounded-md border px-3 py-1.5 text-caption font-medium transition-colors duration-fast',
              running
                ? 'cursor-wait border-brand-200 bg-brand-100/60 text-brand-600'
                : 'border-line bg-card text-ink-600 hover:border-brand-400 hover:text-brand-600',
            )}
          >
            {running ? (
              <>
                <Led tone="brand" pulse className="size-1.5" />
                周期计算中{job.progress !== null ? ` ${job.progress}%` : ''}
              </>
            ) : (
              <>
                <Icon name="spark-ai" size={14} className="text-ai-600" />
                触发新周期
              </>
            )}
          </button>
        ) : (
          <span className="text-micro text-ink-400">登录后可手动触发新周期</span>
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
              <div className="h-full rounded-pill bg-brand-600 transition-all duration-500" style={{ width: `${job.progress}%` }} />
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
          <p className="text-body-s text-ink-500">焦点周期快照暂不可用 · 留空优于编造</p>
        ) : latestQ.data ? (
          <>
            {latestQ.data.stage !== null && <StageStepper stage={latestQ.data.stage} />}
            <div className={latestQ.data.stage !== null ? 'mt-4' : ''}>
              <CycleSummary cycle={latestQ.data} />
            </div>
          </>
        ) : null}
      </div>

      {/* 与上一成功周期历史对照（accordion 折叠） */}
      {prevQ.data && (
        <div className="mt-4 border-t border-brand-100 pt-3">
          <button
            onClick={() => setHistoryOpen((v) => !v)}
            aria-expanded={historyOpen}
            className="flex w-full items-center justify-between text-caption text-ink-500 transition-colors hover:text-ink-800"
          >
            <span className="flex items-center gap-1.5">
              <Icon name="doc-quote" size={13} className="text-ink-400" />
              与上一成功周期对照 · {prevQ.data.dominantEvent}
            </span>
            <Icon name="chevron-down" size={14} className={cn('transition-transform duration-ui', historyOpen && 'rotate-180')} />
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
                <div className="mt-3 rounded-md border border-line bg-card p-4">
                  <CycleSummary cycle={prevQ.data} compact />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="触发新的市场焦点周期？"
        description="将基于当前热点准备区生成一次综合分析，消耗模型预算并计入每日额度；若当前版本已分析过，将明确重算一次。"
        confirmLabel="确认触发"
        onConfirm={() => void startJob()}
        onCancel={() => setConfirmOpen(false)}
      />
    </motion.section>
  );
}
