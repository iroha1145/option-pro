/**
 * B3 AI 影响分析卡 + B4 AI 任务状态机（earnings.md）
 * 顶边 2px ai-600 标识条 · 吸顶 · 换代码 blur(6px)→0 + fade 400ms「聚焦」
 *
 * 状态机：
 *  - 已缓存 → 真实契约结果（摘要 / 预期 / 关联标的）
 *  - 409 analysis_required → owner「生成分析」（confirm 注明模型费用）→ 创建 job 退避轮询 [2,3,5,8,10]s
 *    （活跃 led-pulse + 服务端任务状态，终态渲染结果 / 失败原因 + 重试）
 *  - 无论生成开关是否开启都先读取缓存；仅在缺少结果时区分 visitor / AI 关闭
 * 纪律：不展示后端契约没有的推导指标
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import { earningsApi } from '@/api/modules/earnings';
import type {
  EarningsImpactDirection,
  EarningsImpactRelation,
  EarningsImpactResult,
} from '@/api/modules/earnings';
import type { AiJob } from '@/api/types';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import SourceNote from '@/components/shared/SourceNote';
import { SkeletonText } from '@/components/shared/Skeleton';
import PulseDot from './PulseDot';
import type { EarningsRow } from './types';
import { exNum, exStr } from './types';

/* ---------------- AIJobPublic 状态机（api-contract §0.4） ---------------- */
const ACTIVE_STATUSES = new Set(['preparing', 'pending', 'queued', 'in_progress', 'processing', 'running', 'cancel_requested']);
const SUCCESS_STATUSES = new Set(['succeeded', 'completed']);
const isActive = (s: string) => ACTIVE_STATUSES.has(s);
const isSuccess = (s: string) => SUCCESS_STATUSES.has(s);

const BACKOFF_MS = [2000, 3000, 5000, 8000, 10000];

