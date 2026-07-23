import { motion, useReducedMotion } from 'framer-motion';
import Icon from '@/components/icons';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import { useAccess } from '@/hooks/useAccess';
import { usePolling } from '@/hooks/usePolling';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import { Led } from './bits';
import { fetchNewsAnalysisProgress } from './analysisProgressApi';
import type { NewsAnalysisProgress } from './analysisProgressContract';

function Metric({
  label,
  value,
  tone = 'text-ink-900',
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <div className="min-w-0 rounded-sm border border-line bg-card px-3 py-2.5">
      <p className="text-micro text-ink-400">{label}</p>
      <p className={cn('mt-0.5 font-mono text-data-m tnum', tone)}>{value}</p>
    </div>
  );
}

function progressHeadline(progress: NewsAnalysisProgress): string {
  if (progress.status === 'idle') return '暂无新闻分析任务';
  if (progress.status === 'completed') return '最近一批任务已结束';
  if (progress.currentIndex !== null) {
    return progress.currentPhase === 'provider_queued'
      ? `第 ${progress.currentIndex} / ${progress.total} 条已提交，等待供应方执行`
      : `正在处理第 ${progress.currentIndex} / ${progress.total} 条`;
  }
  if (progress.inProgress > 1) return `${progress.inProgress} 条任务正在处理`;
  if (progress.awaitingValidation > 0 && progress.waiting === 0) {
    return `${progress.awaitingValidation} 条结果等待简体中文校验`;
  }
  return '任务正在队列中推进';
}

function OwnerAnalysisProgressCard() {
  const reduceMotion = useReducedMotion();
  const progressQ = usePolling(fetchNewsAnalysisProgress, 5_000);
  const progress = progressQ.data;

  if (progressQ.loading && !progress) {
    return (
      <section aria-label="新闻分析进度" className="card-surface mt-4 px-5 py-4">
        <SkeletonBlock className="h-5 w-44" />
        <SkeletonBlock className="mt-3 h-2 w-full" />
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {Array.from({ length: 6 }, (_, index) => (
            <SkeletonBlock key={index} className="h-14" />
          ))}
        </div>
      </section>
    );
  }

  if (progressQ.error && !progress) {
    return (
      <section aria-label="新闻分析进度" className="card-surface mt-4 px-5 py-4">
        <div className="flex items-center gap-2">
          <Led tone="down" />
          <p className="text-body-s font-medium text-ink-800">真实分析进度暂不可用</p>
        </div>
        <p className="mt-1 text-micro text-ink-400">{progressQ.error.message}</p>
      </section>
    );
  }

  if (!progress) return null;
  const processing = progress.waiting > 0 || progress.inProgress > 0;
  const validating = !processing && progress.awaitingValidation > 0;
  const otherTerminal =
    progress.cancelled + progress.insufficientContext + progress.budgetBlocked;
  const sourceLabel =
    progress.batchSource === 'scheduled'
      ? '定时批次'
      : progress.batchSource === 'manual'
        ? '手动任务'
        : '尚无批次';

  return (
    <motion.section
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduceMotion ? { duration: 0 } : { duration: 0.36, ease: [0.16, 1, 0.3, 1] }}
      aria-label="新闻分析进度"
      aria-live="polite"
      className="card-surface mt-4 px-4 py-4 sm:px-5"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="eyebrow flex items-center gap-2">
            <Icon name="spark-ai" size={13} className="text-ai-600" />
            NEWS ANALYSIS · 新闻分析进度
          </p>
          <div className="mt-1.5 flex items-center gap-2">
            <Led
              tone={processing ? 'ai' : validating ? 'warn' : progress.total > 0 ? 'up' : 'muted'}
              pulse={processing}
            />
            <p className="text-body-s font-medium text-ink-800">{progressHeadline(progress)}</p>
          </div>
        </div>
        <p className="font-mono text-micro text-ink-400 tnum">
          {sourceLabel}
          {progress.lastUpdatedAt ? ` · 更新 ${fmtRelative(progress.lastUpdatedAt)}` : ''}
        </p>
      </div>

      {progress.total > 0 && (
        <div className="mt-3">
          <div className="flex items-center justify-between font-mono text-micro text-ink-400 tnum">
            <span>
              已结束 {progress.finished} / {progress.total}
            </span>
            <span className="text-ink-600">{progress.progressPercent}%</span>
          </div>
          <div
            role="progressbar"
            aria-label="最近一批新闻分析完成比例"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress.progressPercent}
            className="mt-1.5 h-1.5 overflow-hidden rounded-pill bg-paper-2"
          >
            <div
              className="h-full w-full origin-left rounded-pill bg-ai-600 transition-transform duration-300 motion-reduce:transition-none"
              style={{ transform: `scaleX(${progress.progressPercent / 100})` }}
            />
          </div>
        </div>
      )}

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
        <Metric label="总任务" value={progress.total} />
        <Metric label="已结束" value={progress.finished} />
        <Metric label="成功" value={progress.succeeded} tone="text-up-700" />
        <Metric
          label="待校验"
          value={progress.awaitingValidation}
          tone={progress.awaitingValidation ? 'text-warn-600' : undefined}
        />
        <Metric
          label="被拒绝"
          value={progress.rejected}
          tone={progress.rejected ? 'text-down-700' : undefined}
        />
        <Metric label="失败" value={progress.failed} tone={progress.failed ? 'text-down-700' : undefined} />
        <Metric label="等待" value={progress.waiting} tone={progress.waiting ? 'text-warn-600' : undefined} />
        <Metric label="进行中" value={progress.inProgress} tone={progress.inProgress ? 'text-ai-600' : undefined} />
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-ink-400">
        <span className="font-mono tnum">
          当前队列 {progress.queueTotal} 条 · 等待 {progress.queueWaiting} · 进行中 {progress.queueInProgress}
        </span>
        {progress.currentNewsId !== null && (
          <span className="font-mono tnum">新闻编号 {progress.currentNewsId}</span>
        )}
        {otherTerminal > 0 && (
          <span>
            另有 {otherTerminal} 条因取消、信息不足或预算限制结束
          </span>
        )}
        {progress.rejected > 0 && (
          <span className="text-down-700">
            被拒绝结果未通过简体中文或结构校验，不会进入新闻正文
          </span>
        )}
        {progressQ.error && (
          <span className="text-warn-600">最新状态读取失败，显示上次结果</span>
        )}
      </div>
    </motion.section>
  );
}

export default function AnalysisProgressCard() {
  const { isOwner } = useAccess();
  return isOwner ? <OwnerAnalysisProgressCard /> : null;
}
