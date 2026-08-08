/**
 * §01 首页（/）
 * 指数带（列表由后端决定：live 为美股三指+日经+上证共 5 个，mock 6 个——
 * 列数 auto-fit 不写死） · 市场状态 · 雷达信号 · 财报临近 · 关注池异动 · CTA 联动带
 * 轮询：指数/状态 60s，其余 300s（visibility 暂停由 usePolling 负责）
 *
 * 数据纪律（与 components/market/IndexCards.tsx 同款）：
 * - 无有效价显「—」，不显 0.00；sparkline 仅 mock 有数据，live 无指数 K 线端点如实留空
 * - 强度聚合 aggregateAvailable !== true 时隐藏对应行，不显 0
 */
import { useMemo, type ReactNode } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { isMock, type ApiError } from '@/api/client';
import type { MarketSession } from '@/api/types';
import { marketApi } from '@/api/modules/market';
import { signalsApi } from '@/api/modules/signals';
import { strengthApi } from '@/api/modules/strength';
import { breakoutsApi } from '@/api/modules/breakouts';
import { earningsApi } from '@/api/modules/earnings';
import { stocksApi } from '@/api/modules/stocks';
import { marketPulseApi } from '@/components/market/api';
import { regimeMean } from '@/components/market/RegimePanel';
import { getIndexIntraday } from '@/mocks/marketPulse';
import { usePolling } from '@/hooks/usePolling';
import { useNow } from '@/hooks/useNow';
import { cn } from '@/lib/utils';
import { DUR_SECTION, EASE_PAPER } from '@/lib/motion';
import { fmtCountdown, fmtNyTime, fmtPrice, fmtRelative, fmtTimeHHMMSS } from '@/lib/format';
import { fmtMDCN } from '@/components/earnings/types';
import { signed } from '@/components/cta/ctaMeta';
import PageHeader from '@/components/shared/PageHeader';
import SessionLED from '@/components/shared/SessionLED';
import ChangeBadge from '@/components/shared/ChangeBadge';
import TickerLogo from '@/components/shared/TickerLogo';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonCard, SkeletonRows } from '@/components/shared/Skeleton';
import Sparkline from '@/components/charts/Sparkline';
import { t } from '../i18n/core.ts';

const MARKET_TO_SESSION: Record<string, MarketSession> = {
  open: 'regular',
  premarket: 'premarket',
  postmarket: 'afterhours',
  closed: 'closed',
};

const SESSION_LABEL: Record<string, string> = {
  open: t('盘中'),
  premarket: t('盘前'),
  postmarket: t('盘后'),
  closed: t('休市'),
};

function RetryButton({ onClick, refreshing }: { onClick: () => void; refreshing: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={refreshing}
      className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105 disabled:opacity-60"
    >
      {refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
      {t('重试')}
    </button>
  );
}

/** 区块卡统一外壳：text-h3 标题 + 右侧 brand-700「查看全部」 */
function SectionCard({
  title,
  to,
  children,
  className,
}: {
  title: string;
  to: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('card-surface', className)} aria-label={title}>
      <div className="flex items-center justify-between gap-3 px-4 pb-1 pt-4 md:px-5 md:pt-5">
        <h3 className="text-h3 text-ink-900">{title}</h3>
        <Link
          to={to}
          className="shrink-0 text-caption font-medium text-brand-700 transition-colors duration-fast hover:text-brand-600"
        >
          {t('查看全部')}
        </Link>
      </div>
      {children}
    </section>
  );
}