/* ---------------- 服务端任务进度 ---------------- */
function JobSteps({ job }: { job: AiJob }) {
  const queued = ['queued', 'pending', 'preparing'].includes(String(job.status));
  return (
    <div role="status">
      <p className="flex items-center gap-2 text-caption text-ink-500">
        <PulseDot className="bg-ai-600" size={8} />
        {queued ? '第 1 / 1 条正在排队' : '正在分析第 1 / 1 条'}
        <span className="ml-auto font-mono text-micro text-ai-600 tnum">
          {job.progress === null ? '等待服务端状态' : `${Math.round(job.progress)}%`}
        </span>
      </p>
      {job.progress !== null && (
        <div className="mt-2 h-1 overflow-hidden rounded-pill bg-line">
          <div
            className="h-full w-full origin-left rounded-pill bg-ai-600 transition-transform duration-300 motion-reduce:transition-none"
            style={{ transform: `scaleX(${job.progress / 100})` }}
          />
        </div>
      )}
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
  | 'needs-analysis' // 409 analysis_required（owner 可生成）
  | 'locked-visitor' // visitor 未分析 / 401
  | 'locked-ai' // AI 已关闭
  | 'job' // 任务活跃
  | 'job-failed' // 任务终态失败
  | 'unavailable'; // 503 / 其他错误

interface ImpactCardProps {
  ticker: string | null;
  row?: EarningsRow | null;
  onAnalyzed: (ticker: string) => void;
  className?: string;
}

export default function ImpactCard({ ticker, row, onAnalyzed, className }: ImpactCardProps) {
  const { isOwner, aiEnabled, aiAvailable } = useAccess();
  const { openTicker } = useShell();
  const toast = useToast();

  const [phase, setPhase] = useState<Phase>('idle');
  const [impact, setImpact] = useState<EarningsImpactResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [job, setJob] = useState<AiJob | null>(null);
  const [confirming, setConfirming] = useState(false);
  const pollRef = useRef<{ stopped: boolean; timer?: number }>({ stopped: true });

  const stopPolling = useCallback(() => {
    pollRef.current.stopped = true;
    if (pollRef.current.timer) window.clearTimeout(pollRef.current.timer);
  }, []);

  /* 拉取已缓存分析结果 */
  const loadImpact = useCallback(
    async (t: string): Promise<boolean> => {
      try {
        const res = await earningsApi.impact(t);
        setImpact(res);
        setPhase('ready');
        return true;
      } catch (e) {
        const err = e instanceof ApiError ? e : null;
        if (err && (err.code === 409 || err.bizCode === 'analysis_required')) {
          setPhase(!isOwner ? 'locked-visitor' : aiAvailable ? 'needs-analysis' : 'locked-ai');
        } else if (err && err.code === 401) {
          setPhase('locked-visitor');
        } else if (err && err.code === 503) {
          setErrorMsg(err.message || '快照暂不可用');
          setPhase('unavailable');
        } else {
          setErrorMsg(err?.message ?? '加载失败');
          setPhase('unavailable');
        }
        return false;
      }
    },
    [aiAvailable, isOwner],
  );

  /* 轮询任务（退避 [2,3,5,8,10]s · 隐藏标签页 ×3 降频 · 终态停止） */
  const pollJob = useCallback(
    (id: string, t: string) => {
      stopPolling();
      const state: { stopped: boolean; timer?: number } = { stopped: false };
      pollRef.current = state;
      let attempt = 0;
      const tick = async () => {
        if (state.stopped) return;
        try {
          const j = await aiJobsApi.get(id);
          if (state.stopped) return;
          setJob(j);
          if (!isActive(j.status)) {
            if (isSuccess(j.status)) {
              toast.success(`${t} AI 影响分析已生成`);
              onAnalyzed(t);
              setPhase('loading');
              await loadImpact(t);
            } else if (j.status === 'cancelled') {
              toast.info('已取消分析任务');
              setPhase('needs-analysis');
            } else {
              setErrorMsg(j.error || '任务失败，请重试');
              setPhase('job-failed');
            }
            return;
          }
        } catch {
          /* 单次轮询失败不致命，按退避继续 */
        }
        if (state.stopped) return;
        const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
        attempt += 1;
        const delay = typeof document !== 'undefined' && document.visibilityState !== 'visible' ? base * 3 : base;
        state.timer = window.setTimeout(() => void tick(), delay);
      };
      state.timer = window.setTimeout(() => void tick(), BACKOFF_MS[0]);
    },
    [loadImpact, onAnalyzed, stopPolling, toast],
  );

  /* 换标的/权限变化 → 渲染期同步重置（adjust-state-during-render），副作用留给 effect */
  const contextKey = `${ticker ?? ''}|${aiEnabled}|${aiAvailable}|${isOwner}`;
  const [prevContextKey, setPrevContextKey] = useState(contextKey);
  if (contextKey !== prevContextKey) {
    setPrevContextKey(contextKey);
    setJob(null);
    setConfirming(false);
    setImpact(null);
    setErrorMsg('');
    setPhase(!ticker ? 'idle' : 'loading');
  }

  /* 缓存读取不受生成开关影响；卸载/换标的停止轮询（blur-in「聚焦」由 key 驱动） */
  useEffect(() => {
    if (!ticker) return stopPolling;
    const id = window.setTimeout(() => void loadImpact(ticker), 0);
    return () => {
      window.clearTimeout(id);
      stopPolling();
    };
  }, [ticker, aiEnabled, aiAvailable, isOwner, loadImpact, stopPolling]);

  /* 创建任务（owner，confirm 注明模型费用） */
  const startJob = async () => {
    if (!ticker) return;
    setConfirming(false);
    try {
      const j = await aiJobsApi.createEarningsImpact({
        ticker,
        name: row?.name ?? '',
        sector: row ? exStr(row, 'sector') ?? '' : '',
        earnings_date: row?.date ?? '',
        eps_estimate: row?.epsEstimate ?? null,
        revenue_estimate: row?.revEstimate ?? null,
        market_cap: row ? exNum(row, 'marketCap') : null,
      });
      setJob(j);
      setPhase('job');
      pollJob(j.id, ticker);
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err?.code === 429) {
        toast.error('AI 任务队列已满', `约 ${err.retryAfter ?? 60}s 后重试`);
      } else if (err?.code === 401) {
        setPhase('locked-visitor');
      } else {
        toast.error('任务创建失败', err?.message);
      }
    }
  };

  const cancelJob = async () => {
    if (!job) return;
    try {
      await aiJobsApi.cancel(job.id);
      stopPolling();
      toast.info('已取消分析任务');
      setPhase('needs-analysis');
    } catch {
      toast.error('取消失败');
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

          {/* ---------- visitor 锁定态 ---------- */}
          {phase === 'locked-visitor' && (
            <LockedPanel
              iconClass="text-ink-300"
              title="登录后可用模型分析"
              description={`${ticker ?? '该标的'} 尚未生成 AI 影响分析，登录 Owner 后可创建模型任务。`}
            />
          )}

          {/* ---------- 409：owner 生成引导 ---------- */}
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
                    将调用模型生成 {ticker} 的财报影响分析，<span className="font-semibold text-ai-600">会产生模型费用</span>。
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

          {/* ---------- 任务活跃：步骤条 + 取消 ---------- */}
          {phase === 'job' && job && (
            <div>
              <div className="flex items-center gap-2">
                <PulseDot className="bg-ai-600" size={8} />
                <h3 className="text-h3 text-ink-800">正在分析 · {ticker}</h3>
                <span className="ml-auto font-mono text-micro text-ai-600 tnum">
                  {job.progress === null ? '处理中' : `${Math.round(job.progress)}%`}
                </span>
              </div>
              <div className="mt-5">
                <JobSteps job={job} />
              </div>
              <div className="mt-5 flex items-center justify-between border-t border-line pt-3">
                <span className="font-mono text-micro text-ink-400">任务 {job.id}</span>
                <button
                  onClick={() => void cancelJob()}
                  className="h-7 rounded-sm border border-line px-2.5 text-caption text-ink-500 transition-colors hover:border-down-600/50 hover:text-down-700"
                >
                  取消
                </button>
              </div>
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
                  <span className="ml-auto rounded-pill border border-ai-600/20 bg-ai-50 px-2 py-0.5 text-micro font-medium text-ai-600">
                    简体中文
                  </span>
                </div>
                <p className="mt-1 font-mono text-micro text-ink-400">{impact.outputLanguage}</p>
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
