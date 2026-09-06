import SoftBadge from '@/components/shared/SoftBadge';
/** Shared card surfaces, with the existing heat, source and news interactions. */
import { motion } from 'framer-motion';
import { usePolling } from '@/hooks/usePolling';
import { catalystsContract } from './api';
import type { HotspotGroup } from './api';
import { HeatMeter, Led, TickerChip } from './bits';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import HorizontalScroller from '@/components/shared/HorizontalScroller';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import Icon from '@/components/icons';
import { fmtRelative } from '@/lib/format';
import { t as __t } from '../../i18n/core.ts';

function HotspotCard({ h, index, onOpen }: { h: HotspotGroup; index: number; onOpen: () => void }) {
  const openable = !!h.representative?.newsId;
  return (
    <motion.button
      type="button"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: Math.min(index * 0.05, 0.4) }}
      onClick={onOpen}
      disabled={!openable}
      data-hotspot-card
      className={`card-surface group relative flex w-[260px] shrink-0 snap-start flex-col overflow-hidden p-4 text-left sm:w-[300px] sm:p-5 ${
        openable ? 'card-hover' : 'cursor-default'
      }`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-warn-50 text-warn-700">
          <Icon name="flame-line" size={15} />
        </span>
        {h.eventType && (
          <SoftBadge tone="brand" className="shrink-0">
            {h.eventType}
          </SoftBadge>
        )}
        <HeatMeter level={h.heatLevel} heat={h.heat} className="ml-auto" />
      </div>
      <p className="mt-4 line-clamp-2 min-h-12 text-body font-semibold leading-6 text-ink-800">{h.theme}</p>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {h.tickers.slice(0, 3).map((t) => (
          <TickerChip key={t} ticker={t} />
        ))}
        <span className="ml-auto font-mono text-micro text-ink-400 tnum">
          {h.newsCount} {h.countKind === 'sources' ? __t('源') : __t('条')}
        </span>
      </div>
      <div className="mt-4 flex items-center justify-between gap-2 border-t border-line pt-3">
        <span className="flex min-w-0 flex-wrap gap-1">
          {h.keywords.slice(0, 2).map((k) => (
            <SoftBadge key={k} className="truncate">
              {k}
            </SoftBadge>
          ))}
        </span>
        {openable && (
          <span className="flex shrink-0 items-center gap-0.5 text-micro text-ink-500 transition-colors group-hover:text-brand-600">
            {__t('查看代表新闻')}
            <Icon name="chevron-right" size={12} />
          </span>
        )}
      </div>
    </motion.button>
  );
}

function HotspotSkeleton({ i }: { i: number }) {
  return (
    <div key={i} className="card-surface w-[260px] shrink-0 snap-start p-4 sm:w-[300px] sm:p-5" aria-hidden="true">
      <div className="flex items-center gap-2">
        <SkeletonBlock className="size-7" />
        <SkeletonBlock className="h-4 w-24" />
        <SkeletonBlock className="ml-auto h-3 w-10" />
      </div>
      <SkeletonBlock className="mt-3 h-3 w-full" />
      <SkeletonBlock className="mt-2 h-3 w-4/5" />
      <div className="mt-3 flex gap-1.5">
        <SkeletonBlock className="h-4 w-10" />
        <SkeletonBlock className="h-4 w-10" />
      </div>
    </div>
  );
}

