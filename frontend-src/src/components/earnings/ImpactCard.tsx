/**
 * B3 AI 影响分析卡 + B4 AI 任务状态机（earnings.md）
 * 顶边 2px ai-600 标识条 · 吸顶 · 换代码 blur(6px)→0 + fade 400ms「聚焦」
 *
 * 状态机：
 *  - 已缓存 → 真实契约结果（摘要 / 预期 / 关联标的）
 *  - 409 analysis_required → owner / visitor 均可「生成分析」→ 报告级状态退避轮询 [2,3,5,8,10]s
 *    （只读取状态和结果，不向 visitor 暴露任务编号、费用或取消能力）
 *  - 财报发布后由后台自动重分析；最终结果锁定，界面不再提供重复请求入口
 *  - 无论生成开关是否开启都先读取缓存；owner 的运行开关不限制 visitor 读取公开入口
 * 纪律：不展示后端契约没有的推导指标
 */
import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { earningsApi } from '@/api/modules/earnings';
import type {
  EarningsImpactDirection,
  EarningsImpactRelation,
  EarningsImpactResult,
  EarningsReportAnalysis,
} from '@/api/modules/earnings';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import SourceNote from '@/components/shared/SourceNote';
import { SkeletonText } from '@/components/shared/Skeleton';
import PulseDot from './PulseDot';
import type { EarningsRow } from './types';
import { exNum } from './types';

/* ---------------- 报告级公开分析状态机 ---------------- */
const ACTIVE_STATUSES = new Set(['preparing', 'pending', 'queued', 'in_progress', 'processing', 'running', 'cancel_requested']);
const FAILED_STATUSES = new Set(['failed', 'cancelled', 'canceled', 'budget_blocked']);
const isActive = (s: string) => ACTIVE_STATUSES.has(s);

const BACKOFF_MS = [2000, 3000, 5000, 8000, 10000];
const FINAL_STAGES = new Set([
  'final',
  'finalized',
  'locked',
  'final_analysis',
  'post_release_final',
]);

function normalizedStage(value: string | undefined): string {
  return String(value ?? '').trim().toLowerCase().replaceAll('-', '_');
}

function isReanalysisStage(value: string | undefined): boolean {
  const stage = normalizedStage(value);
  return stage.includes('reanalysis')
    || stage === 'reanalyzing'
    || stage === 'released_pending'
    || stage === 'finalization_in_progress';
}

function isFinalImpact(value: EarningsImpactResult | null): boolean {
  if (!value) return false;
  return value.locked === true
    || value.final === true
    || FINAL_STAGES.has(normalizedStage(value.analysisStage));
}

function isImpactFinalizing(value: EarningsImpactResult | null): boolean {
  return value?.finalizationInProgress === true
    || isReanalysisStage(value?.analysisStage);
}

function completedStageLabel(value: EarningsImpactResult): string {
  const stage = normalizedStage(value.analysisStage);
  if (stage === 'pre_release') return '发布前分析';
  if (stage === 'post_release_manual') return '发布后分析';
  return '简体中文';
}

/* ---------------- 报告级服务端状态：不展示任务编号、费用或取消能力 ---------------- */
function JobSteps({ analysis }: { analysis: EarningsReportAnalysis }) {
  const queued = ['queued', 'pending', 'preparing'].includes(String(analysis.status));
  return (
    <div role="status">
      <p className="flex items-center gap-2 text-caption text-ink-500">
        <PulseDot className="bg-ai-600" size={8} />
        {queued ? '第 1 / 1 条正在排队' : '正在分析第 1 / 1 条'}
        <span className="ml-auto font-mono text-micro text-ai-600 tnum">
          {queued ? '等待开始' : '模型处理中'}
        </span>
      </p>
      <div className="mt-2 h-1 overflow-hidden rounded-pill bg-line" role="progressbar" aria-label="财报分析处理中">
        <motion.div
          className="h-full w-1/3 rounded-pill bg-ai-600"
          animate={{ x: ['-100%', '300%'] }}
          transition={{ duration: 1.5, ease: 'easeInOut', repeat: Infinity }}
        />
      </div>
    </div>
  );
}

