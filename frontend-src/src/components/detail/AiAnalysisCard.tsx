/**
 * AI 股票分析（signal_analysis 任务）
 * 挂载时先 GET 回填已有任务；owner 确认费用后创建；2s 退避轮询；
 * 结果按后端结构化契约展开，缺字段时只显示摘要，不编造分项。
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { useAccess } from '@/hooks/useAccess';
import Icon from '@/components/icons';
import SoftBadge from '@/components/shared/SoftBadge';
import { createSignalAnalysisJob, getLatestSignalAnalysisJob } from './api';
import { useAiJob } from './useAiJob';
import { aiJobResultSummary } from '@/api/modules/ai-jobs';
import { isIndexSymbol } from '@/lib/quoteSymbol';
import { cn } from '@/lib/utils';
import {
  FINAL_BIAS_META,
  FLOW_DIRECTION_META,
  parseSignalAnalysisResult,
  type SignalAnalysisResult,
} from './signalAnalysis';
import { t } from '../../i18n/core.ts';

function ScoreChip({ label, value }: { label: string; value: number }) {
  return (
    <span className="rounded-xs bg-card px-1.5 py-0.5 font-mono text-micro text-ink-600">
      {label} {Math.round(value)}
    </span>
  );
}

function EvidenceList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2.5">
      <p className="text-micro text-ink-400">{title}</p>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={item} className="text-caption leading-5 text-ink-600">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SignalAnalysisView({ result }: { result: SignalAnalysisResult }) {
  const bias = FINAL_BIAS_META[result.final_bias];
  const flow = FLOW_DIRECTION_META[result.options_flow_read.net_direction];
  return (
    <div className="mt-3 rounded-md border border-ai-600/25 bg-ai-50 px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <SoftBadge tone={bias.tone}>{bias.label}</SoftBadge>
        <span className="text-micro text-ink-500">{result.horizon}</span>
        <span className="text-micro text-ink-500">{result.dominant_regime}</span>
      </div>
      <p className="mt-2.5 text-body-s leading-relaxed text-ink-800">{result.summary}</p>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <ScoreChip label={t('趋势置信')} value={result.trend_bias_confidence} />
        <ScoreChip label={t('见顶风险')} value={result.top_risk_confidence} />
        <ScoreChip label={t('见底机会')} value={result.bottom_opportunity_confidence} />
        <ScoreChip label={t('逢低质量')} value={result.dip_buy_quality} />
        <ScoreChip label={t('破位风险')} value={result.breakdown_risk} />
        <ScoreChip label={t('数据质量')} value={result.data_quality} />
      </div>
      <div className="mt-3 border-t border-ai-600/15 pt-2.5">
        <p className="flex flex-wrap items-center gap-1.5 text-caption text-ink-600">
          <span>{t('期权资金流')}</span>
          <SoftBadge tone={flow.tone}>{flow.label}</SoftBadge>
          <span className="font-mono text-micro text-ink-400">
            {t('置信')} {Math.round(result.options_flow_read.confidence)}
          </span>
        </p>
        <EvidenceList title={t('偏多证据')} items={result.options_flow_read.bullish_flow_evidence} />
        <EvidenceList title={t('偏空证据')} items={result.options_flow_read.bearish_flow_evidence} />
        <EvidenceList title={t('中性或未知资金流')} items={result.options_flow_read.unknown_or_neutral_flow} />
        <EvidenceList title={t('资金流警告')} items={result.options_flow_read.warnings} />
      </div>
      <EvidenceList title={t('多头证据')} items={result.top_evidence} />
      <EvidenceList title={t('底部证据')} items={result.bottom_evidence} />
      <EvidenceList title={t('逢低证据')} items={result.dip_buy_evidence} />
      <EvidenceList title={t('空头证据')} items={result.bearish_evidence} />
      <EvidenceList title={t('矛盾点')} items={result.contradictions} />
      <EvidenceList title={t('确认信号')} items={result.confirmation_signals} />
      <EvidenceList title={t('失效信号')} items={result.invalidation_signals} />
      <EvidenceList title={t('事件风险')} items={result.event_risks} />
      <EvidenceList title={t('支撑')} items={result.key_levels.support} />
      <EvidenceList title={t('阻力')} items={result.key_levels.resistance} />
      <EvidenceList title={t('均价锚')} items={result.key_levels.vwap_levels} />
      <EvidenceList title={t('期权关键位')} items={result.key_levels.options_levels} />
      <EvidenceList title={t('数据质量说明')} items={result.data_quality_notes} />
    </div>
  );
}

export default function AiAnalysisCard({ ticker }: { ticker: string }) {
  const { isOwner, loading, aiEnabled, aiAvailable } = useAccess();
  const { job, error, queryIssue, starting, start, cancel, resume, reset, adopt } = useAiJob();
  const [confirming, setConfirming] = useState(false);
  const [hydrating, setHydrating] = useState(true);

  useEffect(() => {
    let dead = false;
    setHydrating(true);
    void getLatestSignalAnalysisJob(ticker)
      .then((latest) => {
        if (!dead && latest) adopt(latest);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!dead) setHydrating(false);
      });
    return () => {
      dead = true;
    };
  }, [adopt, ticker]);

  const running =
    job &&
    (job.status === 'queued' ||
      job.status === 'in_progress' ||
      job.status === 'running');
  const parsed = job?.status === 'succeeded' ? parseSignalAnalysisResult(job.result) : null;
  const resultSummary =
    job?.status === 'succeeded' && !parsed ? aiJobResultSummary(job.result) : null;
  const canStart = isOwner && aiAvailable && !job && !starting && !confirming && !hydrating;

  return (
    <div className="rounded-lg border border-line bg-card p-4 shadow-sh-1">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-h3 text-ink-900">
          <Icon name="spark-ai" size={16} className="text-ai-600" />
          {isIndexSymbol(ticker) ? t('AI 指数分析') : t('AI 股票分析')}
        </p>
        {canStart && (
          <button
            onClick={() => setConfirming(true)}
            className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-on-accent shadow-btn-hi transition-[filter] duration-fast hover:brightness-105 active:brightness-95"
          >
            {t('开始分析')}
          </button>
        )}
      </div>

      {!isOwner && !loading && !job && !hydrating && (
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

      {isOwner && !loading && !aiAvailable && !job && !hydrating && (
        <p className="mt-3 text-caption text-ink-500">
          {aiEnabled ? t('AI 分析暂不可用') : t('AI 分析未启用')}
          {aiEnabled
            ? t('暂时无法生成新分析，已有分析仍可查看。')
            : t('分析生成已关闭，已有分析仍可查看。')}
        </p>
      )}

      <AnimatePresence initial={false}>
        {hydrating && !job && (
          <p className="mt-3 text-caption text-ink-500">{t('正在读取已有分析…')}</p>
        )}
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
                {isIndexSymbol(ticker)
                  ? t('分析该指数的技术信号和市场资料，使用 1 次分析额度。')
                  : t('分析 {ticker} 的技术信号、市场环境、期权和新闻，使用 1 次分析额度。', { ticker })}
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => {
                    setConfirming(false);
                    void start(() => createSignalAnalysisJob(ticker, false));
                  }}
                  disabled={starting}
                  className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-on-accent shadow-btn-hi hover:brightness-105 disabled:cursor-wait disabled:opacity-60"
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
                  {queryIssue === 'paused' || queryIssue === 'blocked' ? t('任务状态待确认') : job.status === 'queued'
                    ? t('排队中…')
                    : job.progress === null
                      ? t('模型分析中…')
                      : t('模型分析中 {pct}%', { pct: Math.round(job.progress) })}
                </span>
                {isOwner && (
                  <button onClick={() => void cancel()} className="text-ink-400 transition-colors hover:text-ink-600">
                    {t('取消任务')}
                  </button>
                )}
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

        {job?.status === 'succeeded' && parsed && (
          <motion.div
            key="done-structured"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <SignalAnalysisView result={parsed} />
            {isOwner && aiAvailable && (
              <button
                onClick={() => {
                  reset();
                  void start(() => createSignalAnalysisJob(ticker, true));
                }}
                className="mt-2 text-caption font-medium text-ai-600 hover:text-ai-600/80"
              >
                {t('重新分析')}
              </button>
            )}
          </motion.div>
        )}

        {job?.status === 'succeeded' && !parsed && resultSummary && (
          <motion.div
            key="done-summary"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="mt-3 rounded-md border border-ai-600/25 bg-ai-50 px-3.5 py-3">
              <p className="text-body-s leading-relaxed text-ink-800">{resultSummary}</p>
              {isOwner && aiAvailable && (
                <button
                  onClick={() => {
                    reset();
                    void start(() => createSignalAnalysisJob(ticker, true));
                  }}
                  className="mt-2 text-caption font-medium text-ai-600 hover:text-ai-600/80"
                >
                  {t('重新分析')}
                </button>
              )}
            </div>
          </motion.div>
        )}

        {(error ||
          job?.status === 'failed' ||
          job?.status === 'cancelled' ||
          (job?.status === 'succeeded' && !parsed && !resultSummary)) && (
          <motion.p
            key="error"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className={cn('mt-3 text-caption text-down-700')}
          >
            {error ??
              (job?.status === 'failed'
                ? t('分析任务失败')
                : job?.status === 'cancelled'
                  ? t('任务已取消')
                  : t('分析已完成，暂无摘要'))}{' '}
            {queryIssue === 'retrying' && <span>{t('正在重新查询原任务')}</span>}
            {(queryIssue === 'paused' || queryIssue === 'blocked') && <button onClick={resume} className="ml-2 font-medium text-ai-600">{t('继续查询原任务')}</button>}
            {isOwner && !running && (
              <button
                onClick={() => {
                  const retryPaid = !job
                    || job.status === 'failed'
                    || job.status === 'cancelled'
                    || (job.status === 'succeeded' && !parsed && !resultSummary);
                  reset();
                  if (retryPaid && aiAvailable) {
                    void start(() => createSignalAnalysisJob(ticker, true));
                  }
                }}
                className="ml-2 font-medium text-ai-600"
              >
                {t('重试')}
              </button>
            )}
            {job?.status === 'failed' && job.errorDetail && (
              <span className="mt-1 block break-all font-mono text-micro text-ink-400">
                {job.errorDetail}
              </span>
            )}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}
