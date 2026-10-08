import AnalysisIcon from '@/components/shared/AnalysisIcon';
/** 状态 hero：数据源状态 / 热点计算 / 分析可用性 / 今日新闻（真实契约口径，不可用原因如实标注） */
import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { useAccess } from '@/hooks/useAccess';
import { sharedAiBudgetText } from '../../api/aiBudget.ts';
import { usePolling } from '@/hooks/usePolling';
import { remoteState } from '@/hooks/remoteState';
import { catalystsContract } from './api';
import { Led } from './bits';
import SoftBadge from '@/components/shared/SoftBadge';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import { fmtRelative } from '@/lib/format';
import { aiModelLabel } from '@/lib/aiModelLabel';
import { afterLoadIdle } from '@/lib/afterLoadIdle';
import { DUR_SECTION, EASE_PAPER } from '@/lib/motion';
import { t } from '../../i18n/core.ts';

/** 列数由状态栏自身宽度决定；手机上标签与内容成行，特别窄时再上下排列。 */
function HeroCell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="news-status-cell min-w-0 px-4 py-4 sm:px-5">
      <p className="news-status-label eyebrow">{label}</p>
      <div className="news-status-content min-w-0">{children}</div>
    </div>
  );
}

/** 分析不可用原因 → 中文（对齐 personal_service.analysis_availability 的真实原因码；未知码给通用文案） */
const ANALYSIS_REASON_CN: Record<string, { label: string; tone: 'muted' | 'down' | 'warn' }> = {
  owner_login_required: { label: t('需管理员登录'), tone: 'muted' },
  not_configured: { label: t('未配置模型密钥'), tone: 'down' },
  ai_not_configured: { label: t('未配置模型密钥'), tone: 'down' },
  shared_budget_unavailable: { label: t('共享预算暂时无法核对，请稍后重试'), tone: 'warn' },
  settings_unavailable: { label: t('运行设置不可用'), tone: 'down' },
  read_only_mode: { label: t('只读模式'), tone: 'muted' },
  manual_analysis_disabled: { label: t('手动分析已关闭'), tone: 'muted' },
  worker_unavailable: { label: t('后台服务暂不可用'), tone: 'down' },
  daily_token_limit: { label: t('今日分析用量已达上限'), tone: 'warn' },
  daily_budget_usd_reached: { label: t('共享日预算不足'), tone: 'warn' },
  /* 这是上次请求的结果，并非实时余额；充值后的手动请求可以确认恢复。 */
  provider_credit_exhausted: { label: t('分析服务余额不足，充值后重试'), tone: 'down' },
  analysis_in_progress: { label: t('分析任务进行中'), tone: 'warn' },
  cooldown_active: { label: t('冷却中'), tone: 'warn' },
  catalyst_disabled: { label: t('新闻模块未启用'), tone: 'down' },
};

