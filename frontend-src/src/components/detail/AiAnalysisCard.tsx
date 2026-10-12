import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import AnalysisSources from '@/components/shared/AnalysisSources';
import ThinkingLabel from '@/components/shared/ThinkingLabel';
import { aiModelLabel } from '@/lib/aiModelLabel';
import { useAccess } from '@/hooks/useAccess';
import Icon from '@/components/icons';
import { ApiError } from '@/api/client';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import { aiJobDeferralMessage, aiJobErrorMessage, aiJobResultSummary } from '@/api/aiJobNormalize';
import { isIndexSymbol } from '@/lib/quoteSymbol';
import { loginReturnState } from '@/lib/loginReturn';
import { createSignalAnalysisJob, getStockAnalysisView } from './api';
import { useAiJob } from './useAiJob';
import StockAnalysisReport from './StockAnalysisReport';
import type { StockAnalysisView } from './stockAnalysisTypes';
import type { AiJob } from '@/api/types';
import { localeTag, t } from '../../i18n/core.ts';
const active = (job: AiJob | null) => !!job && ['queued', 'in_progress', 'running'].includes(job.status);
function timeLabel(stamp: string) {
  const date = new Date(stamp);
  return Number.isFinite(date.getTime()) ? date.toLocaleString(localeTag(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' }) : stamp;
}
/** Remount across identities: no earlier account's task or quota survives a session change. */
export default function AiAnalysisCard({ ticker }: { ticker: string }) {
  const { isOwner, isSignedIn, username, loading, identityUnavailable } = useAccess();
  if (!isSignedIn) return <div className="card-surface p-5">
    <CardTitle ticker={ticker} />
    {loading ? <p className="mt-3 text-caption text-ink-500">{t('正在确认登录状态')}</p>
      : identityUnavailable ? <p className="mt-3 text-caption text-ink-500">{t('暂时无法确认登录状态')}</p>
      : <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-dashed border-line-strong bg-card-warm px-3 py-2.5">
        <p className="min-w-0 text-caption text-ink-500">{t('登录后可用模型分析')}</p>
        <Link to="/login" state={loginReturnState(window.location)} className="shrink-0 whitespace-nowrap text-caption font-medium text-brand-600 hover:text-brand-700">{t('去登录')}</Link>
      </div>}
  </div>;
  return <SignedInAnalysis key={`${isOwner ? 'owner' : 'customer'}:${username ?? ''}:${ticker}`} ticker={ticker} isOwner={isOwner} />;
}
function CardTitle({ ticker }: { ticker: string }) {
  return (
    <h3 className="flex min-w-0 items-center gap-2 text-h3 text-ink-900">
      <Icon name="spark-ai" size={16} className="shrink-0 text-ai-600" />
      <span className="truncate">{isIndexSymbol(ticker) ? t('AI 指数分析') : t('AI 股票分析')}</span>
    </h3>
  );
}
function SignedInAnalysis({ ticker, isOwner }: { ticker: string; isOwner: boolean }) {
  const { job, error, queryIssue, starting, start, cancel, resume, adopt, clear } = useAiJob();
  const [view, setView] = useState<StockAnalysisView | null>(null);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [readingOriginal, setReadingOriginal] = useState(false);
  const [originalReadError, setOriginalReadError] = useState<string | null>(null);
  const originalGenerationRef = useRef(0);
  const originalJobRef = useRef(job?.id);
  originalJobRef.current = job?.id;
  const aliveRef = useRef(true);
  const readGenerationRef = useRef(0);
  const readControllerRef = useRef<AbortController | null>(null);
  const terminalRef = useRef('');
  const initialRef = useRef(true);
  const refresh = useCallback(async () => {
    const generation = ++readGenerationRef.current;
    readControllerRef.current?.abort();
    const controller = new AbortController();
    readControllerRef.current = controller;
    setRefreshing(true);
    try {
      const next = await getStockAnalysisView(ticker, controller.signal);
      if (!aliveRef.current || generation !== readGenerationRef.current) return;
      setView(next);
      setReadError(null);
      if (initialRef.current) {
        initialRef.current = false;
        adopt(next.activeJob ?? next.currentJob ?? next.latestJob);
      } else if (next.activeJob ?? next.currentJob) {
        adopt(next.activeJob ?? next.currentJob ?? null);
      }
    } catch (issue) {
      if (!aliveRef.current || generation !== readGenerationRef.current) return;
      setReadError(issue instanceof ApiError && issue.bizCode === 'account_login_required'
        ? t('登录已过期，请重新登录') : t('暂时无法读取股票报告'));
    } finally {
      if (aliveRef.current && generation === readGenerationRef.current) { setLoading(false); setRefreshing(false); }
    }
  }, [adopt, ticker]);
  useEffect(() => {
    aliveRef.current = true;
    void refresh();
    return () => { aliveRef.current = false; readGenerationRef.current += 1; readControllerRef.current?.abort(); originalGenerationRef.current += 1; clear(); };
  }, [refresh, clear]);
  useEffect(() => {
    if (!job || active(job)) return;
    const identity = `${job.id}:${job.status}`;
    if (terminalRef.current === identity) return;
    terminalRef.current = identity;
    void refresh();
  }, [job, refresh]);
  useEffect(() => {
    const reread = () => { if (!document.hidden) void refresh(); };
    window.addEventListener('focus', reread);
    document.addEventListener('visibilitychange', reread);
    const resetAt = view?.quota?.resetAt;
    const wait = resetAt ? Date.parse(resetAt) - Date.now() : NaN;
    // The server is the time authority; a local clock only schedules a fresh read.
    // A response can arrive just after its close boundary; schedule one reread
    // then as well, so an exhausted prior-period quota cannot remain stuck.
    const timer = Number.isFinite(wait) && wait < 2147483000 ? setTimeout(reread, Math.max(500, wait + 500)) : null;
    return () => { window.removeEventListener('focus', reread); document.removeEventListener('visibilitychange', reread); if (timer) clearTimeout(timer); };
  }, [refresh, view?.quota?.resetAt]);
  const running = active(job);
  const completedJob = job?.status === 'succeeded' && job.result != null ? job : null;
  const latest = view?.latestJob ?? null;
  const reportJob = latest && (!completedJob || Date.parse(latest.updatedAt) >= Date.parse(completedJob.updatedAt)) ? latest : completedJob;
  const summary = reportJob ? aiJobResultSummary(reportJob.result) : null;
  const retryJob = view?.canRetry ? view.currentJob : null;
  const canGenerate = !!view?.enabled && (!!view.canGenerate || !!retryJob) && !running && !starting && !loading && !refreshing && !readError;
  const create = async () => {
    setSubmitError(null);
    try { return await createSignalAnalysisJob(ticker, retryJob?.id); }
    catch (issue) {
      if (aliveRef.current && issue instanceof ApiError && issue.bizCode === 'stock_analysis_daily_limit') {
        setSubmitError(t('今日分析额度已用完，额度重置后再试'));
        void refresh();
      }
      throw issue;
    }
  };
  const onGenerate = async () => {
    setConfirming(false);
    await start(create);
    if (aliveRef.current) void refresh();
  };
  const unresolvedFailure = job?.status === 'failed' && !view?.canRetry;
  const canReadOriginal = job?.status === 'failed' && !view?.canRetry && !!job.id;
  const rereadOriginal = async () => {
    if (!canReadOriginal || !job || readingOriginal) return;
    const id = job.id;
    const generation = ++originalGenerationRef.current;
    setReadingOriginal(true);
    setOriginalReadError(null);
    try {
      const saved = await aiJobsApi.get(id);
      if (!aliveRef.current || generation !== originalGenerationRef.current || originalJobRef.current !== id) return;
      if (saved.id !== id || saved.kind !== 'signal-analysis') throw new Error('job_identity_mismatch');
      adopt(saved);
      void refresh();
    } catch {
      if (aliveRef.current && generation === originalGenerationRef.current && originalJobRef.current === id) {
        setOriginalReadError(t('暂时无法读取任务状态，任务可能仍在后台运行'));
      }
    } finally {
      if (aliveRef.current && generation === originalGenerationRef.current) setReadingOriginal(false);
    }
  };
  const deferral = aiJobDeferralMessage(job);
  const usage = reportJob?.usage;
  const usageLabels: [string, string][] = [
    ['input_tokens', t('输入用量')], ['output_tokens', t('输出用量')],
    ['cache_read_input_tokens', t('缓存读取用量')], ['cache_creation_input_tokens', t('缓存写入用量')],
    ['cached_input_tokens', t('缓存读取用量')],
    ['cache_creation_5m_input_tokens', t('五分钟缓存写入用量')],
    ['cache_creation_1h_input_tokens', t('一小时缓存写入用量')],
  ];
  const displayedUsage = usageLabels.filter(([key]) => typeof usage?.[key] === 'number');
  return <div className="card-surface p-5">
    <div className="flex items-center justify-between gap-3"><CardTitle ticker={ticker} />
      {!running && !starting && !confirming && !unresolvedFailure && <button onClick={() => setConfirming(true)} disabled={!canGenerate} className="btn-ai shrink-0 whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50">{retryJob ? t('重试分析') : reportJob ? t('重新分析') : t('生成分析')}</button>}
    </div>
    {loading && <p className="mt-3 text-caption text-ink-500" role="status">{t('正在读取股票报告')}</p>}
    {view?.quota && <div className="mt-3 text-micro leading-relaxed text-ink-500">
      <p>{t('今日剩余 {remaining}/{limit} 次 · 所有股票共用', { remaining: view.quota.remaining, limit: view.quota.limit })}</p>
      <p>{view.cacheResetAt
        ? t('额度与报告缓存于 {time} 刷新', { time: timeLabel(view.cacheResetAt) })
        : t('下次重置 {time} · 美股收盘时', { time: timeLabel(view.quota.resetAt) })}</p>
      <p>{t('查看已有共享报告不扣次数')}</p>
    </div>}
    {view?.latestJobCached && reportJob && !running && <p className="mt-2 text-caption text-ink-500">{t('本时段已有共享报告')}</p>}
    {view?.cacheResetAt && !view.latestJobCached && reportJob && !running && <p className="mt-2 text-caption text-ink-500">{t('当前显示历史报告，缓存已失效')}</p>}
    {view && !view.enabled && <p className="mt-2 text-caption text-ink-500">{t('个股分析已关闭')}</p>}
    {readError && <p className="mt-3 text-caption text-danger-700" role="status">{readError} <button onClick={() => void refresh()} disabled={refreshing} className="text-ai-600">{t('重试')}</button></p>}
    {confirming && <div className="mt-3 rounded-md bg-ai-50 px-3 py-2.5">
      <p className="text-caption text-ink-600">{retryJob ? t('上次分析未完成。确认重试将再使用今日 1 次额度。') : t('生成个股深度研究报告，将使用今日 1 次额度。')}</p>
      <div className="mt-2 flex flex-wrap gap-2"><button onClick={() => void onGenerate()} disabled={!canGenerate} className="btn-ai shrink-0 whitespace-nowrap disabled:opacity-50">{t('生成分析')}</button><button onClick={() => setConfirming(false)} className="control-button shrink-0 whitespace-nowrap">{t('取消')}</button></div>
    </div>}
    {starting && <p className="mt-3" role="status"><ThinkingLabel live={false}>{t('正在创建分析任务…')}</ThinkingLabel></p>}
    {running && job && <div className="mt-3">
      <div className="flex items-center justify-between gap-3 text-caption text-ink-500">
        <ThinkingLabel live={!(queryIssue === 'paused' || queryIssue === 'blocked' || job.cancelRequested)} className="min-w-0 truncate whitespace-nowrap">
          {queryIssue === 'paused' || queryIssue === 'blocked' ? t('任务状态待确认') : job.cancelRequested ? t('已请求取消') : job.status === 'queued' ? t('排队中…') : job.progress === null ? t('模型分析中…') : t('模型分析中 {pct}%', { pct: Math.round(job.progress) })}
        </ThinkingLabel>
        {isOwner && <button onClick={() => void cancel()} disabled={job.cancelRequested} className="shrink-0 whitespace-nowrap text-ink-400 hover:text-ink-600 disabled:opacity-50">{t('取消分析')}</button>}
      </div>
      {deferral && <p className="mt-1 text-micro text-ink-400">{deferral}</p>}
      {job.progress !== null && <div className="mt-1.5 h-1 overflow-hidden rounded-pill bg-line"><div className="h-full bg-ai-600 transition-[width] duration-ui ease-paper" style={{ width: `${job.progress}%` }} /></div>}
    </div>}
    {(error || submitError || job?.status === 'failed' || job?.status === 'cancelled' || job?.error === 'legacy_output_hidden') && <p className="mt-3 text-caption text-danger-700" role="status">
      {submitError ?? error ?? (job?.status === 'cancelled' ? t('任务已取消') : aiJobErrorMessage(job?.error ?? null))}
      {queryIssue === 'retrying' && <span className="ml-2">{t('正在重新查询原任务')}</span>}
      {canReadOriginal && <button onClick={() => void rereadOriginal()} disabled={readingOriginal} className="ml-2 text-ai-600 disabled:opacity-50">{t('继续查询原任务')}</button>}
      {originalReadError && <span className="mt-1 block">{originalReadError}</span>}
      {(queryIssue === 'paused' || queryIssue === 'blocked') && <button onClick={resume} className="ml-2 text-ai-600">{t('继续查询原任务')}</button>}
      {isOwner && job?.errorDetail && <span className="mt-1 block break-all text-micro text-ink-400">{job.errorDetail}</span>}
    </p>}
    {reportJob && <div className="mt-3 overflow-hidden rounded-md border border-ai-600/25 bg-ai-50">
      <div
        data-stock-report-scroll=""
        role="region"
        aria-label={t('股票分析报告')}
        tabIndex={0}
        className="overflow-y-auto overscroll-contain px-3.5 py-3 [scrollbar-gutter:stable]"
        style={{ maxHeight: 'clamp(260px, 55dvh, 440px)' }}
      >
      {running && <p className="mb-3 text-micro text-ink-500">{t('正在生成新分析，保留上一份报告')}</p>}
      <StockAnalysisReport result={reportJob.result} visuals={reportJob.visuals} fallback={<p className="text-body-s leading-relaxed text-ink-800 whitespace-pre-wrap">{summary ?? t('分析已完成，暂无摘要')}</p>} />
      {reportJob.model?.trim() && <p className="mt-3 break-words text-micro text-ink-400">{aiModelLabel(reportJob.model, reportJob.reasoning)}</p>}
      {(reportJob.completedAt || reportJob.updatedAt) && <p className="mt-1 text-micro text-ink-400">{reportJob.completedAt
        ? t('报告完成时间 {time}', { time: timeLabel(reportJob.completedAt) })
        : t('报告更新时间 {time}', { time: timeLabel(reportJob.updatedAt) })}</p>}
      {displayedUsage.length > 0 && <details className="mt-2 text-micro text-ink-500"><summary className="cursor-pointer">{t('模型与缓存用量')}</summary><div className="mt-1">{displayedUsage.map(([key, label]) => <p key={key}>{label}: {usage![key]!.toLocaleString(localeTag())}</p>)}</div></details>}
      <AnalysisSources sources={reportJob.evidenceSources} />
      </div>
    </div>}
  </div>;
}
