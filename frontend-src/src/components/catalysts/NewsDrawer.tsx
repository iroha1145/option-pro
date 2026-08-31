/** 新闻详情抽屉：标题/来源/时间/原文外链/摘要 + 模型分析区（任务状态机：生成/重试/取消/轮询） */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import Drawer from '@/components/Drawer';
import Icon from '@/components/icons';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { SkeletonBlock, SkeletonText } from '@/components/shared/Skeleton';
import { cn } from '@/lib/utils';
import { fmtLocaleDateTime, fmtLocaleTime } from '@/lib/format';
import { catalystsContract } from './api';
import type { CatalystNewsItem, NewsAnalysisJob, NewsClassification, TrustedStockImpact } from './api';
import { AnalysisStatusChip, ClassificationChip, ConfidenceLabel, ImpactValue, Led, StaleChip, TickerChip } from './bits';
import ConfirmDialog from './ConfirmDialog';
import { t as __t } from '../../i18n/core.ts';

const TERMINAL: NewsAnalysisJob['status'][] = ['completed', 'failed', 'cancelled', 'insufficient_context'];
const DIR_STYLE: Record<NewsClassification, { label: string; cls: string }> = {
  bullish: { label: __t('利多'), cls: 'bg-up-50 text-up-700' },
  bearish: { label: __t('利空'), cls: 'bg-down-50 text-down-700' },
  neutral: { label: __t('中性'), cls: 'bg-paper-2 text-ink-500 border border-line' },
};

/* ---------------- 逐股影响卡 ---------------- */
function StockImpactCard({ imp, index }: { imp: TrustedStockImpact; index: number }) {
  const { openTicker } = useShell();
  const d = DIR_STYLE[imp.direction];
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: 0.08 + index * 0.05 }}
      className="rounded-md border border-line bg-card-warm/60 p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <TickerChip ticker={imp.ticker} onClick={() => openTicker(imp.ticker)} />
        <span className={cn('rounded-xs px-1.5 py-0.5 text-micro font-medium', d.cls)}>{d.label}</span>
        <ImpactValue value={imp.impactScore} />
        <span className="ml-auto font-mono text-micro text-ink-400">{imp.horizon}</span>
      </div>
      <p className="mt-2 text-micro text-ink-500">
        <span className="font-medium text-ink-600">{__t('机制 ·')} {imp.mechanism}</span>
      </p>
      <p className="mt-1 text-body-s leading-relaxed text-ink-600">{imp.reason}</p>
    </motion.div>
  );
}

/* ---------------- 服务端任务进度 ---------------- */
function JobStepper({ job }: { job: NewsAnalysisJob }) {
  const label = job.status === 'queued' ? __t('任务排队中') : __t('模型分析中');
  return (
    <div className="rounded-sm border border-line bg-card px-3 py-2.5">
      <div className="flex min-w-0 items-center gap-2">
        <Led tone="brand" pulse={job.status === 'in_progress'} />
        <p className="truncate text-body-s font-medium text-ink-700">{label}</p>
        <span className="ml-auto shrink-0 font-mono text-micro text-ink-400 tnum">
          {job.progress === null ? __t('等待服务端状态') : `${Math.round(job.progress)}%`}
        </span>
      </div>
      {job.progress !== null && (
        <div className="mt-2 h-1 overflow-hidden rounded-pill bg-brand-100">
          <div
            className="h-full w-full origin-left rounded-pill bg-brand-600 transition-transform duration-500 motion-reduce:transition-none"
            style={{ transform: `scaleX(${job.progress / 100})` }}
          />
        </div>
      )}
    </div>
  );
}

/* ================= 抽屉主体 ================= */
interface NewsDrawerProps {
  newsId: string | null;
  onClose: () => void;
  onUpdate: (item: CatalystNewsItem) => void;
}

