/** 状态 hero：数据源状态 / 热点计算 / 分析可用性 / 今日新闻（真实契约口径，不可用原因如实标注） */
import { motion } from 'framer-motion';
import { usePolling } from '@/hooks/usePolling';
import { remoteState } from '@/hooks/remoteState';
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

/** 分析不可用原因 → 中文（对齐 personal_service.analysis_availability 的真实原因码；未知码给通用文案） */
const ANALYSIS_REASON_CN: Record<string, { label: string; tone: 'muted' | 'down' | 'warn' }> = {
  owner_login_required: { label: '需 Owner 登录', tone: 'muted' },
  not_configured: { label: '未配置模型密钥', tone: 'down' },
  ai_not_configured: { label: '未配置模型密钥', tone: 'down' },
  settings_unavailable: { label: '运行设置不可用', tone: 'down' },
  read_only_mode: { label: '只读模式', tone: 'muted' },
  manual_analysis_disabled: { label: '手动分析已关闭', tone: 'muted' },
  worker_unavailable: { label: '后台 worker 不可用', tone: 'down' },
  daily_token_limit: { label: '今日 Token 预算已用完', tone: 'warn' },
  daily_budget_usd_reached: { label: '今日预算已用完', tone: 'warn' },
  analysis_in_progress: { label: '分析任务进行中', tone: 'warn' },
  cooldown_active: { label: '冷却中', tone: 'warn' },
  catalyst_disabled: { label: '催化剂模块未启用', tone: 'down' },
};

export default function StatusHero({ refreshToken = 0 }: { refreshToken?: number }) {
  /* refreshToken 参与依赖：页头「刷新」必须真的刷新这一栏（审计 P2-21）。 */
  const statusQ = usePolling(() => catalystsContract.status(), 45_000, [refreshToken]);
  const hotStatusQ = usePolling(() => catalystsContract.hotspotsStatus(), 45_000, [refreshToken]);
  const newsQ = usePolling(() => catalystsContract.newsToday(), 120_000, [refreshToken]);

  const s = statusQ.data;
  const hs = hotStatusQ.data;
  /* 状态读取失败与「采集暂停 / 热点 0 组 / 模型不可用」是三件不同的事（审计 P1-13）。
     旧实现只有 loading 与「有数据」两种分支，于是接口失败会被画成真实业务状态。 */
  const statusState = remoteState(statusQ);
  const hotState = remoteState(hotStatusQ);
  const loading = statusState === 'loading';
  const statusUnread = statusState === 'error';
  const hotUnread = hotState === 'error';

  const reason = s?.analysisReason ? ANALYSIS_REASON_CN[s.analysisReason] ?? { label: '模型分析不可用', tone: 'down' as const } : null;
  const unreadCell = (
    <div className="flex items-center gap-2">
      <Led tone="warn" />
      <span className="text-body-s font-medium text-ink-800">状态读取失败</span>
    </div>
  );

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
          ) : statusUnread ? (
            unreadCell
          ) : (
            <div className="flex items-center gap-2">
              <Led tone={s?.collecting ? 'up' : 'muted'} pulse={!!s?.collecting} />
              <span className="text-body-s font-medium text-ink-800">
                {s?.collecting ? '采集中' : '已暂停'}
                {s?.collecting && s.intervalMinutes != null && <span className="text-ink-500"> · 每 {s.intervalMinutes} 分钟</span>}
              </span>
              <span className="font-mono text-micro text-ink-400 tnum">
                {s && s.sourcesTotal > 0 ? `${s.sourcesActive}/${s.sourcesTotal} ${s.streams?.length ? '流' : '源'}` : ''}
              </span>
            </div>
          )}
          {s && (
            <p className="mt-1 flex flex-wrap items-center gap-x-2.5 font-mono text-micro text-ink-400 tnum">
              <span>上次采集 {fmtRelative(s.lastCrawlAt)}</span>
              {s.streams?.map((st) => (
                <span key={st.name} className="inline-flex items-center gap-1">
                  <Led tone={st.ok ? 'up' : 'down'} className="size-1.5" />
                  {st.name}
                </span>
              ))}
            </p>
          )}
        </HeroCell>

        <HeroCell label="热点计算">
          {hotState === 'loading' ? (
            <SkeletonBlock className="h-5 w-28" />
          ) : hotUnread ? (
            unreadCell
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
              <Led tone={hs?.scanning ? 'brand' : 'muted'} pulse={!!hs?.scanning} />
              <span className="text-body-s font-medium text-ink-800">
                {hs?.scanning ? '已就绪' : '已暂停'} · <span className="font-mono tnum">{hs?.groupCount ?? 0}</span> 组热点
              </span>
            </div>
          )}
          {hs && <p className="mt-1 font-mono text-micro text-ink-400 tnum">更新 {fmtRelative(hs.updatedAt)}</p>}
        </HeroCell>

        <HeroCell label="分析可用性">
          {loading ? (
            <SkeletonBlock className="h-5 w-28" />
          ) : statusUnread ? (
            unreadCell
          ) : (
            <div className="flex items-center gap-2">
              {/* 「分析可用」是静态状态，不脉冲（脉冲只给真实进行中的任务） */}
              <Led
                tone={s?.analysisAvailable ? 'ai' : reason ? reason.tone : 'down'}
              />
              <span className="text-body-s font-medium text-ink-800">
                {s?.analysisAvailable ? '模型分析可用' : reason ? reason.label : '模型分析不可用'}
              </span>
              <Icon name="spark-ai" size={14} className="text-ai-600" />
            </div>
          )}
          {s && (
            <p className="mt-1 font-mono text-micro text-ink-400 tnum">
              {s.analysisModel ? (
                <>
                  模型 {s.analysisModel}
                  {s.analysisReasoning ? ` · ${s.analysisReasoning}` : ''}
                </>
              ) : s.queueDepth != null ? (
                <>
                  队列 {s.queueDepth} · 已分析 <span className="text-ink-600">{s.analyzedToday ?? 0}</span>
                </>
              ) : null}
            </p>
          )}
        </HeroCell>

        <HeroCell label="今日新闻">
          {newsQ.loading && !newsQ.data ? (
            <SkeletonBlock className="h-7 w-16" />
          ) : (
            <p className="font-mono text-data-l text-ink-900 tnum">
              {newsQ.data ? `${newsQ.data.count}${newsQ.data.saturated ? '+' : ''}` : '—'}
              <span className="ml-1.5 text-micro font-normal text-ink-400">条 / 24h</span>
            </p>
          )}
          {newsQ.data && (
            <p className="mt-0.5 font-mono text-micro text-ink-400 tnum">
              已分析 <span className="text-ink-600">{newsQ.data.analyzed}</span> 条
            </p>
          )}
        </HeroCell>
      </div>
      <div className="px-5 pb-3">
        <SourceNote text="新闻与经济日历持续收录；每条新闻标注原始来源；滞后表示数据更新到了什么时候；影响分与置信度为 AI 估计" />
      </div>
    </motion.section>
  );
}
