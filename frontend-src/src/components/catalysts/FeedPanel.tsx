/** feed 新闻流面板：电报带列表 + 呼吸式刷新 + 游标分页 + 空态/骨架/503 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { useShell } from '@/components/Layout';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import InfoHint from '@/components/shared/InfoHint';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { catalystsContract } from './api';
import type { CatalystNewsItem } from './api';
import type { CatalystFilters } from './filters';
import { toFeedQuery } from './filters';
import { AnalysisStatusChip, ClassificationChip, ConfidenceLabel, ImpactValue, StaleChip, TickerChip } from './bits';
import { t as __t } from '../../i18n/core.ts';

const PAGE_SIZE = 12;

/* ---------------- 时间列 ---------------- */
function TimeCol({ iso }: { iso: string }) {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return (
    <div className="flex w-11 shrink-0 flex-col items-center pt-0.5">
      <span className="font-mono text-[11px] leading-[14px] text-ink-400 tnum">
        {sameDay
          ? d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
          : d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}
      </span>
      <span className="mt-1.5 hidden w-[2px] flex-1 rounded-full bg-line sm:block" aria-hidden="true" />
    </div>
  );
}

/* ---------------- 新闻行 ---------------- */
export function NewsRow({
  item,
  index,
  animate = true,
  onOpen,
}: {
  item: CatalystNewsItem;
  index: number;
  animate?: boolean;
  onOpen: (id: string) => void;
}) {
  const { openTicker } = useShell();
  const a = item.analysis;
  const bestImpact = a ? a.trustedStockImpacts.reduce((m, x) => (Math.abs(x.impactScore) > Math.abs(m?.impactScore ?? 0) ? x : m), a.trustedStockImpacts[0]) : null;
  return (
    <motion.article
      /* 繁忙 feed 列表：stagger ≤30ms，仅第一页播放；y 写法以便 hover 上浮可组合（内联 transform 字符串会挡住 whileHover） */
      initial={animate ? { opacity: 0, y: 14 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: animate ? Math.min(index * 0.03, 0.3) : 0 }}
      /* v8.1：行级去位移。上浮属于「卡片脱离纸面」的 elevation 隐喻——列表行无阴影无边界，
         浮起没有语义；60 行高频扫视区满屏跳也违反动效克制。背景色 + 标题下划线两重反馈已够。 */
      className="group relative flex cursor-pointer gap-3 px-4 py-[18px] transition-colors duration-fast hover:bg-paper-2/70 focus-visible:bg-paper-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-400/60 sm:px-5"
      onClick={() => onOpen(item.newsId)}
      /* 键盘可达：article + onClick 对 Tab 键用户完全不存在，右侧那颗
         「AI 分析」按钮又是 aria-hidden 的 span——整条新闻打不开。 */
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen(item.newsId);
        }
      }}
      aria-label={item.titleZh}
    >
      <TimeCol iso={item.publishedAt} />
      <div className="min-w-0 flex-1">
        {/* 顶行：来源 · 相对时间 · 多源 · 过期 */}
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-micro text-ink-400">
          <span className="font-medium text-ink-500">{item.source}</span>
          <span aria-hidden="true">·</span>
          <span className="font-mono tnum">{fmtRelative(item.publishedAt)}</span>
          {item.sourceCount > 1 && (
            <span className="rounded-xs bg-paper-2 px-1 py-px font-mono text-[10px] text-ink-400" title={__t("多源确认条数")}>
              {item.sourceCount} {__t('源')}
            </span>
          )}
          {item.isStale && <StaleChip />}
        </p>
        {/* 标题 */}
        <h3 className="mt-1.5 text-[15px] leading-[22px] font-semibold text-ink-900">
          <span className="bg-[linear-gradient(currentColor,currentColor)] bg-[length:0%_1px] bg-left-bottom bg-no-repeat transition-[background-size] duration-200 group-hover:bg-[length:100%_1px] group-hover:text-brand-600">
            {item.titleZh}
          </span>
        </h3>
        {/* 摘要 */}
        <p className="mt-1 line-clamp-2 text-body-s text-ink-500">{item.summaryZh}</p>
        {/* 底行：代码 chips + 分析状态 + 分类/置信度/影响 */}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <span className="flex max-w-full flex-wrap items-center gap-1" onClick={(e) => e.stopPropagation()}>
            {item.sourceTickers.map((t) => (
              <TickerChip key={t} ticker={t} onClick={() => openTicker(t)} />
            ))}
          </span>
          <AnalysisStatusChip status={item.analysisStatus} />
          {a && (
            <>
              <ClassificationChip classification={a.classification} />
              {/* 置信与影响共用一条说明（SCORE_HINTS.newsAssessment）。原先两个读数
                  后面各挂一句常驻免责声明（「· 非胜率」「· 非收益」）、又各带一个 ⓘ，
                  每条新闻重复一遍、又解释不了自己。声明留着，收进这一个 ⓘ。
                  三者同处一个 flex 单元：换行时一起走，ⓘ 不会被甩到下一行。 */}
              <span className="flex shrink-0 items-center gap-x-2 whitespace-nowrap">
                <ConfidenceLabel value={a.confidence} bare />
                {bestImpact && <ImpactValue value={bestImpact.impactScore} bare />}
                <InfoHint hint={SCORE_HINTS.newsAssessment} size={11} />
              </span>
            </>
          )}
        </div>
      </div>
      {/* 右侧：AI 分析幽灵钮（hover 显现） */}
      <div className="flex w-8 shrink-0 items-start justify-end">
        <span
          className="flex size-7 items-center justify-center rounded-sm border border-line bg-card text-ai-600 opacity-0 shadow-sh-1 transition-[opacity,transform] duration-fast group-hover:opacity-100 group-active:scale-95"
          title={__t("查看 / 生成 AI 分析")}
          aria-hidden="true"
        >
          <Icon name="spark-ai" size={14} />
        </span>
      </div>
    </motion.article>
  );
}

