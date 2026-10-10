/**
 * 首页「市场报告」：每个交易日开盘前、收盘后各一份，由 Claude 生成（后端 market_brief）。
 *
 * 60 秒读一次 latest。Owner 可以手动生成：提交后每 20 秒跟进一次，出现新研判或新的失败
 * 记录就停，最长 25 分钟。状态：首次加载骨架；读不到且没有旧数据 → 错误与重试；还没有
 * 任何研判 → 写明下一个时段；刷新失败但有旧数据 → 陈旧条；最近一次运行失败 → 一行说明，
 * 正文仍是上一份成功的研判。标题行右侧可收起正文，收起与否记在本机，下次打开首页照旧。
 */
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { invalidateQueryPaths } from '@/api/queryRegistry';
import { MARKET_BRIEF_LATEST_PATH, marketBriefApi } from '@/api/modules/marketBrief';
import ConfirmDialog from '@/components/catalysts/ConfirmDialog';
import Icon from '@/components/icons';
import AnalysisIcon from '@/components/shared/AnalysisIcon';
import CollapsePresence from '@/components/shared/CollapsePresence';
import EmptyState from '@/components/shared/EmptyState';
import IconSwap, { BusyIcon } from '@/components/shared/IconSwap';
import { SkeletonBlock, SkeletonText } from '@/components/shared/Skeleton';
import SoftBadge from '@/components/shared/SoftBadge';
import Spinner from '@/components/shared/Spinner';
import StaleStrip from '@/components/shared/StaleStrip';
import StatusNotice from '@/components/shared/StatusNotice';
import TextSwap from '@/components/shared/TextSwap';
import ThinkingLabel from '@/components/shared/ThinkingLabel';
import { remoteState } from '@/hooks/remoteState';
import { useAccess } from '@/hooks/useAccess';
import { useNow } from '@/hooks/useNow';
import { usePolling } from '@/hooks/usePolling';
import { useToast } from '@/hooks/useToast';
import { cn } from '@/lib/utils';
import MarketBriefContent from './MarketBriefContent';
import { useMarketBriefPreview } from './marketBriefPreview';
import {
  attemptErrorText,
  attemptFailureText,
  etClock,
  failedAttemptAfter,
  followBaseline,
  followOutcome,
  generatedText,
  isPreviousBrief,
  nextSlotText,
  slotTag,
  triggerFailureText,
  triggerReply,
  type FollowBaseline,
} from './marketBriefText';
import { t } from '../../i18n/core.ts';

const POLL_MS = 60_000;
const FOLLOW_INTERVAL_MS = 20_000;
const FOLLOW_TIMEOUT_MS = 25 * 60_000;
const COLLAPSED_KEY = 'optix:market-brief-collapsed';

function readCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
}

function persistCollapsed(collapsed: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    if (collapsed) window.localStorage.setItem(COLLAPSED_KEY, '1');
    else window.localStorage.removeItem(COLLAPSED_KEY);
  } catch {
    /* 隐私模式或存储已满：只在这次打开的页面里生效 */
  }
}

interface Outcome {
  seq: number;
  kind: 'ready' | 'failed';
  reason: string | null;
}