/** 列表卡三态统一：加载 SkeletonRows / 错误 EmptyState+重试 / 空态简洁文案 */
function ListBody({
  loading,
  error,
  refreshing,
  onRetry,
  isEmpty,
  emptyTitle,
  rows = 6,
  children,
}: {
  loading: boolean;
  error: ApiError | null;
  refreshing: boolean;
  onRetry: () => void;
  isEmpty: boolean;
  emptyTitle: string;
  rows?: number;
  children: ReactNode;
}) {
  if (loading) {
    return (
      <div className="pb-2">
        <SkeletonRows rows={rows} />
      </div>
    );
  }
  /* 陈旧数据纪律：后续轮询失败时 usePolling 保留上一份成功数据——只有
     「确实没有旧数据」才整块换错误卡；有旧数据就继续显示 + 陈旧条
     （GPT-5.6-Pro 审计首页问题 4）。 */
  if (error && isEmpty) {
    return (
      <EmptyState
        variant="error"
        title={error.code === 503 ? t('数据暂不可用') : t('加载失败')}
        description={error.message}
        action={<RetryButton onClick={onRetry} refreshing={refreshing} />}
      />
    );
  }
  if (isEmpty) return <EmptyState title={emptyTitle} />;
  return (
    <>
      {error && <StaleStrip onRetry={onRetry} refreshing={refreshing} />}
      {children}
    </>
  );
}

/** 刷新失败但仍有旧数据：小黄条明示陈旧 + 重试，不清空内容。 */
function StaleStrip({ onRetry, refreshing }: { onRetry: () => void; refreshing: boolean }) {
  return (
    <p className="mx-4 mb-1 mt-2 flex items-center justify-between gap-2 rounded-sm bg-warn-50 px-2.5 py-1.5 text-micro text-warn-700 md:mx-5">
      {t('刷新失败，显示上次成功的结果')}
      <button
        onClick={onRetry}
        disabled={refreshing}
        className="shrink-0 font-medium underline-offset-2 hover:underline disabled:opacity-60"
      >
        {t('重试')}
      </button>
    </p>
  );
}

/** 统计小砖：居中大数字 + micro 标签（涨绿/跌红/平灰；无数据显 —） */
function MiniStat({ label, value, tone }: { label: string; value: number | null; tone: 'up' | 'down' | 'flat' }) {
  return (
    <div className="rounded-md bg-paper-2 py-2 text-center">
      <p
        className={cn(
          'font-mono text-data-l tnum',
          value === null
            ? 'text-ink-400'
            : tone === 'up'
              ? 'text-up-700'
              : tone === 'down'
                ? 'text-down-700'
                : 'text-ink-500',
        )}
      >
        {value === null ? '—' : value}
      </p>
      <p className="mt-0.5 text-micro text-ink-400">{label}</p>
    </div>
  );
}