/* ---------------- 骨架行 ---------------- */
function FeedSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="divide-y divide-line" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex gap-3 px-4 py-[18px] sm:px-5">
          <div className="flex w-11 shrink-0 flex-col items-center">
            <SkeletonBlock className="h-3 w-8" />
            <SkeletonBlock className="mt-1.5 hidden w-[2px] flex-1 sm:block" />
          </div>
          <div className="min-w-0 flex-1">
            <SkeletonBlock className="h-2.5 w-28" />
            <SkeletonBlock className="mt-2 h-4 w-3/4" />
            <SkeletonBlock className="mt-2 h-3 w-full" />
            <div className="mt-2.5 flex gap-2">
              <SkeletonBlock className="h-4 w-10" />
              <SkeletonBlock className="h-4 w-14" />
              <SkeletonBlock className="h-4 w-16" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

type Phase = 'loading' | 'ready' | 'error';

interface FeedPanelProps {
  filters: CatalystFilters;
  onOpenNews: (id: string) => void;
  patches: Record<string, CatalystNewsItem>;
  refreshToken: number;
  /** 一轮加载的结果；ok=false 时父级不得更新「最后更新时间」（审计 P2-22）。 */
  onFeedResult: (result: { total: number | null; ok: boolean }) => void;
  onClearFilters: () => void;
}