export default function StatusHero({ refreshToken = 0, feedSettled = false }: { refreshToken?: number; feedSettled?: boolean }) {
  /* refreshToken 参与依赖：页头「刷新」必须真的刷新这一栏（审计 P2-21）。 */
  const statusQ = usePolling(() => catalystsContract.status(), 45_000, [refreshToken]);
  const hotStatusQ = usePolling(() => catalystsContract.hotspotsStatus(), 45_000, [refreshToken]);
  /* 今日计数走完整 24h feed 汇总，与列表 72h/12 不是同一请求。
     首屏先让 FeedPanel 占用连接：feed 首页落地（成功或失败）就拉，否则 load 后
     固定延迟兜底；站内切换命中缓存时不必干等。数字口径不变。 */
  const [newsTodayEnabled, setNewsTodayEnabled] = useState(false);
  useEffect(() => {
    if (refreshToken > 0) {
      return;
    }
    return afterLoadIdle(() => setNewsTodayEnabled(true), 3500);
  }, [refreshToken]);
  const newsQ = usePolling(() => catalystsContract.newsToday(), 120_000, [refreshToken], {
    enabled: refreshToken > 0 || newsTodayEnabled || feedSettled,
  });

  const s = statusQ.data;
  const hs = hotStatusQ.data;
  /* 状态读取失败与「采集暂停 / 热点 0 组 / 模型不可用」是三件不同的事（审计 P1-13）。
     旧实现只有 loading 与「有数据」两种分支，于是接口失败会被画成真实业务状态。 */
  const statusState = remoteState(statusQ);
  const hotState = remoteState(hotStatusQ);
  const loading = statusState === 'loading';
  /* stale（读取失败但有旧数据）与 error 同待遇：状态灯是「现在能不能采集」，
     拿 15 分钟前的旧快照亮绿灯说「采集中」，是把读不到伪装成健康。 */
  const statusUnread = statusState === 'error' || statusState === 'stale';
  const hotUnread = hotState === 'error' || hotState === 'stale';

  const sharedBudget = s?.analysisBudget && s.analysisBudget.dailyBudgetUsd > 0;
  const reasonCode = sharedBudget && s?.analysisReason === 'daily_token_limit'
    ? s.analysisBudget?.dollarBudgetAvailable === false ? 'daily_budget_usd_reached' : null
    : s?.analysisReason;
  const { isOwner } = useAccess();
  const budgetText = isOwner && !statusUnread && reasonCode !== 'shared_budget_unavailable' ? sharedAiBudgetText(s?.analysisBudget) : null;
  const reason = reasonCode ? ANALYSIS_REASON_CN[reasonCode] ?? { label: t('模型分析不可用'), tone: 'down' as const } : null;
  const unreadCell = (
    <SoftBadge tone="warn" size="md" className="whitespace-normal">
      <Led tone="warn" />
      {t('状态读取失败')}
    </SoftBadge>
  );

  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR_SECTION, ease: EASE_PAPER }}
      aria-label={t("来源状态")}
      className="card-surface news-status mt-5 sm:mt-6"
    >
      <div className="news-status-grid">
        <HeroCell label={t("来源状态")}>
          {loading ? (
            <SkeletonBlock className="h-5 w-32 max-w-full" />
          ) : statusUnread ? (
            unreadCell
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <SoftBadge tone={s?.collecting ? 'ok' : 'neutral'} size="md" className="whitespace-normal">
                <Led tone={s?.collecting ? 'ok' : 'muted'} pulse={!!s?.collecting} />
                <span>{s?.collecting ? t('正在获取') : t('已暂停')}</span>
              </SoftBadge>
              <span className="whitespace-nowrap text-micro text-ink-400 tnum">
                {s && s.sourcesTotal > 0 ? `${s.sourcesActive}/${s.sourcesTotal} ${s.streams?.length ? t('流') : t('源')}` : ''}
              </span>
            </div>
          )}
          {s && (
            <p className="mt-1 flex flex-wrap items-center gap-x-2.5 text-micro text-ink-400 tnum">
              <span>{t('最近获取')} {fmtRelative(s.lastCrawlAt)}</span>
              {s.collecting && s.intervalMinutes != null && (
                <span className="whitespace-nowrap">{t('· 每')} {s.intervalMinutes} {t('分钟')}</span>
              )}
              {s.streams?.map((st) => (
                <SoftBadge key={st.name} tone={st.ok ? 'ok' : 'danger'} className="whitespace-normal [overflow-wrap:anywhere]">
                  <Led tone={st.ok ? 'ok' : 'danger'} className="size-1.5" />
                  {st.name}
                </SoftBadge>
              ))}
            </p>
          )}
        </HeroCell>

        <HeroCell label={t("热点整理")}>
          {hotState === 'loading' ? (
            <SkeletonBlock className="h-5 w-28 max-w-full" />
          ) : hotUnread ? (
            unreadCell
          ) : hs?.state === 'computing' ? (
            <div className="flex flex-wrap items-center gap-2">
              <SoftBadge tone="warn" size="md" className="whitespace-normal">
                <Led tone="warn" pulse />
                <span>{t('正在整理热点…')}</span>
              </SoftBadge>
              {hs.etaSeconds != null && (
                <span className="text-micro text-ink-400 tnum">{t('预计')} {hs.etaSeconds}s</span>
              )}
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <SoftBadge tone={hs?.scanning ? 'brand' : 'neutral'} size="md" className="whitespace-normal">
                <Led tone={hs?.scanning ? 'brand' : 'muted'} pulse={!!hs?.scanning} />
                <span>{hs?.scanning ? t('已就绪') : t('已暂停')}</span>
              </SoftBadge>
              <span className="whitespace-nowrap text-caption text-ink-600 tnum">{hs?.groupCount ?? 0} {t('个热点')}</span>
            </div>
          )}
          {hs && <p className="mt-1 text-micro text-ink-400 tnum">{t('更新')} {fmtRelative(hs.updatedAt)}</p>}
        </HeroCell>

        <HeroCell label={t("分析服务")}>
          {loading ? (
            <SkeletonBlock className="h-5 w-28 max-w-full" />
          ) : statusUnread ? (
            unreadCell
          ) : (
            <SoftBadge
              tone={s?.analysisAvailable ? 'ai' : reason?.tone === 'muted' ? 'neutral' : reason?.tone ?? 'down'}
              size="md"
              className="whitespace-normal"
            >
              <AnalysisIcon size={14} />
              <span>
                {s?.analysisAvailable ? t('模型分析可用') : reason ? reason.label : t('模型分析不可用')}
              </span>
            </SoftBadge>
          )}
          {s && (
            <p className="mt-1 break-words text-micro text-ink-400 tnum">
              {s.analysisModel?.trim() ? (
                <>
                  {t('模型')} {aiModelLabel(s.analysisModel, s.analysisReasoning)?.replaceAll(' · ', '\u00a0·\u00a0')}
                </>
              ) : s.queueDepth != null ? (
                <>
                  {t('队列')} {s.queueDepth} {t('· 已分析')} <span className="text-ink-600">{s.analyzedToday ?? 0}</span>
                </>
              ) : null}
            </p>
          )}
          {budgetText && <div className="mt-1 min-w-0 break-words text-micro leading-5 text-ink-400">
            <p className="tnum">{budgetText.summary}</p>
            <p>{budgetText.note}</p>
          </div>}
        </HeroCell>

        <HeroCell label={t("近 24 小时新闻")}>
          {newsQ.loading && !newsQ.data ? (
            <SkeletonBlock className="h-7 w-16" />
          ) : (
            <p className="text-data-l text-ink-900 tnum">
              {newsQ.data ? `${newsQ.data.count}${newsQ.data.saturated ? '+' : ''}` : '—'}
              <span className="ml-1.5 text-micro font-normal text-ink-400">{t('条')}</span>
            </p>
          )}
          {newsQ.data && (
            <p className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-micro text-ink-400 tnum">
              <span className="whitespace-nowrap">{t('已分析')} <span className="text-ink-600">{newsQ.data.analyzed}</span> {t('条')}</span>
              {newsQ.data.pending > 0 && (
                <span className="whitespace-nowrap">
                  {t('待中文')} <span className="text-ink-600">{newsQ.data.pending}</span>
                </span>
              )}
            </p>
          )}
        </HeroCell>
      </div>
    </motion.section>
  );
}