const DIRECTION_META: Record<EarningsImpactDirection, { label: string; className: string }> = {
  bullish: { label: '利多', className: 'border-up-600/25 bg-up-50 text-up-700' },
  bearish: { label: '利空', className: 'border-down-600/25 bg-down-50 text-down-700' },
  mixed: { label: '多空交织', className: 'border-warn-600/25 bg-warn-50 text-warn-600' },
};

const RELATION_LABELS: Record<EarningsImpactRelation, string> = {
  competitor: '竞争对手',
  supplier: '供应链',
  customer: '客户',
  etf: '交易所交易基金',
  opposing: '反向关联',
};

/* ---------------- Serif 引文：$CODE 高亮可点击 ---------------- */
function QuotedSummary({ text, onOpenTicker }: { text: string; onOpenTicker: (t: string) => void }) {
  const parts = text.split(/(\$[A-Z][A-Z0-9._]{0,11})/g);
  return (
    <blockquote className="rounded-sm border-l-2 border-ai-600 bg-ai-50/50 py-3 pl-4 pr-3">
      <p className="font-display text-[15px] leading-[26px] text-ink-800">
        {parts.map((p, i) =>
          /^\$[A-Z]/.test(p) ? (
            <button
              key={i}
              onClick={() => onOpenTicker(p.slice(1))}
              className="font-semibold text-brand-600 underline decoration-brand-400/50 decoration-dotted underline-offset-4 transition-colors hover:text-brand-700"
              aria-label={`查看 ${p.slice(1)} 详情`}
            >
              {p}
            </button>
          ) : (
            <span key={i}>{p}</span>
          ),
        )}
      </p>
    </blockquote>
  );
}

/* ================= 主组件 ================= */
type Phase =
  | 'idle' // 未选标的
  | 'loading' // 拉取缓存结果
  | 'ready' // 已缓存
  | 'needs-analysis' // 409 analysis_required（owner / visitor 均可生成）
  | 'locked-ai' // AI 已关闭
  | 'public-unavailable' // 公开任务入口尚不可用
  | 'finalizing' // 财报已发布，后台自动重分析
  | 'final-locked' // 最终分析已锁定
  | 'job' // 任务活跃
  | 'job-failed' // 任务终态失败
  | 'unavailable'; // 503 / 其他错误

interface ImpactCardProps {
  ticker: string | null;
  row?: EarningsRow | null;
  onAnalyzed: (ticker: string, analysis: EarningsReportAnalysis) => void;
  className?: string;
}

function reportAnalysisNeedsPolling(value: EarningsReportAnalysis | null): boolean {
  if (!value) return false;
  return isActive(normalizedStage(value.status))
    || value.finalizationInProgress
    || (
      normalizedStage(value.analysisStage) === 'post_release_final'
      && !value.final
      && !value.locked
    );
}

