/** 状态 hero：数据源状态 / 热点计算 / 分析可用性（catalysts status + hotspots/status）+ SourceNote */
import { motion } from 'framer-motion';
import { usePolling } from '@/hooks/usePolling';
import { catalystsContract } from './api';
import { Led } from './bits';
import SourceNote from '@/components/shared/SourceNote';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { fmtRelative } from '@/lib/format';

function HeroCell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0 flex-1 px-4 py-4 first:pl-5 last:pr-5 sm:px-5">
      <p className="eyebrow">{label}</p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

export default function StatusHero() {
  const statusQ = usePolling(() => catalystsContract.status(), 45_000);
  const hotStatusQ = usePolling(() => catalystsContract.hotspotsStatus(), 45_000);

  const s = statusQ.data;
  const hs = hotStatusQ.data;
  const loading = statusQ.loading && !s;

  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1] }}
      aria-label="数据源状态"
      className="card-surface mt-6"
    >
      <div className="flex flex-col divide-y divide-line sm:flex-row sm:divide-x sm:divide-y-0">
        <HeroCell label="数据源状态">
          {loading ? (
            <SkeletonBlock className="h-5 w-32" />
          ) : (
            <div className="flex items-center gap-2">
              <Led tone={s?.collecting ? 'up' : 'muted'} pulse={!!s?.collecting} />
              <span className="text-body-s font-medium text-ink-800">
                {s?.collecting ? '采集中' : '已暂停'}
                {s?.collecting && <span className="text-ink-500"> · 每 {s.intervalMinutes} 分钟</span>}
              </span>
              <span className="font-mono text-micro text-ink-400 tnum">
                {s ? `${s.sourcesActive}/${s.sourcesTotal} 源` : ''}
              </span>
            </div>
          )}
          {s && <p className="mt-1 font-mono text-micro text-ink-400 tnum">上次采集 {fmtRelative(s.lastCrawlAt)}</p>}
        </HeroCell>

        <HeroCell label="热点计算">
          {hotStatusQ.loading && !hs ? (
            <SkeletonBlock className="h-5 w-28" />
          ) : hs?.state === 'computing' ? (
            <div className="flex items-center gap-2">
              <Led tone="warn" pulse />
              <span className="text-body-s font-medium text-ink-800">热点计算中…</span>
              {hs.etaSeconds != null && (
                <span className="font-mono text-micro text-ink-400 tnum">预计 {hs.etaSeconds}s</span>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Led tone="brand" pulse={!!hs?.scanning} />
              <span className="text-body-s font-medium text-ink-800">扫描中 · {hs?.groupCount ?? 0} 组热点</span>
            </div>
          )}
          {hs && <p className="mt-1 font-mono text-micro text-ink-400 tnum">更新 {fmtRelative(hs.updatedAt)}</p>}
        </HeroCell>

        <HeroCell label="分析可用性">
          {loading ? (
            <SkeletonBlock className="h-5 w-28" />
          ) : (
            <div className="flex items-center gap-2">
              <Led tone={s?.analysisAvailable ? 'ai' : 'down'} pulse={!!s?.analysisAvailable} />
              <span className="text-body-s font-medium text-ink-800">
                {s?.analysisAvailable ? '模型分析可用' : '模型分析不可用'}
              </span>
              <Icon name="spark-ai" size={14} className="text-ai-600" />
            </div>
          )}
          {s && (
            <p className="mt-1 font-mono text-micro text-ink-400 tnum">
              队列 {s.queueDepth} · 已分析 <span className="text-ink-600">{s.analyzedToday}</span>
            </p>
          )}
        </HeroCell>

        <HeroCell label="今日新闻">
          {loading ? (
            <SkeletonBlock className="h-7 w-16" />
          ) : (
            <p className="font-mono text-data-l text-ink-900 tnum">
              {s?.newsToday ?? 0}
              <span className="ml-1.5 text-micro font-normal text-ink-400">条 / 24h</span>
            </p>
          )}
          {s && <p className="mt-0.5 font-mono text-micro text-ink-400 tnum">覆盖 6 大主题线</p>}
        </HeroCell>
      </div>
      <div className="px-5 pb-3">
        <SourceNote text="来源：Optix NewsDesk · 延迟新闻流 · 影响分与置信度为模型估计" />
      </div>
    </motion.section>
  );
}