/* 骨架至少撑到首屏以下：研判正文远长于一屏，骨架偏矮时下面的市场状态、突破信号会先露出来、再被正文推下去。 */
function BriefSkeleton() {
  return (
    <div className="min-h-[calc(100dvh-24rem)]">
      <span className="sr-only" role="status">{t('加载中')}</span>
      <div aria-hidden="true">
        <SkeletonBlock className="h-3 w-3/4" />
        <SkeletonBlock className="mt-4 h-6 w-4/5" />
        <div className="mt-6 grid gap-6 xl:grid-cols-2">
          {[0, 1, 2, 3].map((index) => (
            <div key={index}>
              <SkeletonBlock className="h-4 w-28" />
              <SkeletonText lines={3} className="mt-3" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function MarketBriefCard({ className }: { className?: string }) {
  const { isOwner, isVisitor, loading: accessLoading } = useAccess();
  const toast = useToast();
  const latestQ = usePolling(() => marketBriefApi.latest(), POLL_MS, [], { restore: marketBriefApi.restore });
  const now = useNow(60_000);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [follow, setFollow] = useState<FollowBaseline | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const bodyId = useId();
  const submittingRef = useRef(false);

  const data = useMarketBriefPreview(latestQ.data);
  const brief = data?.brief ?? null;

  /* 手动生成有了结果（新研判，或新的失败记录）就停止跟进。读数一到就在同次渲染里
     派生，不等下一拍跟进；提示条交给下面的 effect。 */
  if (follow && data) {
    const kind = followOutcome(follow, data);
    if (kind) {
      const reason = kind === 'failed' && data.latestAttempt ? attemptErrorText(data.latestAttempt.errorCode) : null;
      setFollow(null);
      setOutcome((prev) => ({ seq: (prev?.seq ?? 0) + 1, kind, reason }));
    }
  }

  useEffect(() => {
    if (!outcome) return;
    if (outcome.kind === 'ready') toast.success(t('新报告已生成'));
    else toast.error(t('报告生成失败'), outcome.reason ?? undefined);
  }, [outcome, toast]);

  /* 跟进期间每 20 秒硬失效再强制读：注册表的 60 秒新鲜窗口和浏览器 HTTP 缓存都会让
     普通刷新拿回旧研判（MacroConditionsPanel 同一做法）。页面隐藏时只计时、不读。 */
  const refreshLatest = latestQ.refresh;
  useEffect(() => {
    if (!follow) return;
    const timer = window.setInterval(() => {
      if (Date.now() - follow.since >= FOLLOW_TIMEOUT_MS) {
        setFollow(null);
        toast.info(t('报告仍在生成'), t('完成后自动显示'));
        return;
      }
      if (document.visibilityState !== 'visible') return;
      invalidateQueryPaths([MARKET_BRIEF_LATEST_PATH], { reload: true });
      refreshLatest({ force: true });
    }, FOLLOW_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [follow, refreshLatest, toast]);

  const start = useCallback(async () => {
    setConfirmOpen(false);
    if (submittingRef.current) return;
    submittingRef.current = true;
    setFollow(followBaseline(data, Date.now()));
    try {
      // 新排队时不另弹提示：按钮与下方的进行中状态行已经说明在生成。
      const reply = triggerReply(await marketBriefApi.trigger(), Date.now());
      if (!reply.follow) setFollow(null);
      if (reply.refresh) {
        invalidateQueryPaths([MARKET_BRIEF_LATEST_PATH], { reload: true });
        refreshLatest({ force: true });
      }
      if (reply.title) toast.info(reply.title, reply.description ?? undefined);
    } catch (error) {
      setFollow(null);
      toast.error(t('没有开始生成'), triggerFailureText(error instanceof Error ? error : null));
    } finally {
      submittingRef.current = false;
    }
  }, [data, refreshLatest, toast]);

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    persistCollapsed(next);
  };

  const retry = () => {
    invalidateQueryPaths([MARKET_BRIEF_LATEST_PATH]);
    refreshLatest({ force: true });
  };

  const busy = follow !== null;
  const state = remoteState(latestQ, (value) => value.brief === null);
  const year = etClock(now)?.day.slice(0, 4);
  const previous = brief ? isPreviousBrief(brief, data?.nextSlot ?? null, now) : false;
  const generated = brief ? generatedText(brief.generatedAt, brief.tradingDate, year) : null;
  const failed = data ? failedAttemptAfter(data.latestAttempt, brief) : null;

  let body: ReactNode;
  if (state === 'loading') {
    body = <BriefSkeleton />;
  } else if (state === 'error') {
    const error = latestQ.error;
    body = (
      <EmptyState
        variant="error"
        title={error?.code === 503 ? t('数据暂不可用') : t('加载失败')}
        description={error?.message}
        action={
          <button
            type="button"
            className="control-button"
            onClick={retry}
            disabled={latestQ.refreshing}
            aria-busy={latestQ.refreshing}
          >
            <BusyIcon busy={latestQ.refreshing} size={14} />
            {t('重试')}
          </button>
        }
      />
    );
  } else if (!brief) {
    body = (
      <EmptyState
        title={t('首份报告尚未生成')}
        description={nextSlotText(data?.nextSlot ?? null, year) ?? undefined}
      />
    );
  } else {
    body = <MarketBriefContent brief={brief} year={year} />;
  }

  return (
    <section aria-label={t('市场报告')} className={cn('card-surface p-4 sm:p-5 lg:p-6', className)}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-ai-50 text-ai-600" aria-hidden="true">
            <AnalysisIcon size={17} />
          </span>
          <h2 className="text-h3 text-ink-900">{t('市场报告')}</h2>
          {brief && (
            <SoftBadge tone="ai">{t('{model} 生成', { model: brief.model.label ?? brief.model.id ?? 'AI' })}</SoftBadge>
          )}
          {/* 首次读取时按常见读数留出模型与时段标签的位置，窄屏上标题行的折行与读到后一致。 */}
          {state === 'loading' && (
            <SoftBadge tone="ai" aria-hidden="true" className="invisible">{t('{model} 生成', { model: 'Claude Opus 5.5' })}</SoftBadge>
          )}
        </div>
        {brief && (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            {previous && <SoftBadge tone="warn">{t('上一份')}</SoftBadge>}
            <SoftBadge>{slotTag(brief.slot, brief.tradingDate, year)}</SoftBadge>
            {generated && <span className="text-micro text-ink-400 tnum">{generated}</span>}
          </div>
        )}
        {state === 'loading' && (
          <div aria-hidden="true" className="invisible flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <SoftBadge>{slotTag('pre_open', '2026-10-09')}</SoftBadge>
            <span className="text-micro tnum">{t('生成于 纽约时间 {time}', { time: '08:40' })}</span>
          </div>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {isOwner ? (
            <button
              type="button"
              className="btn-ai"
              onClick={() => setConfirmOpen(true)}
              /* 首次读取未回来时先不让点：跟进要拿点击那一刻的读数当基准。 */
              disabled={busy || latestQ.loading}
              aria-busy={busy}
            >
              <IconSwap state={busy ? 'b' : 'a'} a={<AnalysisIcon size={14} />} b={<Spinner size={12} tone="muted" />} />
              <TextSwap swapKey={busy ? 'busy' : 'idle'}>{busy ? t('生成中') : t('现在生成')}</TextSwap>
            </button>
          ) : !accessLoading && isVisitor ? (
            <span className="text-micro text-ink-400">{t('登录后可手动生成')}</span>
          ) : null}
          {/* 无边框文字按钮（与正文里「上一份研判的复盘」同款）：标题行里不再多一个描边块；触屏仍是 44px 点按区。 */}
          <button
            type="button"
            className="touch-target inline-flex items-center justify-center gap-1 rounded-sm px-1 text-caption text-ink-500 transition-colors duration-fast hover:text-ink-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
            onClick={toggleCollapsed}
            aria-expanded={!collapsed}
            aria-controls={collapsed ? undefined : bodyId}
          >
            <TextSwap swapKey={collapsed ? 'collapsed' : 'expanded'}>{collapsed ? t('展开') : t('收起')}</TextSwap>
            <Icon
              name="chevron-down"
              size={14}
              className={cn('shrink-0 transition-transform duration-ui', !collapsed && 'rotate-180')}
            />
          </button>
        </div>
      </div>

      {/* 首屏按记住的状态直接显示，不播展开补间；之后点「收起 / 展开」才有过渡。 */}
      <CollapsePresence open={!collapsed} id={bodyId} appear={false}>
        <div className="mt-4 border-t border-line">
          {busy && (
            <p role="status" className="mt-3 flex items-center gap-1.5 text-caption text-ink-500">
              <span className="size-1.5 shrink-0 animate-led-pulse rounded-full bg-ai-600" aria-hidden="true" />
              <ThinkingLabel>{t('模型正在生成报告，通常需要几分钟')}</ThinkingLabel>
            </p>
          )}
          {failed && <StatusNotice className="mt-4">{attemptFailureText(failed, year)}</StatusNotice>}
          {state === 'stale' && (
            <StaleStrip onRetry={retry} refreshing={latestQ.refreshing} className="mt-4" />
          )}

          <div className="mt-4">{body}</div>
        </div>
      </CollapsePresence>

      <ConfirmDialog
        open={confirmOpen}
        title={t('现在生成一份市场报告？')}
        description={t('模型会读取当前的行情、广度、宏观、行业、新闻与日历证据重新成文，通常需要几分钟，会消耗模型用量并计入每日次数。')}
        confirmLabel={t('开始生成')}
        onConfirm={() => void start()}
        onCancel={() => setConfirmOpen(false)}
      />
    </section>
  );
}