export default function ImpactCard({ ticker, row, onAnalyzed, className }: ImpactCardProps) {
  const { isOwner, aiEnabled, aiAvailable } = useAccess();
  const { openTicker } = useShell();
  const toast = useToast();
  const reportDate = row?.date ?? null;
  const reportYear = row ? exNum(row, 'year') : null;
  const reportQuarter = row ? exNum(row, 'quarter') : null;

  const [phase, setPhase] = useState<Phase>('idle');
  const [impact, setImpact] = useState<EarningsImpactResult | null>(null);
  const [analysis, setAnalysis] = useState<EarningsReportAnalysis | null>(null);
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [confirming, setConfirming] = useState(false);
  const [pollAttempt, setPollAttempt] = useState(0);

  const applyReportAnalysis = useCallback((next: EarningsReportAnalysis) => {
    const resolvedReportDate = next.reportDate ?? reportDate ?? undefined;
    const resolvedResult = next.result
      ? {
          ...next.result,
          ...(next.analysisStage ? { analysisStage: next.analysisStage } : {}),
          ...(next.locked ? { locked: true } : {}),
          ...(next.final ? { final: true } : {}),
          ...(next.finalizationInProgress ? { finalizationInProgress: true } : {}),
          ...(next.reportId ? { reportId: next.reportId } : {}),
          ...(resolvedReportDate ? { reportDate: resolvedReportDate } : {}),
        }
      : null;
    const resolvedAnalysis: EarningsReportAnalysis = {
      ...next,
      ...(resolvedReportDate ? { reportDate: resolvedReportDate } : {}),
      result: resolvedResult,
    };

    setAnalysis(resolvedAnalysis);
    setImpact(resolvedResult);
    setErrorMsg(resolvedAnalysis.errorCode ?? '');
    if (ticker) onAnalyzed(ticker, resolvedAnalysis);
    if (resolvedResult) {
      setPhase('ready');
      return;
    }
    if (resolvedAnalysis.locked || resolvedAnalysis.final) {
      setPhase('final-locked');
      return;
    }
    if (
      resolvedAnalysis.finalizationInProgress
      || (
        normalizedStage(resolvedAnalysis.analysisStage) === 'post_release_final'
        && isActive(normalizedStage(resolvedAnalysis.status))
      )
    ) {
      setPhase('finalizing');
      return;
    }
    if (isActive(normalizedStage(resolvedAnalysis.status))) {
      setPhase('job');
      return;
    }
    if (FAILED_STATUSES.has(normalizedStage(resolvedAnalysis.status))) {
      setPhase('job-failed');
      return;
    }
    if (resolvedAnalysis.errorCode === 'manual_analysis_disabled') {
      setPhase('locked-ai');
      return;
    }
    setPhase(isOwner && !aiAvailable ? 'locked-ai' : 'needs-analysis');
  }, [aiAvailable, isOwner, onAnalyzed, reportDate, ticker]);

  /* 精确报告级 GET：只读状态与结果，不读取通用任务、费用或取消信息。 */
  const loadImpact = useCallback(
    async (t: string): Promise<EarningsReportAnalysis | null> => {
      if (!reportDate) {
        setErrorMsg('当前日历行缺少财报日期，无法绑定精确报告');
        setPhase('unavailable');
        return null;
      }
      try {
        const next = await earningsApi.reportAnalysis(t, reportDate, {
          year: reportYear,
          quarter: reportQuarter,
        });
        applyReportAnalysis(next);
        return next;
      } catch (e) {
        const err = e instanceof ApiError ? e : null;
        if (err?.bizCode === 'earnings_analysis_locked') {
          setPhase('final-locked');
        } else if (err?.bizCode === 'earnings_finalization_in_progress') {
          setPhase('finalizing');
        } else if (err && (err.code === 409 || err.bizCode === 'analysis_required')) {
          setPhase(isOwner && !aiAvailable ? 'locked-ai' : 'needs-analysis');
        } else if (err && err.code === 401) {
          setErrorMsg(err.message || '公开分析入口暂不可用');
          setPhase('public-unavailable');
        } else if (err && err.code === 503) {
          setErrorMsg(err.message || '快照暂不可用');
          setPhase('unavailable');
        } else {
          setErrorMsg(err?.message ?? '加载失败');
          setPhase('unavailable');
        }
        return null;
      }
    },
    [
      aiAvailable,
      applyReportAnalysis,
      isOwner,
      reportDate,
      reportQuarter,
      reportYear,
    ],
  );

  /* 换标的/权限变化 → 渲染期同步重置（adjust-state-during-render），副作用留给 effect */
  const contextKey = [
    ticker ?? '',
    reportDate ?? '',
    reportYear ?? '',
    reportQuarter ?? '',
    aiEnabled,
    aiAvailable,
    isOwner,
  ].join('|');
  const [prevContextKey, setPrevContextKey] = useState(contextKey);
  if (contextKey !== prevContextKey) {
    setPrevContextKey(contextKey);
    setAnalysis(null);
    setConfirming(false);
    setPollAttempt(0);
    setImpact(null);
    setErrorMsg('');
    setPhase(!ticker ? 'idle' : 'loading');
  }

  /* 缓存读取不受生成开关影响。 */
  useEffect(() => {
    if (!ticker) return;
    const id = window.setTimeout(() => void loadImpact(ticker), 0);
    return () => window.clearTimeout(id);
  }, [ticker, aiEnabled, aiAvailable, isOwner, loadImpact]);

  /* 排队、分析或自动终版期间只轮询报告级 GET。 */
  const shouldPoll = reportAnalysisNeedsPolling(analysis);
  useEffect(() => {
    if (!ticker || !shouldPoll) return;
    const serverDelay = analysis?.retryAfterSeconds;
    const base = serverDelay != null && serverDelay > 0
      ? Math.max(1_000, serverDelay * 1_000)
      : BACKOFF_MS[Math.min(pollAttempt, BACKOFF_MS.length - 1)];
    const delay = typeof document !== 'undefined' && document.visibilityState !== 'visible' ? base * 3 : base;
    const timer = window.setTimeout(async () => {
      const next = await loadImpact(ticker);
      setPollAttempt((value) => value + 1);
      if (next?.result && !reportAnalysisNeedsPolling(next)) {
        toast.success(`${ticker} AI 影响分析已生成`);
      }
    }, delay);
    return () => window.clearTimeout(timer);
  }, [analysis?.retryAfterSeconds, loadImpact, pollAttempt, shouldPoll, ticker, toast]);

  /* 报告级 POST 的正文固定为 {confirm:true}，全部财务事实由服务端当前快照绑定。 */
  const startJob = async () => {
    if (
      !ticker
      || !reportDate
      || phase === 'finalizing'
      || phase === 'final-locked'
      || isFinalImpact(impact)
    ) return;
    setConfirming(false);
    try {
      const next = await earningsApi.requestReportAnalysis(ticker, reportDate, {
        year: reportYear,
        quarter: reportQuarter,
      });
      applyReportAnalysis(next);
      setPollAttempt(0);
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err?.bizCode === 'earnings_analysis_locked') {
        const finalState = await loadImpact(ticker);
        if (!finalState?.result) setPhase('final-locked');
      } else if (err?.bizCode === 'earnings_finalization_in_progress') {
        setPhase('finalizing');
      } else if (err?.code === 429) {
        toast.error('AI 任务队列已满', `约 ${err.retryAfter ?? 60}s 后重试`);
      } else if (err?.code === 401) {
        setErrorMsg(err.message || '公开分析入口暂不可用');
        setPhase('public-unavailable');
      } else if (err?.bizCode === 'manual_analysis_disabled') {
        setPhase('locked-ai');
      } else {
        toast.error('任务创建失败', err?.message);
      }
    }
  };

  return (
    <aside className={cn('card-surface self-start overflow-hidden', className)} aria-label="AI 影响分析">
      {/* 顶边 2px ai-600 标识条 */}
      <div className="h-0.5 bg-ai-600" aria-hidden="true" />
      <AnimatePresence mode="wait">
        <motion.div
          key={`${ticker ?? 'none'}-${phase === 'ready' ? 'ready' : 'state'}`}
          initial={{ opacity: 0, filter: 'blur(6px)' }}
          animate={{ opacity: 1, filter: 'blur(0px)' }}
          exit={{ opacity: 0, transition: { duration: 0.16 } }}
          transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
          className="p-5"
        >
          {/* ---------- 未选标的 ---------- */}
          {phase === 'idle' && (
            <div className="flex flex-col items-center py-10 text-center">
              <span className="flex size-12 items-center justify-center rounded-lg border border-line bg-card-warm text-ink-400">
                <Icon name="calendar-spark" size={22} />
              </span>
              <h3 className="mt-3 text-h3 text-ink-800">选择一只标的</h3>
              <p className="mt-1 max-w-[260px] text-caption text-ink-500">点击周历 chip 或列表行，查看该财报的 AI 连锁影响分析。</p>
            </div>
          )}

          {/* ---------- 加载骨架 ---------- */}
          {phase === 'loading' && (
            <div aria-label="AI 影响分析加载中">
              <div className="flex items-center gap-2">
                <Icon name="spark-ai" size={16} className="text-ai-600" />
                <span className="font-display text-[18px] leading-6 text-ink-900">AI 影响 · {ticker}</span>
              </div>
              <div className="mt-4 space-y-4">
                <div className="skeleton-shimmer h-9 w-28 rounded-sm" />
                <SkeletonText lines={2} />
                <SkeletonText lines={2} />
                <SkeletonText lines={4} />
              </div>
            </div>
          )}

          {/* ---------- AI 关闭锁定态 ---------- */}
          {phase === 'locked-ai' && (
            <LockedPanel
              iconClass="text-ink-300"
              title={aiEnabled ? 'AI 分析暂不可用' : 'AI 分析未启用'}
              description={
                aiEnabled
                  ? '模型服务当前不可用，已有分析仍会照常显示。'
                  : '模型生成开关已关闭，已有分析仍会照常显示。'
              }
            />
          )}

          {/* ---------- 公开入口异常 ---------- */}
          {phase === 'public-unavailable' && (
            <LockedPanel
              iconClass="text-ink-300"
              title="公开分析入口暂不可用"
              description={errorMsg || `${ticker ?? '该标的'} 的模型任务暂时无法提交，请稍后重试。`}
            />
          )}

          {/* ---------- 财报发布后：后台自动重分析 ---------- */}
          {phase === 'finalizing' && (
            <div className="py-8" role="status" aria-live="polite">
              <div className="flex items-center gap-2">
                <PulseDot className="bg-ai-600" size={8} />
                <h3 className="text-h3 text-ink-800">正在生成最终分析 · {ticker}</h3>
              </div>
              <p className="mt-2 text-caption leading-5 text-ink-500">
                财报已经发布，后台正在把实际每股收益和营收纳入模型。完成后会自动锁定为最终分析。
              </p>
              <div className="mt-4 h-1 overflow-hidden rounded-pill bg-line">
                <motion.div
                  className="h-full w-1/3 rounded-pill bg-ai-600"
                  animate={{ x: ['-100%', '300%'] }}
                  transition={{ duration: 1.6, ease: 'easeInOut', repeat: Infinity }}
                />
              </div>
              <button
                type="button"
                disabled
                className="mt-4 h-8 cursor-not-allowed rounded-md border border-line bg-card-warm px-3 text-caption text-ink-300"
              >
                自动重分析中
              </button>
            </div>
          )}

          {/* ---------- 最终结果锁定，禁止重复请求 ---------- */}
          {phase === 'final-locked' && (
            <div className="flex flex-col items-center py-9 text-center">
              <span className="flex size-12 items-center justify-center rounded-lg border border-up-600/25 bg-up-50 text-up-700">
                <Icon name="spark-ai" size={22} />
              </span>
              <span className="mt-3 rounded-pill border border-up-600/25 bg-up-50 px-2.5 py-1 text-micro font-semibold text-up-700">
                已锁定 · 最终分析
              </span>
              <h3 className="mt-3 text-h3 text-ink-800">该财报不再重复分析</h3>
              <p className="mt-1 max-w-[280px] text-caption text-ink-500">
                财报发布后的自动重分析已经完成，后台已禁止再次创建同一财报的任务。
              </p>
              <button
                type="button"
                disabled
                className="mt-4 h-8 cursor-not-allowed rounded-md border border-line bg-card-warm px-3 text-caption text-ink-300"
              >
                最终分析已锁定
              </button>
            </div>
          )}

          {/* ---------- 409：公开生成引导 ---------- */}
          {phase === 'needs-analysis' && (
            <div className="flex flex-col items-center py-8 text-center">
              <span className="flex size-12 items-center justify-center rounded-lg border border-ai-600/30 bg-ai-50 text-ai-600">
                <Icon name="spark-ai" size={22} />
              </span>
              <h3 className="mt-3 text-h3 text-ink-800">尚未生成 AI 影响</h3>
              <p className="mt-1 max-w-[280px] text-caption text-ink-500">
                创建模型任务，分析 {ticker} 财报对关联标的的连锁影响。
              </p>
              {confirming ? (
                <div className="mt-4 w-full rounded-md border border-ai-600/30 bg-ai-50 p-3 text-left">
                  <p className="text-caption text-ink-600">
                    将按当前财报日期生成 {ticker} 的报告级分析；最终结果锁定后不会重复生成。
                  </p>
                  <div className="mt-2.5 flex gap-2">
                    <button
                      onClick={() => void startJob()}
                      className="flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md bg-ai-600 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                    >
                      <Icon name="spark-ai" size={13} />
                      确认生成
                    </button>
                    <button
                      onClick={() => setConfirming(false)}
                      className="h-8 rounded-md border border-line bg-card px-3 text-caption text-ink-500 transition-colors hover:text-ink-800"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setConfirming(true)}
                  className="mt-4 flex h-9 items-center gap-2 rounded-md bg-ai-600 px-4 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                >
                  <Icon name="spark-ai" size={14} />
                  生成分析
                </button>
              )}
            </div>
          )}

          {/* ---------- 报告级任务活跃：无任务编号、费用或取消入口 ---------- */}
          {phase === 'job' && analysis && (
            <div>
              <div className="flex items-center gap-2">
                <PulseDot className="bg-ai-600" size={8} />
                <h3 className="text-h3 text-ink-800">正在分析 · {ticker}</h3>
                <span className="ml-auto font-mono text-micro text-ai-600 tnum">
                  {['queued', 'pending', 'preparing'].includes(normalizedStage(analysis.status))
                    ? '排队中'
                    : '模型处理中'}
                </span>
              </div>
              <div className="mt-5">
                <JobSteps analysis={analysis} />
              </div>
              <p className="mt-5 border-t border-line pt-3 text-micro leading-5 text-ink-400">
                报告级状态会自动刷新；公开页面不显示任务编号、费用或取消入口。
              </p>
            </div>
          )}

          {/* ---------- 任务失败：原因 + 重试 ---------- */}
          {phase === 'job-failed' && (
            <div>
              <div className="rounded-md border border-down-600/30 bg-down-50 p-3">
                <p className="text-caption font-medium text-down-700">分析任务失败</p>
                <p className="mt-0.5 text-micro text-ink-500">{errorMsg || '未知原因'}</p>
              </div>
              <button
                onClick={() => setPhase('needs-analysis')}
                className="mt-3 flex h-8 items-center gap-1.5 rounded-md border border-line bg-card px-3 text-caption text-ink-600 transition-colors hover:border-ai-600/50 hover:text-ai-600"
              >
                <Icon name="refresh" size={13} />
                重试
              </button>
            </div>
          )}

          {/* ---------- 503 / 其他错误 ---------- */}
          {phase === 'unavailable' && (
            <div className="flex flex-col items-center py-8 text-center">
              <span className="flex size-12 items-center justify-center rounded-lg border border-line bg-card-warm text-ink-400">
                <Icon name="doc-quote" size={22} />
              </span>
              <h3 className="mt-3 text-h3 text-ink-800">快照暂不可用</h3>
              <p className="mt-1 max-w-[260px] text-caption text-ink-500">{errorMsg || '接口未覆盖此能力，留空而非编造。'}</p>
              <button
                onClick={() => {
                  if (!ticker) return;
                  setPhase('loading');
                  void loadImpact(ticker);
                }}
                className="mt-4 flex h-8 items-center gap-1.5 rounded-md bg-brand-600 px-3.5 text-caption font-medium text-white transition-[filter] hover:brightness-105"
              >
                <Icon name="refresh" size={13} />
                重试
              </button>
            </div>
          )}

          {/* ---------- 已缓存结果：严格展示后端真实契约 ---------- */}
          {phase === 'ready' && impact && (
            <motion.div
              initial="hidden"
              animate="show"
              variants={{ show: { transition: { staggerChildren: 0.06 } } }}
            >
              {/* 1 头部 */}
              <Section>
                <div className="flex items-center gap-2">
                  <Icon name="spark-ai" size={16} className="text-ai-600" />
                  <h3 className="font-display text-[18px] leading-6 text-ink-900">AI 影响 · {impact.ticker}</h3>
                  {isFinalImpact(impact) ? (
                    <span className="ml-auto rounded-pill border border-up-600/25 bg-up-50 px-2 py-0.5 text-micro font-semibold text-up-700">
                      已锁定 · 最终分析
                    </span>
                  ) : isImpactFinalizing(impact) ? (
                    <span className="ml-auto inline-flex items-center gap-1.5 rounded-pill border border-ai-600/25 bg-ai-50 px-2 py-0.5 text-micro font-medium text-ai-600">
                      <PulseDot className="bg-ai-600" size={6} />
                      自动重分析中
                    </span>
                  ) : (
                    <span className="ml-auto rounded-pill border border-ai-600/20 bg-ai-50 px-2 py-0.5 text-micro font-medium text-ai-600">
                      {completedStageLabel(impact)}
                    </span>
                  )}
                </div>
                <p className="mt-1 font-mono text-micro text-ink-400">
                  {impact.outputLanguage}
                  {impact.reportDate ? ` · 财报日 ${impact.reportDate}` : ''}
                </p>
                {isImpactFinalizing(impact) && !isFinalImpact(impact) && (
                  <div className="mt-3 rounded-md border border-ai-600/25 bg-ai-50 px-3 py-2.5" role="status" aria-live="polite">
                    <p className="flex items-center gap-2 text-caption font-medium text-ai-600">
                      <PulseDot className="bg-ai-600" size={7} />
                      财报已发布，正在自动重分析
                    </p>
                    <p className="mt-1 text-micro text-ink-500">
                      当前先保留发布前结果；实际每股收益和营收写入完成后，将自动替换并锁定。
                    </p>
                  </div>
                )}
                {isFinalImpact(impact) && (
                  <p className="mt-3 rounded-md border border-up-600/20 bg-up-50 px-3 py-2 text-micro leading-5 text-up-700">
                    已纳入发布后的实际财报数据，并锁定为最终版本；不会再次发起模型请求。
                  </p>
                )}
              </Section>

              {/* 2 摘要 */}
              <Section>
                <p className="eyebrow">分析摘要</p>
                <div className="mt-2.5">
                  <QuotedSummary text={impact.summary} onOpenTicker={openTicker} />
                </div>
              </Section>

              {/* 3 财报预期 */}
              <Section>
                <p className="eyebrow">财报预期</p>
                <p className="mt-2 text-body-s leading-6 text-ink-700">{impact.expectation}</p>
              </Section>

              {/* 4 关联标的 */}
              <Section>
                <div className="flex items-baseline justify-between">
                  <p className="eyebrow">关联标的</p>
                  <span className="text-micro text-ink-300">{impact.impacted.length} 项</span>
                </div>
                <div className="mt-2.5 space-y-2">
                  {impact.impacted.map((item) => {
                    const meta = DIRECTION_META[item.direction];
                    return (
                      <button
                        key={`${item.ticker}-${item.relation}`}
                        onClick={() => openTicker(item.ticker)}
                        aria-label={`查看 ${item.ticker}：${meta.label}`}
                        className="w-full rounded-md border border-line bg-card-warm p-3 text-left transition-colors hover:border-brand-400/50 hover:bg-card"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span className="font-mono text-caption font-semibold text-ink-900">${item.ticker}</span>
                          <span className="min-w-0 truncate text-caption text-ink-500">{item.name}</span>
                          <span className={cn('ml-auto shrink-0 rounded-xs border px-1.5 py-0.5 text-micro font-medium', meta.className)}>
                            {meta.label}
                          </span>
                        </span>
                        <span className="mt-1.5 block text-micro text-ink-400">{RELATION_LABELS[item.relation]}</span>
                        <span className="mt-1 block text-caption leading-5 text-ink-600">{item.reason}</span>
                      </button>
                    );
                  })}
                </div>
                <SourceNote className="mt-3" text="模型生成 · 基于当前财报日历字段 · 仅供研究" />
              </Section>
            </motion.div>
          )}
        </motion.div>
      </AnimatePresence>
    </aside>
  );
}

/* ---------------- 段落容器（stagger 60ms） ---------------- */
function Section({ children }: { children: ReactNode }) {
  return (
    <motion.section
      variants={{
        hidden: { opacity: 0, y: 10 },
        show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] } },
      }}
      className="border-b border-line pb-4 pt-4 first:pt-0 last:border-b-0 last:pb-0"
    >
      {children}
    </motion.section>
  );
}

/* ---------------- 锁定态面板 ---------------- */
function LockedPanel({ title, description, iconClass }: { title: string; description: string; iconClass?: string }) {
  return (
    <div className="flex flex-col items-center py-10 text-center">
      <span className={cn('flex size-12 items-center justify-center rounded-lg border border-line bg-card-warm', iconClass)}>
        <Icon name="spark-ai" size={22} />
      </span>
      <h3 className="mt-3 text-h3 text-ink-800">{title}</h3>
      <p className="mt-1 max-w-[260px] text-caption text-ink-500">{description}</p>
    </div>
  );
}