export default function Home() {
  /* 60s：指数 + 市场状态 */
  const indicesQ = usePolling(() => marketApi.indices(), 60_000);
  const statusQ = usePolling(() => marketPulseApi.statusDetail(), 60_000);
  /* 300s：形态六维 / 信号 / 强度 / 雷达 / 财报 / 自选 / CTA */
  const regimeQ = usePolling(() => marketPulseApi.regime(), 300_000);
  const signalsQ = usePolling(() => signalsApi.market(), 300_000);
  const strengthQ = usePolling(() => strengthApi.market(), 300_000);
  const breakoutsQ = usePolling(() => breakoutsApi.current(), 300_000);
  const earningsQ = usePolling(() => earningsApi.upcoming(), 300_000);
  const watchlistQ = usePolling(() => stocksApi.watchlist(), 300_000);
  const ctaQ = usePolling(() => marketApi.ctaTrend(), 300_000);

  const status = statusQ.data;
  /* 时段读不到时显示「时段未知」，不落回「休市」（与大盘页同一纪律） */
  const session: MarketSession | null =
    status?.market ? MARKET_TO_SESSION[status.market] ?? null : null;

  /* 趋势偏向只依据六维 market_regime；接口没有读数时不做近似替代 */
  const mean = useMemo(() => (regimeQ.data ? regimeMean(regimeQ.data) : null), [regimeQ.data]);
  const bias = mean === null ? null : mean >= 60 ? t('偏多') : mean <= 40 ? t('偏空') : t('中性');

  /* 雷达信号：最新 8 条 */
  const breakouts = useMemo(() => (breakoutsQ.data ?? []).slice(0, 8), [breakoutsQ.data]);

  /* 财报临近：只取今天（纽约日历）及以后，按日期升序前 6 条。接口从
     days_until=-3 起步，不过滤会让三天前已公布的小公司霸占「临近」榜
     （GPT-5.6-Pro 审计首页问题 2）。 */
  const nyToday = useMemo(
    () =>
      new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
      }).format(new Date()),
    [],
  );
  const earnings = useMemo(
    () =>
      (earningsQ.data?.items ?? [])
        .filter((it) => it.date >= nyToday)
        .slice()
        .sort((a, b) => a.date.localeCompare(b.date))
        .slice(0, 6),
    [earningsQ.data, nyToday],
  );

  /* 关注池异动：stocksApi.watchlist() 返回站点公共关注池（非登录账号的个
     人自选——个人过滤在自选页做），按 |changePct| 降序前 6；缺失的行排最后 */
  const movers = useMemo(() => {
    const mag = (v: number | null | undefined) =>
      typeof v === 'number' && Number.isFinite(v) ? Math.abs(v) : -1;
    return (watchlistQ.data ?? []).slice().sort((a, b) => mag(b.changePct) - mag(a.changePct)).slice(0, 6);
  }, [watchlistQ.data]);

  /* 涨跌平家数（自选股池统计；缺涨跌幅的行不进入任何一桶） */
  const breadth = useMemo(() => {
    const pool = watchlistQ.data;
    if (!pool) return { adv: null, dec: null, flat: null, total: null };
    let adv = 0;
    let dec = 0;
    let flat = 0;
    for (const item of pool) {
      if (typeof item.changePct !== 'number' || !Number.isFinite(item.changePct)) continue;
      if (item.changePct > 0) adv += 1;
      else if (item.changePct < 0) dec += 1;
      else flat += 1;
    }
    return { adv, dec, flat, total: pool.length };
  }, [watchlistQ.data]);

  const ctaInstruments = ctaQ.data?.instruments ?? [];

  return (
    <div>
      {/* 页头带 */}
      <PageHeader
        section="01"
        eyebrow="OPTIX PRO · DELAYED 15MIN"
        title={t('首页')}
        description={t('指数、信号与自选的全景。')}
        meta={
          <>
            <SessionLED
              session={session}
              label={session ? SESSION_LABEL[status!.market] : undefined}
              loading={statusQ.loading}
            />
            {indicesQ.lastUpdatedAt && (
              <span className="font-mono text-caption text-ink-400 tnum">
                {t('更新')} {fmtTimeHHMMSS(indicesQ.lastUpdatedAt)}
              </span>
            )}
          </>
        }
      />

      {/* 指数带（SPX/NDX/DJI/RUT/SOX/VIX，点击进 /market?index= 高亮定位） */}
      <section className="mt-8" aria-label={t('指数概览')}>
        {indicesQ.loading ? (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:[grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]">
            {Array.from({ length: 5 }, (_, i) => (
              <SkeletonCard key={i} className="h-24" />
            ))}
          </div>
        ) : indicesQ.error && !indicesQ.data?.length ? (
          <div className="card-surface">
            <EmptyState
              variant="error"
              title={indicesQ.error.code === 503 ? t('数据暂不可用') : t('加载失败')}
              description={indicesQ.error.code === 503 ? t('暂无指数数据') : indicesQ.error.message}
              action={<RetryButton onClick={indicesQ.refresh} refreshing={indicesQ.refreshing} />}
            />
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:[grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]">
            {(indicesQ.data ?? []).map((q, i) => {
              /* 数据纪律：无有效价（live 快照缺失）显「—」，不显 0.00 */
              const hasPrice = Number.isFinite(q.price) && q.price > 0;
              /* sparkline mock-only：live 无指数 K 线端点，如实留空 */
              const spark = isMock ? getIndexIntraday(q.code, q.changePct) : null;
              return (
                <motion.div
                  key={q.code}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: DUR_SECTION, ease: EASE_PAPER, delay: Math.min(i * 0.045, 0.4) }}
                >
                  <Link
                    to={`/market?index=${q.code}`}
                    className="card-surface card-hover flex flex-col gap-1 rounded-lg p-3"
                    aria-label={t('{name} {code} 详情', { name: q.name, code: q.code })}
                  >
                    <span className="truncate text-caption text-ink-500">{q.name}</span>
                    <span className="font-mono text-data-l text-ink-900 tnum">
                      {hasPrice ? fmtPrice(q.price) : '—'}
                    </span>
                    <span className="flex items-end justify-between gap-2">
                      <ChangeBadge value={q.changePct} size="sm" />
                      {spark && <Sparkline data={spark} width={64} height={20} change={q.changePct} />}
                    </span>
                  </Link>
                </motion.div>
              );
            })}
          </div>
        )}
      </section>

      {/* 行2：市场状态 + 雷达信号 */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <MarketStatusPanel
          status={status}
          session={session}
          loading={statusQ.loading}
          error={statusQ.error}
          refreshing={statusQ.refreshing}
          onRetry={statusQ.refresh}
          mean={mean}
          bias={bias}
          breadth={breadth}
          strength={strengthQ.data}
          signalMetrics={signalsQ.data?.metrics ?? null}
        />

        <SectionCard title={t('雷达信号')} to="/breakouts" className="lg:col-span-2">
          <ListBody
            loading={breakoutsQ.loading}
            error={breakoutsQ.error}
            refreshing={breakoutsQ.refreshing}
            onRetry={breakoutsQ.refresh}
            isEmpty={breakouts.length === 0}
            emptyTitle={t('雷达仍在盯')}
            rows={8}
          >
            <div className="divide-y divide-line">
              {breakouts.map((s) => (
                <div key={s.id} className="flex items-center gap-3 px-4 py-2.5 md:px-5">
                  <TickerLogo ticker={s.ticker} size={28} />
                  <p className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-caption font-semibold text-ink-800">{s.ticker}</span>
                    <span className="ml-2 text-caption text-ink-500">{s.name}</span>
                  </p>
                  <span className="hidden shrink-0 text-caption text-ink-600 sm:block">{s.label}</span>
                  <span className="shrink-0 text-micro text-ink-400">{fmtRelative(s.at)}</span>
                  <span className="w-10 shrink-0 text-right font-mono text-caption text-ink-800 tnum">
                    {s.strengthScore}
                  </span>
                </div>
              ))}
            </div>
          </ListBody>
        </SectionCard>
      </div>

      {/* 行3：财报临近 + 自选异动 */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <SectionCard title={t('财报临近')} to="/earnings">
          <ListBody
            loading={earningsQ.loading}
            error={earningsQ.error}
            refreshing={earningsQ.refreshing}
            onRetry={earningsQ.refresh}
            isEmpty={earnings.length === 0}
            emptyTitle={t('近一个月暂无财报')}
          >
            <div className="divide-y divide-line">
              {earnings.map((it) => (
                <div key={`${it.ticker}-${it.date}`} className="flex items-center gap-3 px-4 py-2.5 md:px-5">
                  <TickerLogo ticker={it.ticker} size={28} />
                  <p className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-caption font-semibold text-ink-800">{it.ticker}</span>
                    <span className="ml-2 text-caption text-ink-500">{it.name}</span>
                  </p>
                  <span className="flex shrink-0 flex-col items-end gap-1">
                    <span className="flex items-center gap-1.5">
                      <span className="text-caption text-ink-500">{fmtMDCN(it.date)}</span>
                      <span className="rounded-xs bg-paper-2 px-1.5 py-0.5 text-micro text-ink-600">
                        {it.timing === 'bmo' ? t('盘前') : it.timing === 'amc' ? t('盘后') : t('时间待定')}
                      </span>
                    </span>
                    {it.epsEstimate !== null && (
                      <span className="font-mono text-micro text-ink-400 tnum">
                        {t('EPS 预期 {v}', { v: it.epsEstimate.toFixed(2) })}
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </ListBody>
        </SectionCard>

        <SectionCard title={t('关注池异动')} to="/watchlist">
          <ListBody
            loading={watchlistQ.loading}
            error={watchlistQ.error}
            refreshing={watchlistQ.refreshing}
            onRetry={watchlistQ.refresh}
            isEmpty={movers.length === 0}
            emptyTitle={t('暂无自选')}
          >
            <div className="divide-y divide-line">
              {movers.map((item) => (
                <div key={item.ticker} className="flex items-center gap-3 px-4 py-2.5 md:px-5">
                  <TickerLogo ticker={item.ticker} size={28} />
                  <p className="min-w-0 flex-1 truncate">
                    <span className="font-mono text-caption font-semibold text-ink-800">{item.ticker}</span>
                    <span className="ml-2 text-caption text-ink-500">{item.name}</span>
                  </p>
                  <span className="shrink-0 font-mono text-caption text-ink-800 tnum">
                    {Number.isFinite(item.price) && item.price > 0 ? fmtPrice(item.price) : '—'}
                  </span>
                  <ChangeBadge value={item.changePct} size="sm" />
                </div>
              ))}
            </div>
          </ListBody>
        </SectionCard>
      </div>

      {/* 行4：CTA 趋势资金联动带。区块常驻：加载给骨架、失败给错误行、
          快照未发布给说明——整块消失会让「本来没有」与「没读到」不可分辨，
          还引发布局跳动（GPT-5.6-Pro 审计首页问题 5）。 */}
      <section className="mt-8" aria-label={t('CTA 趋势资金')}>
        <div className="card-surface p-4 md:p-5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-h3 text-ink-900">{t('CTA 趋势资金')}</h3>
            <Link
              to="/cta"
              className="shrink-0 text-caption font-medium text-brand-700 transition-colors duration-fast hover:text-brand-600"
            >
              {t('查看全部')}
            </Link>
          </div>
          {ctaQ.loading && !ctaQ.data ? (
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
              {Array.from({ length: 4 }, (_, i) => (
                <SkeletonCard key={i} className="h-20" />
              ))}
            </div>
          ) : ctaQ.error && !ctaQ.data ? (
            <p className="mt-3 flex items-center justify-between gap-2 rounded-md bg-paper-2 px-3 py-2.5 text-caption text-ink-500">
              {ctaQ.error.bizCode === 'public_snapshot_unavailable'
                ? t('CTA 估算快照尚未发布：Worker 完成首次计算后自动出现，无需手动操作')
                : t('CTA 估算读取失败')}
              <button
                onClick={() => ctaQ.refresh()}
                disabled={ctaQ.refreshing}
                className="shrink-0 font-medium text-brand-700 hover:text-brand-600 disabled:opacity-60"
              >
                {t('重试')}
              </button>
            </p>
          ) : ctaInstruments.length === 0 ? (
            <p className="mt-3 rounded-md bg-paper-2 px-3 py-2.5 text-caption text-ink-500">{t('暂无数据')}</p>
          ) : (
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
              {ctaInstruments.slice(0, 4).map((ins) => (
                <div key={ins.instrument} className="rounded-md bg-paper-2 px-3 py-2">
                  <p className="truncate text-caption text-ink-500">{ins.label}</p>
                  <p
                    className={cn(
                      'mt-0.5 font-mono text-data-m tnum',
                      ins.position_score === null
                        ? 'text-ink-400'
                        : ins.position_score > 0
                          ? 'text-up-700'
                          : ins.position_score < 0
                            ? 'text-down-700'
                            : 'text-ink-500',
                    )}
                  >
                    {signed(ins.position_score)}
                  </p>
                  <p className="mt-0.5 font-mono text-micro text-ink-400 tnum">{signed(ins.flow_score)}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

/** 「市场状态」卡：时段/纽约时间/倒计时/六维均值/涨跌平小砖/强度与信号 micro 行 */
function MarketStatusPanel({
  status,
  session,
  loading,
  error,
  refreshing,
  onRetry,
  mean,
  bias,
  breadth,
  strength,
  signalMetrics,
}: {
  status: import('@/components/market/api').MarketStatusDetail | null;
  session: MarketSession | null;
  loading: boolean;
  error: ApiError | null;
  refreshing: boolean;
  onRetry: () => void;
  mean: number | null;
  bias: string | null;
  breadth: { adv: number | null; dec: number | null; flat: number | null; total: number | null };
  strength: { aggregateAvailable?: boolean; avgScore: number; ge85Count: number } | null;
  signalMetrics: { label: string; value: number }[] | null;
}) {
  const now = useNow(1000);

  if (loading) return <SkeletonCard className="h-full" />;
  if (error) {
    return (
      <div className="card-surface h-full">
        <EmptyState
          variant="error"
          title={error.code === 503 ? t('数据暂不可用') : t('加载失败')}
          description={error.code === 503 ? t('暂无市场状态数据') : error.message}
          action={<RetryButton onClick={onRetry} refreshing={refreshing} />}
        />
      </div>
    );
  }

  return (
    <section className="card-surface flex h-full flex-col p-4 md:p-5" aria-label={t('市场状态')}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-h3 text-ink-900">{t('市场状态')}</h3>
        <SessionLED
          session={session}
          label={session && status ? SESSION_LABEL[status.market] : undefined}
          loading={loading}
        />
      </div>

      <div className="mt-3">
        <p className="font-mono text-data-l text-ink-900 tnum" suppressHydrationWarning>
          {fmtNyTime(new Date(now))}
        </p>
        <p className="mt-0.5 text-micro text-ink-400">{t('纽约时间')}</p>
      </div>

      <div className="mt-3">
        <div className="flex items-center justify-between border-t border-line py-2">
          <span className="text-caption text-ink-500">{t('距下一开盘')}</span>
          <span className="font-mono text-data-m text-brand-600 tnum" suppressHydrationWarning>
            {status?.next_open ? fmtCountdown(status.next_open, now) : '—'}
          </span>
        </div>
        <div className="flex items-center justify-between border-t border-line py-2">
          <span className="text-caption text-ink-500">{t('距下一收盘')}</span>
          <span className="font-mono text-data-m text-brand-600 tnum" suppressHydrationWarning>
            {status?.next_close ? fmtCountdown(status.next_close, now) : '—'}
          </span>
        </div>
        <div className="flex items-center justify-between border-y border-line py-2">
          <span className="text-caption text-ink-500">{t('六维形态均值')}</span>
          <span className="flex items-baseline gap-2">
            <span className="font-mono text-data-m text-ink-900 tnum">
              {mean === null ? '—' : mean.toFixed(1)}
            </span>
            {bias && <span className="text-caption text-ink-500">{bias}</span>}
          </span>
        </div>
      </div>

      <p className="mt-3 text-micro text-ink-400">
        {t('扫描池')}
        {breadth.total !== null && <span className="font-mono tnum"> · {breadth.total}</span>}
      </p>
      <div className="mt-1.5 grid grid-cols-3 gap-2">
        <MiniStat label={t('上涨')} value={breadth.adv} tone="up" />
        <MiniStat label={t('下跌')} value={breadth.dec} tone="down" />
        <MiniStat label={t('平盘')} value={breadth.flat} tone="flat" />
      </div>

      {/* honesty：接口明确给出全市场聚合才显示，不显 0 */}
      {strength?.aggregateAvailable === true && (
        <p className="mt-3 text-micro text-ink-400">
          {t('平均强度 {avg} · ≥85 {n} 只', { avg: strength.avgScore.toFixed(1), n: strength.ge85Count })}
        </p>
      )}
      {signalMetrics && signalMetrics.length > 0 && (
        <p className="mt-1.5 text-micro text-ink-400">
          {signalMetrics.slice(0, 4).map((m) => `${m.label} ${m.value}`).join(' · ')}
        </p>
      )}
    </section>
  );
}
