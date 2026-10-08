/**
 * 首页「市场综合研判」：每个交易日开盘前、收盘后各一份，由 Claude 生成（后端 market_brief）。
 *
 * 60 秒读一次 latest。Owner 可以手动生成：提交后每 20 秒跟进一次，出现新研判或新的失败
 * 记录就停，最长 25 分钟。状态：首次加载骨架；读不到且没有旧数据 → 错误与重试；还没有
 * 任何研判 → 写明下一个时段；刷新失败但有旧数据 → 陈旧条；最近一次运行失败 → 一行说明，
 * 正文仍是上一份成功的研判。
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { invalidateQueryPaths } from '@/api/queryRegistry';
import { MARKET_BRIEF_LATEST_PATH, marketBriefApi } from '@/api/modules/marketBrief';
import ConfirmDialog from '@/components/catalysts/ConfirmDialog';
import AnalysisIcon from '@/components/shared/AnalysisIcon';
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

interface Outcome {
  seq: number;
  kind: 'ready' | 'failed';
  reason: string | null;
}

function BriefSkeleton() {
  return (
    <div>
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
  const submittingRef = useRef(false);

  const data = latestQ.data;
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
    if (outcome.kind === 'ready') toast.success(t('新研判已生成'));
    else toast.error(t('研判生成失败'), outcome.reason ?? undefined);
  }, [outcome, toast]);

  /* 跟进期间每 20 秒硬失效再强制读：注册表的 60 秒新鲜窗口和浏览器 HTTP 缓存都会让
     普通刷新拿回旧研判（MacroConditionsPanel 同一做法）。页面隐藏时只计时、不读。 */
  const refreshLatest = latestQ.refresh;
  useEffect(() => {
    if (!follow) return;
    const timer = window.setInterval(() => {
      if (Date.now() - follow.since >= FOLLOW_TIMEOUT_MS) {
        setFollow(null);
        toast.info(t('研判仍在生成'), t('完成后会随卡片的定时刷新显示'));
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
        title={t('首份研判尚未生成')}
        description={nextSlotText(data?.nextSlot ?? null, year) ?? undefined}
      />
    );
  } else {
    body = <MarketBriefContent brief={brief} year={year} />;
  }

  return (
    <section aria-label={t('市场综合研判')} className={cn('card-surface p-4 sm:p-5 lg:p-6', className)}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line pb-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-ai-50 text-ai-600" aria-hidden="true">
            <AnalysisIcon size={17} />
          </span>
          <h2 className="text-h3 text-ink-900">{t('市场综合研判')}</h2>
          {brief && (
            <SoftBadge tone="ai">{t('{model} 生成', { model: brief.model.label ?? brief.model.id ?? 'AI' })}</SoftBadge>
          )}
        </div>
        {brief && (
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            {previous && <SoftBadge tone="warn">{t('上一份')}</SoftBadge>}
            <SoftBadge>{slotTag(brief.slot, brief.tradingDate, year)}</SoftBadge>
            {generated && <span className="text-micro text-ink-400 tnum">{generated}</span>}
          </div>
        )}
        <div className="ml-auto flex shrink-0 items-center">
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
        </div>
      </div>

      {busy && (
        <p role="status" className="mt-3 flex items-center gap-1.5 text-caption text-ink-500">
          <span className="size-1.5 shrink-0 animate-led-pulse rounded-full bg-ai-600" aria-hidden="true" />
          <ThinkingLabel>{t('模型正在生成研判，通常需要几分钟')}</ThinkingLabel>
        </p>
      )}
      {failed && <StatusNotice className="mt-4">{attemptFailureText(failed, year)}</StatusNotice>}
      {state === 'stale' && (
        <StaleStrip onRetry={retry} refreshing={latestQ.refreshing} className="mt-4" />
      )}

      <div className="mt-4">{body}</div>

      <ConfirmDialog
        open={confirmOpen}
        title={t('现在生成一份市场综合研判？')}
        description={t('模型会读取当前的行情、广度、宏观、板块、新闻与日历证据重新成文，通常需要几分钟，会消耗模型用量并计入每日次数。')}
        confirmLabel={t('开始生成')}
        onConfirm={() => void start()}
        onCancel={() => setConfirmOpen(false)}
      />
    </section>
  );
}
