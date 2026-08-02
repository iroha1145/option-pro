/**
 * AI 股票分析（signal_analysis 任务）
 * owner：确认费用 → 创建任务 → 2.5s 轮询 → 结果渲染（accordion 展开）
 * visitor：「登录后可用模型分析」
 */
import { useState } from 'react';
import { Link } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { useAccess } from '@/hooks/useAccess';
import Icon from '@/components/icons';
import { createSignalAnalysisJob } from './api';
import { AI_DISCLAIMER, useAiJob } from './useAiJob';
import { aiJobResultSummary } from '@/api/modules/ai-jobs';
import { t } from '../../i18n/core.ts';

export default function AiAnalysisCard({ ticker }: { ticker: string }) {
  const { isOwner, loading } = useAccess();
  const { job, error, starting, start, cancel, reset } = useAiJob();
  const [confirming, setConfirming] = useState(false);

  const running =
    job &&
    (job.status === 'queued' ||
      job.status === 'in_progress' ||
      job.status === 'running');
  const resultSummary =
    job?.status === 'succeeded' ? aiJobResultSummary(job.result) : null;

  return (
    <div className="rounded-lg border border-line bg-card p-4 shadow-sh-1">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-h3 text-ink-900">
          <Icon name="spark-ai" size={16} className="text-ai-600" />
          {t('AI 股票分析')}
        </p>
        {isOwner && !job && !starting && !confirming && (
          <button
            onClick={() => setConfirming(true)}
            className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-white shadow-btn-hi transition-[filter] duration-fast hover:brightness-105 active:brightness-95"
          >
            {t('开始分析')}
          </button>
        )}
      </div>

      {!isOwner && !loading && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-dashed border-line-strong bg-card-warm px-3 py-2.5">
          <p className="text-caption text-ink-500">{t('登录后可用模型分析')}</p>
          <Link
            to="/login"
            state={{ from: `${window.location.pathname}${window.location.search}` }}
            className="shrink-0 text-caption font-medium text-brand-600 hover:text-brand-700"
          >
            {t('去登录')}
          </Link>
        </div>
      )}

      <AnimatePresence initial={false}>
        {isOwner && starting && !job && (
          <p className="mt-3 text-caption text-ink-500">{t('正在创建分析任务…')}</p>
        )}
        {isOwner && confirming && !job && (
          <motion.div
            key="confirm"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 rounded-md bg-ai-50 px-3 py-2.5">
              <p className="text-caption text-ink-600">
                {t('将综合')} {ticker} {t('的技术信号、大盘与宏观环境、期权链和相关新闻生成分析报告，消耗 1 次 AI 额度，是否继续？')}
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => {
                    setConfirming(false);
                    void start(() => createSignalAnalysisJob(ticker));
                  }}
                  disabled={starting}
                  className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-white shadow-btn-hi hover:brightness-105 disabled:cursor-wait disabled:opacity-60"
                >
                  {t('开始分析')}
                </button>
                <button
                  onClick={() => setConfirming(false)}
                  className="rounded-md border border-line-strong bg-card px-3 py-1.5 text-caption text-ink-600 shadow-btn hover:bg-paper-2"
                >
                  {t('取消')}
                </button>
              </div>
            </div>
          </motion.div>
        )}

        {running && (
          <motion.div
            key="running"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3">
              <div className="flex items-center justify-between text-caption text-ink-500">
                <span className="flex items-center gap-1.5">
                  <span className="size-1.5 animate-led-pulse rounded-full bg-ai-600" aria-hidden="true" />
                  {job.status === 'queued'
                    ? t('排队中…')
                    : job.progress === null
                      ? t('模型正在处理 · 暂无进度百分比')
                      : t('模型分析中 {pct}%', { pct: Math.round(job.progress) })}
                </span>
                <button onClick={() => void cancel()} className="text-ink-400 transition-colors hover:text-ink-600">
                  {t('取消任务')}
                </button>
              </div>
              {job.progress !== null && (
                <div className="mt-1.5 h-1 overflow-hidden rounded-pill bg-line">
                  <div
                    className="h-full rounded-pill bg-ai-600 transition-[width] duration-ui ease-paper"
                    style={{ width: `${job.progress}%` }}
                  />
                </div>
              )}
            </div>
          </motion.div>
        )}

        {job?.status === 'succeeded' && resultSummary && (
          <motion.div
            key="done"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 rounded-md border border-ai-600/25 bg-ai-50 px-3.5 py-3">
              <p className="text-body-s leading-relaxed text-ink-800">{resultSummary}</p>
              <p className="mt-2 text-micro text-ink-400">{AI_DISCLAIMER}</p>
              <button onClick={reset} className="mt-2 text-caption font-medium text-ai-600 hover:text-ai-600/80">
                {t('重新分析')}
              </button>
            </div>
          </motion.div>
        )}

        {(error ||
          job?.status === 'failed' ||
          job?.status === 'cancelled' ||
          (job?.status === 'succeeded' && !resultSummary)) && (
          <motion.p
            key="error"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="mt-3 text-caption text-down-700"
          >
            {error ??
              (job?.status === 'failed'
                ? t('分析任务失败')
                : job?.status === 'cancelled'
                  ? t('任务已取消')
                  : t('任务已完成，但未返回可显示的结构化摘要'))}{' '}
            ·{' '}
            <button onClick={reset} className="font-medium text-ai-600">
              {t('重试')}
            </button>
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}
