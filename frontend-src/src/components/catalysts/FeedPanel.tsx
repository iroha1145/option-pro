import AnalysisIcon from '@/components/shared/AnalysisIcon';
/** feed 新闻流面板：电报带列表 + 呼吸式刷新 + 游标分页 + 空态/骨架/503 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { useShell } from '@/hooks/useShell';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import InfoHint from '@/components/shared/InfoHint';
import SoftBadge from '@/components/shared/SoftBadge';
import { cn } from '@/lib/utils';
import { fmtLocaleDate, fmtLocaleTime, fmtRelative } from '@/lib/format';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { catalystsContract } from './api';
import { useFeedResource } from './useFeedResource';
import { appendFeedPage, visibleFeedPage } from './feedSnapshot';
import CatalystCacheStatus from './CatalystCacheStatus';
import { cacheStatusProps } from './cacheStatusProps';
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
          ? fmtLocaleTime(iso)
          : fmtLocaleDate(iso, { month: '2-digit', day: '2-digit' })}
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
      className="group relative flex gap-3 px-4 py-[18px] transition-colors duration-fast hover:bg-paper-2/70 sm:px-5"
    >
      <button
        type="button"
        onClick={() => onOpen(item.newsId)}
        aria-label={item.titleZh}
        className="absolute inset-0 z-0 focus-visible:bg-paper-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-400/60"
      />
      <TimeCol iso={item.publishedAt} />
      {/* 内容层保留指针事件（可划选复制标题、title 提示可悬停），整行点击由这里
          转发到 onOpen；键盘/读屏走上面那颗覆盖层按钮。点在按钮/链接/ⓘ 上或
          正在划选文字时不转发——此前把内容层整体禁掉指针事件的方案，把文字选择
          与所有原生 title 一起杀掉了（复审实锤）。 */}
      <div
        className="relative z-10 min-w-0 flex-1 cursor-pointer"
        onClick={(event) => {
          const target = event.target as Element;
          if (target.closest('button, a, [role="button"], input, select, textarea')) return;
          if (window.getSelection()?.toString()) return;
          onOpen(item.newsId);
        }}
      >
        {/* 顶行：来源 · 相对时间 · 多源 · 过期 */}
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-micro text-ink-400">
          <span className="font-medium text-ink-500">{item.source}</span>
          <span aria-hidden="true">·</span>
          <span className="font-mono tnum">{fmtRelative(item.publishedAt)}</span>
          {item.sourceCount > 1 && (
            <SoftBadge className="font-mono" title={__t("多源确认条数")}>
              {item.sourceCount} {__t('源')}
            </SoftBadge>
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
          <span className="flex max-w-full flex-wrap items-center gap-1">
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
      {/* 右侧：独立分析入口，使用与标题相同的详情回调。 */}
      <div className="relative z-10 flex w-8 shrink-0 items-start justify-end">
        <button
          type="button"
          onClick={() => onOpen(item.newsId)}
          className="flex size-8 items-center justify-center rounded-md bg-ai-600 text-white shadow-btn transition-[filter] duration-fast hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ai-600/40 focus-visible:ring-offset-2"
          title={__t("查看 / 生成 AI 分析")}
          aria-label={__t("查看 / 生成 AI 分析")}
        >
          <AnalysisIcon size={14} />
        </button>
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

interface FeedPanelProps {
  filters: CatalystFilters;
  onOpenNews: (id: string) => void;
  patches: Record<string, CatalystNewsItem>;
  refreshToken: number;
  onFeedResult: (result: { total: number | null; ok: boolean; validatedAt?: number }) => void;
  onClearFilters: () => void;
}

export default function FeedPanel({ filters, onOpenNews, patches, onFeedResult, onClearFilters }: FeedPanelProps) {
  const q = useFeedResource(filters);
  const items = q.data?.items ?? [];
  const nextCursor = q.data?.nextCursor ?? null;
  const hiddenUnanalyzed = q.data?.hiddenUnanalyzed ?? 0;
  const error = q.error;
  const phase = q.data !== null ? 'ready' : error ? 'error' : 'loading';
  const fading = false;
  const fetchFirst = q.refresh;
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const keyRef = useRef(q.key);
  keyRef.current = q.key;
  const pendingMore = useRef(false);
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    pendingMore.current = false;
    setLoadingMore(false);
    setMoreError(null);
    return () => { generation.current += 1; };
  }, [q.key]);

  useEffect(() => {
    onFeedResult({ total: q.data?.total ?? null, ok: q.data !== null && !q.error && !q.restored,
      validatedAt: q.validatedAt || undefined });
  }, [q.data, q.error, q.restored, q.validatedAt, onFeedResult]);

  useEffect(() => {
    if (!Object.keys(patches).length) return;
    q.update((previous) => {
      if (!previous) return previous;
      let changed = false;
      const revised = previous.items.map((item) => {
        if (!patches[item.newsId] || patches[item.newsId] === item) return item;
        changed = true;
        return patches[item.newsId];
      });
      return changed ? { ...previous, items: revised } : previous;
    });
  }, [patches, q.update]);

  const loadMore = useCallback(async () => {
    if (!q.data || !q.data.nextCursor || pendingMore.current || q.refreshing) return;
    const previous = q.data;
    const key = q.key;
    const requestGeneration = generation.current;
    pendingMore.current = true;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await visibleFeedPage((cursor) => catalystsContract.feed({ ...toFeedQuery(filters), limit: PAGE_SIZE, cursor }), previous.nextCursor!);
      if (keyRef.current !== key || generation.current !== requestGeneration) return;
      q.update((current) => current ? appendFeedPage(current, page) : current, previous);
    } catch (cause) {
      if (keyRef.current !== key || generation.current !== requestGeneration) return;
      setMoreError(cause instanceof ApiError ? cause : new ApiError(500, __t('加载更多失败')));
    } finally {
      if (keyRef.current === key && generation.current === requestGeneration) {
        pendingMore.current = false;
        setLoadingMore(false);
      }
    }
  }, [q.data, q.key, q.refreshing, q.update, filters]);

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
      <CatalystCacheStatus {...cacheStatusProps(q)} />
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
              <NewsRow key={it.newsId} item={it} index={i} animate={false} onOpen={onOpenNews} />
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