export default function FeedPanel({ filters, onOpenNews, patches, refreshToken, onFeedResult, onClearFilters }: FeedPanelProps) {
  const [items, setItems] = useState<CatalystNewsItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hiddenUnanalyzed, setHiddenUnanalyzed] = useState(0);
  const [phase, setPhase] = useState<Phase>('loading');
  const [error, setError] = useState<ApiError | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [fading, setFading] = useState(false);
  const reqRef = useRef(0);
  const filtersKey = JSON.stringify(filters);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  /* 合并抽屉回写的分析结果 */
  useEffect(() => {
    const keys = Object.keys(patches);
    if (!keys.length) return;
    setItems((prev) => prev.map((it) => patches[it.newsId] ?? it));
  }, [patches]);

  const fetchFirst = useCallback(async () => {
    const reqId = ++reqRef.current;
    setPhase('loading');
    setError(null);
    try {
      const query = { ...toFeedQuery(filtersRef.current), limit: PAGE_SIZE };
      let res = await catalystsContract.feed(query);
      let hidden = res.hiddenUnanalyzed ?? 0;
      let hops = 0;
      while (res.items.length === 0 && res.nextCursor && hops < 8) {
        hops += 1;
        const more = await catalystsContract.feed({ ...query, cursor: res.nextCursor });
        hidden += more.hiddenUnanalyzed ?? 0;
        res = { ...more, total: res.total, hiddenUnanalyzed: hidden };
      }
      if (reqRef.current !== reqId) return;
      if (res.items.length === 0 && hidden === 0) {
        try {
          const today = await catalystsContract.newsToday();
          hidden = today.pending ?? 0;
        } catch {
          hidden = 0;
        }
      }
      if (reqRef.current !== reqId) return;
      setItems(res.items);
      setNextCursor(res.nextCursor);
      setHiddenUnanalyzed(hidden);
      onFeedResult({ total: res.total, ok: true });
      setPhase('ready');
    } catch (e) {
      if (reqRef.current !== reqId) return;
      setError(e instanceof ApiError ? e : new ApiError(500, __t('加载失败')));
      setPhase('error');
      onFeedResult({ total: null, ok: false });
    }
  }, [onFeedResult]);

  /* 过滤变更：淡出 200ms → 骨架 → 新列表（呼吸式刷新） */
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      void fetchFirst();
      return;
    }
    setFading(true);
    const t = window.setTimeout(() => {
      setFading(false);
      void fetchFirst();
    }, 200);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersKey]);

  /* 页头刷新钮 */
  useEffect(() => {
    if (refreshToken > 0) void fetchFirst();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    const reqId = reqRef.current;
    setLoadingMore(true);
    try {
      const res = await catalystsContract.feed({ ...toFeedQuery(filtersRef.current), limit: PAGE_SIZE, cursor: nextCursor });
      if (reqRef.current !== reqId) return;
      setItems((prev) => [...prev, ...res.items.filter((n) => !prev.some((p) => p.newsId === n.newsId))]);
      setNextCursor(res.nextCursor);
      setMoreError(null);
    } catch (error) {
      // 增量请求的异常此前被吞掉，用户只会觉得按钮没有反应（审计 P2-24）。
      setMoreError(error instanceof ApiError ? error : new ApiError(500, __t('加载更多失败')));
    } finally {
      setLoadingMore(false);
    }
  }, [nextCursor, loadingMore]);

  const hasFilters =
    filters.ticker !== '' ||
    filters.classification !== '' ||
    filters.analysisStatus !== '' ||
    filters.minConfidence > 0 ||
    filters.minAbsImpact > 0 ||
    filters.multiSourceOnly ||
    !!filters.themeId;

  return (
    <div className="card-surface overflow-hidden">
      {phase === 'loading' ? (
        <FeedSkeleton />
      ) : phase === 'error' ? (
        <EmptyState
          variant="error"
          icon="doc-quote"
          title={error?.code === 503 ? __t('新闻暂不可用') : __t('加载失败')}
          description={error?.code === 503 ? __t('稍后刷新再试') : error?.message}
          action={
            <button
              onClick={() => void fetchFirst()}
              className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105"
            >
              <Icon name="refresh" size={14} />
              {__t('重试')}
            </button>
          }
        />
      ) : items.length === 0 && !nextCursor ? (
        <EmptyState
          image="/empty-news.svg"
          title={
            hasFilters
              ? __t('这个角度暂时没有新闻')
              : hiddenUnanalyzed > 0
                ? __t('已收录、等中文分析')
                : __t('暂时没有新闻')
          }
          description={
            hasFilters
              ? __t('放宽过滤条件，或清除后查看全量新闻流')
              : hiddenUnanalyzed > 0
                ? __t('新闻已入库，中文标题与摘要生成后会显示在这里')
                : __t('新闻采集恢复后将自动出现在这里')
          }
          action={
            hasFilters ? (
              <button
                onClick={onClearFilters}
                className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105"
              >
                <Icon name="x" size={13} />
                {__t('清除过滤')}
              </button>
            ) : undefined
          }
        />
      ) : (
        /* items 为空但 nextCursor 还在：首页可能整页被中文校验过滤（译文未跟上），
           后面几页已翻译好的新闻必须还点得出来——空态若吞掉「加载更多」，那些
           新闻就永远不可达。 */
        <>
          <div
            className={cn('divide-y divide-line transition-opacity duration-200', fading && 'opacity-0')}
            aria-live="polite"
          >
            {items.map((it, i) => (
              /* 游标分页追加的项不再播放入场 */
              <NewsRow key={it.newsId} item={it} index={i} animate={i < PAGE_SIZE} onOpen={onOpenNews} />
            ))}
          </div>
          {/* 游标分页 */}
          <div className="border-t border-line px-5 py-3.5 text-center">
            {nextCursor ? (
              <button
                onClick={() => void loadMore()}
                disabled={loadingMore}
                className="inline-flex items-center gap-2 rounded-md border border-line bg-card px-4 py-2 text-caption font-medium text-ink-600 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:opacity-60"
              >
                {loadingMore && <span className="size-3.5 animate-spin rounded-full border-2 border-line-strong border-t-brand-600" aria-hidden="true" />}
                {__t('加载更多')}
              </button>
            ) : (
              <p className="text-micro text-ink-300">{__t('已加载全部')} {items.length} {__t('条')}</p>
            )}
            {moreError && (
              <p className="mt-1.5 text-micro text-down-700">
                {__t('加载更多失败：')}{moreError.message} ·{' '}
                <button type="button" onClick={() => void loadMore()} className="font-medium underline underline-offset-2">
                  {__t('重试')}
                </button>
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