export default function HotspotsStrip({ onOpenNews, refreshToken = 0 }: { onOpenNews: (newsId: string) => void; refreshToken?: number }) {
  /* refreshToken 参与依赖：页头「刷新」必须真的刷新热点带（审计 P2-21）。 */
  const listQ = usePolling(() => catalystsContract.hotspots(), 120_000, [refreshToken]);
  const statusQ = usePolling(() => catalystsContract.hotspotsStatus(), 45_000, [refreshToken]);

  const computing = statusQ.data?.state === 'computing';
  const items = listQ.data ?? [];
  /* 四态（审计 2.2.3）：首取失败是「读不到」，不是「暂无热点分组」；
     轮询失败但有旧数据时继续展示旧卡片并明确标注已过期。 */
  const listFailed = !!listQ.error && listQ.data === null;
  const listStale = !!listQ.error && listQ.data !== null;

  return (
    <section className="mt-6" aria-label={__t("热点主题带")}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="eyebrow">
            {__t('HOT THEMES · 热点带')}
            <InfoHint hint={SCORE_HINTS.hotScore} side="bottom" size={12} className="ml-1" />
          </p>
          <h2 className="mt-1 text-h2 text-ink-900">{__t('市场在交易什么故事')}</h2>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {listStale && (
            <SoftBadge tone="warn" className="whitespace-normal">
              {__t('已过期 · 刷新失败，展示上次结果')}
            </SoftBadge>
          )}
          {statusQ.data && (
            <p className="hidden items-center gap-1.5 text-micro text-ink-400 sm:flex">
              <Led tone={statusQ.data.scanning ? 'brand' : 'muted'} pulse={statusQ.data.scanning} className="size-1.5" />
              {__t('热点扫描')} {statusQ.data.scanning ? __t('活跃') : __t('已暂停')} · {fmtRelative(statusQ.data.updatedAt)}
            </p>
          )}
        </div>
      </div>

      {/* 8 张 260px 的卡合计约 2200px，一定溢出。滚动条是藏起来的，触屏能划，
          桌面端普通鼠标却完全无从滚动 —— 最右边那张永远半截，也看不出右边还有没有。
          HorizontalScroller 补上渐隐遮罩与左右按钮，触屏行为不变。 */}
      <HorizontalScroller
        className="-mx-4 mt-4 md:-mx-8"
        scrollerClassName="px-4 pb-2 md:px-8"
        label={__t("热点主题带，可横向滚动")}
      >
        <div className="flex snap-x snap-mandatory gap-3">
          {listQ.loading && items.length === 0 ? (
            [0, 1, 2].map((i) => <HotspotSkeleton key={i} i={i} />)
          ) : computing ? (
            /* hotspots/status 计算中：带首卡替换为状态卡 */
            <div className="card-surface flex min-h-44 w-[260px] shrink-0 snap-start flex-col items-center justify-center p-5 text-center sm:w-[300px]">
              <span className="size-5 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-hidden="true" />
              <p className="mt-2.5 text-body-s font-medium text-ink-800">{__t('热点计算中…')}</p>
              <p className="mt-1 font-mono text-micro text-ink-400 tnum">
                {statusQ.data?.etaSeconds != null ? __t('预计 {n}s', { n: statusQ.data.etaSeconds }) : __t('请稍候')}
              </p>
            </div>
          ) : null}
          {!listQ.loading && !computing && listFailed && (
            <div className="card-surface flex w-full flex-col items-center justify-center px-5 py-8 text-center">
              <p className="text-body-s text-ink-500">{__t('热点数据读取失败')}</p>
              <p className="mt-1 text-micro text-ink-400">{__t('是读取失败，不代表市场没有热点')}</p>
              <button
                onClick={() => listQ.refresh()}
                className="mt-3 rounded-md border border-line px-3 py-1.5 text-caption text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
              >
                {__t('重试')}
              </button>
            </div>
          )}
          {!listQ.loading && !computing && !listFailed && items.length === 0 && (
            <div className="card-surface flex w-full flex-col items-center justify-center px-5 py-8 text-center">
              <p className="text-body-s text-ink-500">{__t('当前窗口暂无热点分组')}</p>
            </div>
          )}
          {!listQ.loading &&
            !computing &&
            items.map((h, i) => (
              <HotspotCard
                key={h.hotspotId}
                h={h}
                index={i}
                onOpen={() => {
                  if (h.representative?.newsId) onOpenNews(h.representative.newsId);
                }}
              />
            ))}
        </div>
      </HorizontalScroller>
    </section>
  );
}
