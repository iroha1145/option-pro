/** B1 热点主题带：横滑 snap 卡片列（flame-line + 热度计 + 代表新闻 + 代码 chips），点击打开代表新闻抽屉 */
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
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: Math.min(index * 0.05, 0.4) }}
      /* hover 上浮 -3px/240ms 走 whileHover（framer 入场后内联 transform:none 会压掉 CSS hover 位移），阴影用 CSS */
      whileHover={openable ? { y: -3, transition: { duration: 0.24, ease: 'easeOut' } } : undefined}
      onClick={onOpen}
      disabled={!openable}
      className={`group relative flex w-[260px] shrink-0 snap-start flex-col rounded-lg border border-line bg-card p-4 pt-3 text-left shadow-sh-1 transition-shadow duration-[240ms] ease-out sm:w-[300px] ${
        openable ? 'hover:shadow-sh-2' : 'cursor-default'
      }`}
    >
      {/* 顶边热度渐变条 */}
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-[2px] rounded-t-lg"
        /* v8.1：撤跨色渐变（违反自家「禁跨色渐变」纪律）。热度=温度，单一暖色即语义；
           强度仍由 opacity 编码，数值另有 HeatMeter 承担。 */
        style={{ background: '#E8930C', opacity: Math.min(1, 0.3 + h.heat / 140) }}
      />
      <div className="flex items-center gap-2">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-sm bg-warn-50 text-warn-600">
          <Icon name="flame-line" size={15} />
        </span>
        {h.eventType && (
          <span className="shrink-0 rounded-xs border border-brand-400/50 bg-brand-50 px-1.5 py-px text-[10px] leading-[14px] font-medium text-brand-600">
            {h.eventType}
          </span>
        )}
        <HeatMeter level={h.heatLevel} heat={h.heat} className="ml-auto" />
      </div>
      <p className="mt-2.5 line-clamp-2 min-h-[40px] text-body-s font-medium text-ink-800">{h.theme}</p>
      <div className="mt-2.5 flex items-center gap-1.5">
        {h.tickers.slice(0, 3).map((t) => (
          <TickerChip key={t} ticker={t} />
        ))}
        <span className="ml-auto font-mono text-micro text-ink-400 tnum">
          {h.newsCount} {h.countKind === 'sources' ? __t('源') : __t('条')}
        </span>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 border-t border-line pt-2">
        <span className="flex min-w-0 flex-wrap gap-1">
          {h.keywords.slice(0, 2).map((k) => (
            <span key={k} className="truncate rounded-xs border border-line px-1 py-0.5 text-[10px] leading-[14px] text-ink-400">
              {k}
            </span>
          ))}
        </span>
        {openable && (
          <span className="flex shrink-0 items-center gap-0.5 text-micro text-ink-300 group-hover:text-brand-600">
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
    <div key={i} className="w-[260px] shrink-0 snap-start rounded-lg border border-line bg-card p-4 sm:w-[300px]" aria-hidden="true">
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

  return (
    <section className="mt-6" aria-label={__t("热点主题带")}>
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-3">
          <p className="eyebrow">
            {__t('HOT THEMES · 热点带')}
            <InfoHint hint={SCORE_HINTS.hotScore} side="bottom" size={12} className="ml-1" />
          </p>
          <h2 className="text-h2 text-ink-900">{__t('市场在交易什么故事')}</h2>
        </div>
        {statusQ.data && (
          <p className="hidden items-center gap-1.5 text-micro text-ink-400 sm:flex">
            <Led tone={statusQ.data.scanning ? 'brand' : 'muted'} pulse={statusQ.data.scanning} className="size-1.5" />
            {__t('热点扫描')} {statusQ.data.scanning ? __t('活跃') : __t('已暂停')} · {fmtRelative(statusQ.data.updatedAt)}
          </p>
        )}
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
            <div className="flex w-[260px] shrink-0 snap-start flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-card-warm p-4 text-center sm:w-[300px]">
              <span className="size-5 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-hidden="true" />
              <p className="mt-2.5 text-body-s font-medium text-ink-800">{__t('热点计算中…')}</p>
              <p className="mt-1 font-mono text-micro text-ink-400 tnum">
                {statusQ.data?.etaSeconds != null ? __t('预计 {n}s', { n: statusQ.data.etaSeconds }) : __t('请稍候')}
              </p>
            </div>
          ) : null}
          {!listQ.loading && !computing && items.length === 0 && (
            <div className="flex w-full flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-card-warm py-8 text-center">
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
