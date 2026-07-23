/** B1 热点主题带：横滑 snap 卡片列（flame-line + 热度计 + 代表新闻 + 代码 chips），点击过滤新闻流 */
import { motion } from 'framer-motion';
import { usePolling } from '@/hooks/usePolling';
import { catalystsContract } from './api';
import type { HotspotGroup } from './api';
import { HeatMeter, Led, TickerChip } from './bits';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { fmtRelative } from '@/lib/format';

function HotspotCard({ h, index, active, onSelect }: { h: HotspotGroup; index: number; active: boolean; onSelect: () => void }) {
  return (
    <motion.button
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: Math.min(index * 0.05, 0.4) }}
      onClick={onSelect}
      className={`group relative flex w-[260px] shrink-0 snap-start flex-col rounded-lg border bg-card p-4 pt-3 text-left transition-all duration-ui ease-paper sm:w-[300px] ${
        active ? 'border-brand-400 shadow-sh-2' : 'border-line shadow-sh-1'
      } hover:-translate-y-0.5 hover:shadow-sh-2`}
      aria-pressed={active}
    >
      {/* 顶边热度渐变条 */}
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 h-[2px] rounded-t-lg"
        style={{ background: 'linear-gradient(90deg, #E8930C, #2E46E0)', opacity: Math.min(1, 0.35 + h.heat / 160) }}
      />
      <div className="flex items-center gap-2">
        <span className="flex size-7 items-center justify-center rounded-sm bg-warn-50 text-warn-600">
          <Icon name="flame-line" size={15} />
        </span>
        <h3 className="min-w-0 flex-1 truncate font-display text-[16px] font-semibold text-ink-900">{h.theme}</h3>
        <HeatMeter level={h.heatLevel} heat={h.heat} />
      </div>
      {h.representative && (
        <p className="mt-2.5 line-clamp-2 min-h-[40px] text-body-s text-ink-600">{h.representative.titleZh}</p>
      )}
      <div className="mt-2.5 flex items-center gap-1.5">
        {h.tickers.slice(0, 3).map((t) => (
          <TickerChip key={t} ticker={t} />
        ))}
        <span className="ml-auto font-mono text-micro text-ink-400 tnum">{h.newsCount} 条</span>
      </div>
      <div className="mt-2 flex items-center justify-between border-t border-line pt-2">
        <span className="flex flex-wrap gap-1">
          {h.keywords.slice(0, 2).map((k) => (
            <span key={k} className="rounded-xs border border-line px-1 py-0.5 text-[10px] leading-[14px] text-ink-400">
              {k}
            </span>
          ))}
        </span>
        <span className="text-micro text-ink-300 group-hover:text-brand-600">{active ? '已过滤' : '点击过滤 →'}</span>
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

export default function HotspotsStrip({ activeThemeId, onSelectTheme }: { activeThemeId: string | null; onSelectTheme: (id: string | null) => void }) {
  const listQ = usePolling(() => catalystsContract.hotspots(), 120_000);
  const statusQ = usePolling(() => catalystsContract.hotspotsStatus(), 45_000);

  const computing = statusQ.data?.state === 'computing';
  const items = listQ.data ?? [];

  return (
    <section className="mt-6" aria-label="热点主题带">
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-3">
          <p className="eyebrow">HOT THEMES · 热点带</p>
          <h2 className="text-h2 text-ink-900">市场在交易什么故事</h2>
        </div>
        {statusQ.data && (
          <p className="hidden items-center gap-1.5 text-micro text-ink-400 sm:flex">
            <Led tone={statusQ.data.scanning ? 'brand' : 'muted'} pulse={statusQ.data.scanning} className="size-1.5" />
            热点扫描 {statusQ.data.scanning ? '进行中' : '已暂停'} · {fmtRelative(statusQ.data.updatedAt)}
          </p>
        )}
      </div>

      <div className="-mx-4 mt-4 overflow-x-auto px-4 pb-2 no-scrollbar md:-mx-8 md:px-8">
        <div className="flex snap-x snap-mandatory gap-3">
          {listQ.loading && items.length === 0 ? (
            [0, 1, 2].map((i) => <HotspotSkeleton key={i} i={i} />)
          ) : computing ? (
            /* hotspots/status 异常：带首卡替换为状态卡 */
            <div className="flex w-[260px] shrink-0 snap-start flex-col items-center justify-center rounded-lg border border-dashed border-line-strong bg-card-warm p-4 text-center sm:w-[300px]">
              <span className="size-5 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-hidden="true" />
              <p className="mt-2.5 text-body-s font-medium text-ink-800">热点计算中…</p>
              <p className="mt-1 font-mono text-micro text-ink-400 tnum">
                {statusQ.data?.etaSeconds != null ? `预计 ${statusQ.data.etaSeconds}s` : '请稍候'}
              </p>
            </div>
          ) : null}
          {!listQ.loading &&
            !computing &&
            items.map((h, i) => (
              <HotspotCard
                key={h.hotspotId}
                h={h}
                index={i}
                active={activeThemeId === h.hotspotId}
                onSelect={() => onSelectTheme(activeThemeId === h.hotspotId ? null : h.hotspotId)}
              />
            ))}
          {/* 带尾「查看全部热点」虚线卡 */}
          {!listQ.loading && !computing && (
            <button
              onClick={() => onSelectTheme(null)}
              className="flex w-[120px] shrink-0 snap-start flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong text-ink-400 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
            >
              <Icon name="dots-grid" size={18} />
              <span className="text-caption">查看全部热点</span>
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