export default function NewsDrawer({ newsId, onClose, onUpdate }: NewsDrawerProps) {
  const { isOwner, loading: accessLoading } = useAccess();
  const toast = useToast();
  const [item, setItem] = useState<CatalystNewsItem | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [job, setJob] = useState<NewsAnalysisJob | null>(null);
  const [confirm, setConfirm] = useState<'create' | 'force' | 'cancel' | null>(null);
  const pollRef = useRef<number | null>(null);
  const backoffRef = useRef(0);
  /* 关闭抽屉 / 换条时递增，作废在途 analysisJob 响应（审计 P1）。 */
  const pollGenRef = useRef(0);

  const stopPoll = useCallback(() => {
    pollGenRef.current += 1;
    if (pollRef.current !== null) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  /* 拉取详情 */
  useEffect(() => {
    if (!newsId) {
      /* 抽屉关闭（newsId=null）时必须停掉分析任务轮询（审计 2.2.17）：
         组件常驻不卸载，不清理的话 setTimeout 会继续以 2s→10s 打后端，
         直到任务终态再弹一条与当前语境无关的 toast。 */
      setJob(null);
      stopPoll();
      return;
    }
    setItem(null);
    setLoadError(null);
    setJob(null);
    stopPoll();
    let dead = false;
    catalystsContract
      .news(newsId)
      .then((n) => {
        if (dead) return;
        setItem(n);
        onUpdate(n);
      })
      .catch(() => {
        if (!dead) setLoadError(__t('暂时打不开这条新闻的详情'));
      });
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newsId]);

  /* 详情含在途任务 → 恢复轮询 */
  const activeNewsRef = useRef<string | null>(null);
  useEffect(() => {
    activeNewsRef.current = item?.newsId ?? null;
  }, [item?.newsId]);
  useEffect(() => {
    if (!item?.analysisJobId || job) return;
    if (item.analysisStatus !== 'queued' && item.analysisStatus !== 'in_progress') return;
    const forNews = item.newsId;
    catalystsContract
      .analysisJob(item.analysisJobId)
      .then((j) => {
        // 响应落地时可能已经换了新闻（甚至关了抽屉再开新条）：旧 job 不得污染新条
        if (activeNewsRef.current !== forNews) return;
        if (!TERMINAL.includes(j.status)) setJob(j);
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.newsId]);

  /* 轮询任务至终态（退避 2s→3s→5s→8s→10s，总超时 5 分钟） */
  const pollDeadlineRef = useRef<{ jobId: string; at: number } | null>(null);
  useEffect(() => {
    if (!job || TERMINAL.includes(job.status)) return;
    if (pollDeadlineRef.current?.jobId !== job.jobId) {
      pollDeadlineRef.current = { jobId: job.jobId, at: Date.now() + 5 * 60_000 };
    }
    const deadline = pollDeadlineRef.current.at;
    const BACKOFF = [2000, 3000, 5000, 8000, 10000];
    const generation = pollGenRef.current;
    const stillThisPoll = () =>
      generation === pollGenRef.current && activeNewsRef.current === job.newsId;
    const tick = async () => {
      if (!stillThisPoll()) return;
      if (Date.now() >= deadline) {
        stopPoll();
        toast.error(__t('分析任务仍在处理中'), __t('稍后刷新页面可继续查看结果'));
        return;
      }
      try {
        const next = await catalystsContract.analysisJob(job.jobId);
        if (!stillThisPoll()) return;
        setJob({ ...next });
        if (TERMINAL.includes(next.status)) {
          stopPoll();
          if (next.status === 'completed') {
            toast.success(__t('AI 分析已完成'));
            const fresh = await catalystsContract.news(job.newsId);
            if (!stillThisPoll()) return;
            setItem(fresh);
            onUpdate(fresh);
          } else if (next.status === 'insufficient_context') {
            const fresh = await catalystsContract.news(job.newsId);
            if (!stillThisPoll()) return;
            setItem(fresh);
            onUpdate(fresh);
            toast.info(__t('规则判定信息不足'), __t('未调用模型'));
          } else if (next.status === 'failed') {
            toast.error(__t('分析失败'), next.error ?? __t('可重试'));
            const fresh = await catalystsContract.news(job.newsId);
            if (!stillThisPoll()) return;
            setItem(fresh);
            onUpdate(fresh);
          } else {
            toast.info(__t('任务已取消'));
            const fresh = await catalystsContract.news(job.newsId);
            if (!stillThisPoll()) return;
            setItem(fresh);
            onUpdate(fresh);
          }
          window.setTimeout(() => {
            if (stillThisPoll()) setJob(null);
          }, 600);
          return;
        }
        const delay = BACKOFF[Math.min(backoffRef.current, BACKOFF.length - 1)];
        backoffRef.current += 1;
        pollRef.current = window.setTimeout(() => void tick(), delay);
      } catch {
        if (!stillThisPoll()) return;
        pollRef.current = window.setTimeout(() => void tick(), 5000);
      }
    };
    backoffRef.current = 0;
    pollRef.current = window.setTimeout(() => void tick(), 2000);
    return stopPoll;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.jobId, job?.status]);

  const submittingRef = useRef(false);
  const startAnalysis = useCallback(
    async (force: boolean) => {
      if (!item) return;
      if (submittingRef.current) return; // 提交在途，吞掉重复点击
      submittingRef.current = true;
      setConfirm(null);
      try {
        const j = await catalystsContract.createAnalysisJob(item.newsId, force);
        setJob(j);
        const nextItem = { ...item, analysisStatus: (j.status === 'queued' ? 'queued' : 'in_progress') as CatalystNewsItem['analysisStatus'], analysisJobId: j.jobId };
        setItem(nextItem);
        onUpdate(nextItem);
        toast.info(__t('分析任务已提交'), force ? __t('强制重新分析') : __t('可在本页查看真实状态'));
      } catch (e) {
        toast.error(__t('提交失败'), e instanceof Error ? e.message : undefined);
      } finally {
        submittingRef.current = false;
      }
    },
    [item, onUpdate, toast],
  );

  const cancelJob = useCallback(async () => {
    if (!job) return;
    setConfirm(null);
    try {
      const next = await catalystsContract.cancelAnalysisJob(job.jobId);
      setJob({ ...next });
    } catch (e) {
      toast.error(__t('取消失败'), e instanceof Error ? e.message : undefined);
    }
  }, [job, toast]);

  const running = !!job && !TERMINAL.includes(job.status);
  const analysis = item?.analysis ?? null;
  const showCompleted = item?.analysisStatus === 'completed' && analysis && !running;
  const showInsufficient = (item?.analysisStatus === 'insufficient_context' || job?.status === 'insufficient_context') && !running;
  const showFailed = (item?.analysisStatus === 'failed' || job?.status === 'failed') && !running;
  const showCancelled = job?.status === 'cancelled';

  return (
    <Drawer
      open={newsId !== null}
      onClose={() => {
        if (!confirm) onClose(); // 确认弹窗开启时 ESC/背板仅关弹窗
      }}
      width={640}
      label={__t('新闻详情')}
      title={
        <span className="flex items-center gap-2">
          <Icon name="bolt" size={16} className="text-brand-600" />
          <span className="eyebrow">{__t('NEWS DETAIL · 新闻详情')}</span>
        </span>
      }
    >
      {!item && !loadError && (
        <div className="p-6" aria-hidden="true">
          <SkeletonBlock className="h-4 w-32" />
          <SkeletonBlock className="mt-3 h-7 w-full" />
          <SkeletonText lines={3} className="mt-4" />
          <SkeletonBlock className="mt-6 h-40 w-full rounded-md" />
        </div>
      )}
      {loadError && (
        <div className="flex flex-col items-center px-6 py-16 text-center">
          <span className="flex size-14 items-center justify-center rounded-lg border border-line bg-card-warm text-ink-400">
            <Icon name="doc-quote" size={26} />
          </span>
          <h3 className="mt-4 text-h3 text-ink-800">{__t('详情不可用')}</h3>
          <p className="mt-1.5 text-body-s text-ink-500">{loadError} {__t('· 列表摘要仍然有效')}</p>
        </div>
      )}
      {item && (
        <div className="px-6 py-5">
          {/* 头：来源 + 时间 + 状态 */}
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-micro text-ink-400">
            <span className="font-medium text-ink-500">{item.source}</span>
            <span aria-hidden="true">·</span>
            <span className="font-mono tnum">
              {fmtLocaleDateTime(item.publishedAt)}
            </span>
            {item.sourceCount > 1 && <span className="rounded-xs bg-paper-2 px-1 py-px font-mono text-[10px]">{item.sourceCount} {__t('源确认')}</span>}
            {item.isStale && <StaleChip />}
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto inline-flex items-center gap-1 text-micro text-brand-600 transition-colors hover:text-brand-500"
            >
              {__t('原文')}
              <Icon name="external" size={12} />
            </a>
          </div>

          {/* 标题 */}
          <h2 className="mt-3 font-display text-[22px] leading-[30px] font-semibold text-ink-900">{item.titleZh}</h2>

          {/* 摘要（serif 引文排版） */}
          <blockquote className="mt-4 border-l-[3px] border-line-strong pl-4">
            <p className="font-quote text-[15px] leading-[26px] text-ink-800">{item.summaryZh}</p>
          </blockquote>

          {/* 关联代码 */}
          <div className="mt-4 flex flex-wrap items-center gap-1.5">
            <span className="shrink-0 text-micro text-ink-400">{__t('关联代码')}</span>
            {item.sourceTickers.map((t) => (
              <TickerChip key={t} ticker={t} />
            ))}
          </div>

          {/* ============ 模型分析区 ============ */}
          <section className="mt-6 rounded-lg border border-line bg-card-warm/50 p-4" aria-label={__t("模型分析区")}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="flex items-center gap-1.5 text-h3 text-ink-800">
                <Icon name="spark-ai" size={15} className="text-ai-600" />
                {__t('模型分析')}
              </p>
              <AnalysisStatusChip status={running ? (job.status === 'queued' ? 'queued' : 'in_progress') : item.analysisStatus} />
            </div>

            {/* 任务进行中 */}
            <AnimatePresence initial={false}>
              {running && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
                  className="overflow-hidden"
                >
                  <div className="mt-4">
                    <JobStepper job={job} />
                    {isOwner && job.cancellable && (
                      <button
                        onClick={() => setConfirm('cancel')}
                        className="mt-3 rounded-md border border-line bg-card px-3 py-1.5 text-caption text-ink-500 shadow-btn transition-colors hover:border-down-600/40 hover:text-down-700"
                      >
                        {__t('取消任务')}
                      </button>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* 已完成分析 */}
            {showCompleted && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                className="mt-4"
              >
                <div className="flex flex-wrap items-center gap-2.5">
                  <ClassificationChip classification={analysis.classification} />
                  <ConfidenceLabel value={analysis.confidence} />
                </div>
                <p className="mt-3 text-body-s font-medium text-ink-800">{analysis.headlineSummary}</p>
                {/* causal_summary serif 引文 */}
                <blockquote className="mt-3 border-l-[3px] border-ai-600/40 pl-3.5">
                  <p className="font-quote text-[14px] leading-[24px] text-ink-800">{analysis.causalSummary}</p>
                </blockquote>
                <div className="mt-4 space-y-2">
                  {analysis.trustedStockImpacts.map((imp, i) => (
                    <StockImpactCard key={imp.ticker} imp={imp} index={i} />
                  ))}
                </div>
                <p className="mt-3 border-t border-line pt-2.5 font-mono text-micro text-ink-400 tnum">
                  {__t('AI 生成于')}{' '}
                  {fmtLocaleTime(analysis.generatedAt)}
                </p>
              </motion.div>
            )}

            {/* 信息不足 · 未调用模型 */}
            {showInsufficient && (
              <div className="mt-4 rounded-md border border-line bg-paper-2 p-4 text-center">
                <Icon name="doc-quote" size={22} className="mx-auto text-ink-300" />
                <p className="mt-2 text-body-s font-medium text-ink-800">{__t('信息不足 · 未调用模型')}</p>
                <p className="mt-1 text-micro text-ink-400">{__t('这条新闻信息量不足，未做 AI 分析')}</p>
              </div>
            )}

            {/* 失败 */}
            {showFailed && (
              <div className="mt-4 rounded-md border border-down-600/20 bg-down-50 p-3.5">
                <p className="text-body-s font-medium text-down-700">{__t('分析失败')}</p>
                <p className="mt-1 text-micro text-ink-500">{job?.error ?? __t('模型输出校验失败，可重试')}</p>
              </div>
            )}

            {/* 已取消 */}
            {showCancelled && !showFailed && (
              <div className="mt-4 rounded-md border border-line bg-paper-2 p-3.5 text-center text-body-s text-ink-500">{__t('任务已取消')}</div>
            )}

            {/* 操作区 */}
            {!running && (
              <div className="mt-4 flex flex-wrap items-center gap-2.5">
                {accessLoading ? null : isOwner ? (
                  <>
                    {(item.analysisStatus === 'pending' || showCancelled) && (
                      <button
                        onClick={() => setConfirm('create')}
                        className="flex items-center gap-1.5 rounded-md bg-brand-600 px-3.5 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105"
                      >
                        <Icon name="spark-ai" size={13} />
                        {__t('生成 AI 分析')}
                      </button>
                    )}
                    {(showCompleted || showFailed || showInsufficient) && (
                      <button
                        onClick={() => setConfirm('force')}
                        className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3.5 py-2 text-caption font-medium text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
                      >
                        <Icon name="refresh" size={13} />
                        {showFailed || showInsufficient ? __t('重试分析（强制）') : __t('重新分析（强制）')}
                      </button>
                    )}
                  </>
                ) : (
                  <p className="text-micro text-ink-400">{__t('登录后可用模型分析')}</p>
                )}
              </div>
            )}
          </section>
        </div>
      )}

      {/* 费用确认弹窗 */}
      <ConfirmDialog
        open={confirm === 'create'}
        title={__t("生成 AI 分析？")}
        description={__t("将调用模型对该新闻进行情绪与影响分析，消耗模型预算并计入每日额度与任务上限。")}
        confirmLabel={__t("生成分析")}
        onConfirm={() => void startAnalysis(false)}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'force'}
        title={__t("强制重新分析？")}
        description={__t("将忽略既有结果重新调用模型，消耗模型预算并计入每日额度；后台可能因冷却或开关限制而拒绝。")}
        confirmLabel={__t("重新分析")}
        onConfirm={() => void startAnalysis(true)}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'cancel'}
        title={__t("取消分析任务？")}
        description={__t("任务取消后可重新发起；已产生的排队资源将释放。")}
        confirmLabel={__t("取消任务")}
        danger
        onConfirm={() => void cancelJob()}
        onCancel={() => setConfirm(null)}
      />
    </Drawer>
  );
}
