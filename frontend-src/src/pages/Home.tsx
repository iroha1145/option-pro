import { useQuoteSymbols } from '@/hooks/useLiveQuote';
import { LivePrice, LiveChange } from '@/components/shared/LiveQuote';
/**
 * §01 首页（/）
 * 指数带（列表由后端决定：live 为美股三指+日经+上证共 5 个，mock 6 个——
 * 列数 auto-fit 不写死） · 市场状态 · 雷达信号（双列卡片格） · 财报临近
 * （日期锚块行） · 关注池异动（迷你卡格） · CTA 联动带
 * 轮询：指数/状态 60s，其余 300s（visibility 暂停由 usePolling 负责）
 *
 * 数据纪律（与 components/market/IndexCards.tsx 同款）：
 * - 无有效价显「—」，不显 0.00；sparkline 仅 mock 有数据，live 无指数 K 线端点如实留空
 * - 强度聚合 aggregateAvailable !== true 时隐藏对应行，不显 0
 */
import { useEffect, useMemo, type ReactNode } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { isMock, type ApiError } from '@/api/client';
import type { BreakoutSignal, EarningsItem, MarketSession, WatchlistItem } from '@/api/types';
import { marketApi } from '@/api/modules/market';
import { signalsApi } from '@/api/modules/signals';
import { strengthApi } from '@/api/modules/strength';
import { breakoutsApi } from '@/api/modules/breakouts';
import { earningsApi } from '@/api/modules/earnings';
import { stocksApi } from '@/api/modules/stocks';
import { marketPulseApi } from '@/components/market/api';
import { regimeMean } from '@/lib/regime';
import { getIndexIntraday } from '@/mocks/marketPulse';
import { usePolling } from '@/hooks/usePolling';
import { useStockDataStatus } from '@/hooks/useStockDataStatus';
import type { StockDataStatus } from '@/lib/stockDataStatus';
import { quoteSymbol } from '@/lib/quoteSymbol';
import { useNow } from '@/hooks/useNow';
import { cn } from '@/lib/utils';
import { DUR_SECTION, EASE_PAPER } from '@/lib/motion';
import { fmtCountdown, fmtNyTime, fmtPrice, fmtRelative, fmtTimeHHMMSS } from '@/lib/format';
import { instrumentName, signed } from '@/components/cta/ctaMeta';
import PageHeader from '@/components/shared/PageHeader';
import StaleStrip from '@/components/shared/StaleStrip';
import StockDataCoverage from '@/components/shared/StockDataCoverage';
import SessionLED from '@/components/shared/SessionLED';
import StrengthBar from '@/components/shared/StrengthBar';
import { exNum, isFeaturedRow, type EarningsRow } from '@/components/earnings/types';
import ChangeBadge from '@/components/shared/ChangeBadge';
import TickerLogo from '@/components/shared/TickerLogo';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonBlock, SkeletonCard, SkeletonRows } from '@/components/shared/Skeleton';
import Sparkline from '@/components/charts/Sparkline';
import Icon from '@/components/icons';
import { localeTag, t } from '../i18n/core.ts';

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

/* 财报日期块的月份缩写随界面语言走（en Aug / zh 8月 / ja 8月）；
   语言在页面加载期定型，模块级 formatter 与 t() 同口径 */
const MONTH_SHORT_FMT = new Intl.DateTimeFormat(localeTag(), { month: 'short' });

/** 'YYYY-MM-DD' → { 日号, 月份缩写 }；字符串解析避免 UTC 午夜跨时区串日 */
function dateAnchorParts(iso: string): { day: number; monthShort: string } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (!month || !day) return null;
  return { day, monthShort: MONTH_SHORT_FMT.format(new Date(Number(m[1]), month - 1, 1)) };
}

/** 卡片格入场 stagger 档位：i*0.04 封顶 0.3（雷达/自选两区同一节奏） */
function staggerDelay(i: number): number {
  return Math.min(i * 0.04, 0.3);
}

function RetryButton({ onClick, refreshing }: { onClick: () => void; refreshing: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={refreshing}
      className="btn-primary"
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
          className="link-learn shrink-0 text-caption font-medium text-brand-700 transition-colors duration-fast hover:text-brand-600"
        >
          {t('查看全部')}
          <span className="link-learn-chevron" aria-hidden="true">
            <Icon name="chevron-right" size={12} />
          </span>
        </Link>
      </div>
      {children}
    </section>
  );
}

/** 列表卡三态统一：加载骨架（默认 SkeletonRows，可传 skeleton 换卡片格）/
 *  错误 EmptyState+重试 / 空态简洁文案 */
function ListBody({
  loading,
  error,
  refreshing,
  onRetry,
  isEmpty,
  emptyTitle,
  rows = 6,
  skeleton,
  children,
}: {
  loading: boolean;
  error: ApiError | null;
  refreshing: boolean;
  onRetry: () => void;
  isEmpty: boolean;
  emptyTitle: string;
  rows?: number;
  skeleton?: ReactNode;
  children: ReactNode;
}) {
  if (loading) {
    if (skeleton) return <>{skeleton}</>;
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
      {error && <StaleStrip onRetry={onRetry} refreshing={refreshing} className="mx-4 mb-1 mt-2 md:mx-5" />}
      {children}
    </>
  );
}

/** 统计小砖：居中大数字 + micro 标签（涨绿/跌红/平灰；无数据显 —） */
function MiniStat({ label, value, tone }: { label: string; value: number | null; tone: 'up' | 'down' | 'flat' }) {
  return (
    <div className="rounded-[9px] bg-paper-2/70 py-2.5 text-center">
      <p
        className={cn(
          'metric-value text-data-l tnum',
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
  const watchlistQ = usePolling(() => stocksApi.watchlist(true), 300_000);
  const ctaQ = usePolling(() => marketApi.ctaTrend(), 300_000);

  const status = statusQ.data;
  /* 时段读不到时显示「时段未知」，不落回「休市」（与大盘页同一纪律） */
  const session: MarketSession | null =
    status?.market ? MARKET_TO_SESSION[status.market] ?? null : null;

  /* 趋势偏向只依据六维 market_regime；接口没有读数时不做近似替代 */
  const mean = useMemo(() => (regimeQ.data ? regimeMean(regimeQ.data) : null), [regimeQ.data]);
  const bias = mean === null ? null : mean >= 60 ? t('偏多') : mean <= 40 ? t('偏空') : t('中性');

  /* 雷达信号：按 ticker 去重后取前 8（接口按时间倒序，保留每只最新一条）。
     同一只股票的多次形态事件全上首页会把摘要榜刷满重复代码（审计：ROAD ×3、
     TNDM/TEAM ×2）；完整事件流留在雷达页。 */
  const breakouts = useMemo(() => {
    const seen = new Set<string>();
    const rows: BreakoutSignal[] = [];
    for (const s of breakoutsQ.data ?? []) {
      if (seen.has(s.ticker)) continue;
      seen.add(s.ticker);
      rows.push(s);
      if (rows.length === 8) break;
    }
    return rows;
  }, [breakoutsQ.data]);

  /* 财报临近：只取今天（纽约日历）及以后。纽约日随分钟时钟重算——页面跨越
     纽约午夜保持打开时过滤不得停在昨天（审计低优先级项）。 */
  const nowMinute = useNow(60_000);
  const nyToday = useMemo(
    () =>
      new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
      }).format(new Date(nowMinute)),
    [nowMinute],
  );
  /* 排序复用财报页的重点口径（publicFeatured ∪ 公共关注池）：重点公司优先 →
     日期升序 → 市值降序 → 普通公司补足。此前同日内部沿用上游顺序，数千家
     覆盖里的小公司会排在大型公司前面（审计首页问题 4）。 */
  const poolTickers = useMemo(
    () => new Set((watchlistQ.data ?? []).map((item) => item.ticker.toUpperCase())),
    [watchlistQ.data],
  );
  const earnings = useMemo(
    () =>
      (earningsQ.data?.items ?? [])
        .filter((it) => it.date >= nyToday)
        .slice()
        .sort((a, b) => {
          const ra = a as EarningsRow;
          const rb = b as EarningsRow;
          const fa = isFeaturedRow(ra, poolTickers) ? 0 : 1;
          const fb = isFeaturedRow(rb, poolTickers) ? 0 : 1;
          if (fa !== fb) return fa - fb;
          if (a.date !== b.date) return a.date.localeCompare(b.date);
          return (exNum(rb, 'marketCap') ?? -1) - (exNum(ra, 'marketCap') ?? -1);
        })
        .slice(0, 6),
    [earningsQ.data, nyToday, poolTickers],
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
  const readiness = useStockDataStatus([
    ...(watchlistQ.data ?? []).map((item) => item.ticker),
    ...breakouts.map((item) => item.ticker), ...earnings.map((item) => item.ticker),
    ...movers.map((item) => item.ticker), 'NVDA',
  ]);
  const { refresh: refreshWatchlist } = watchlistQ;
  useEffect(() => {
    if (!readiness.dailyVersion) return;
    stocksApi.invalidatePreparedDaily();
    refreshWatchlist({ force: true });
  }, [readiness.dailyVersion, refreshWatchlist]);

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

      <StockDataCoverage state={readiness} className="mt-4" />

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
          <>
            {/* 有旧数据时刷新失败 → 明示陈旧，不清空指数卡（审计首页问题 5 补全） */}
            {indicesQ.error && (
              <StaleStrip onRetry={indicesQ.refresh} refreshing={indicesQ.refreshing} className="mb-3" />
            )}
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
                    className="card-surface card-hover card-glare flex flex-col gap-1 rounded-lg p-3"
                    aria-label={t('{name} {code} 详情', { name: q.name, code: q.code })}
                  >
                    <span className="truncate text-caption text-ink-500">{q.name}</span>
                    <span className="metric-value text-data-l text-ink-900 tnum">
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
          </>
        )}
      </section>

      {/* 行2：市场状态 + 雷达信号 */}
      <div className="mt-8 grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
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
          /* 该卡实际拼了四个辅助接口（六维/信号/强度/关注池宽度）：任一失败
             此前只会悄悄显示「—」或保留旧值——补统一陈旧提示（审计问题 5） */
          auxError={Boolean(regimeQ.error || signalsQ.error || strengthQ.error || watchlistQ.error)}
          auxRefreshing={regimeQ.refreshing || signalsQ.refreshing || strengthQ.refreshing || watchlistQ.refreshing}
          onRetryAux={() => {
            for (const q of [regimeQ, signalsQ, strengthQ, watchlistQ]) {
              if (q.error) q.refresh();
            }
          }}
        />

        <SectionCard title={t('雷达信号')} to="/breakouts" className="lg:col-span-2">
          <ListBody
            loading={breakoutsQ.loading}
            error={breakoutsQ.error}
            refreshing={breakoutsQ.refreshing}
            onRetry={breakoutsQ.refresh}
            isEmpty={breakouts.length === 0}
            emptyTitle={t('雷达仍在盯')}
            skeleton={<SignalGridSkeleton cards={8} />}
          >
            <div className="grid grid-cols-1 gap-2.5 px-4 pb-4 pt-3 sm:grid-cols-2 md:px-5 md:pb-5 xl:grid-cols-2">
              {breakouts.map((s, i) => (
                <RadarSignalCard key={s.id} signal={s} index={i} />
              ))}
            </div>
          </ListBody>
        </SectionCard>
      </div>

      {/* 行3：财报临近 + 自选异动 */}
      <div className="mt-8 grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
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
                /* todayKey 用 nyToday：财报日历一律按纽约日，与上面的过滤同口径
                   （用户版取浏览器本地日，跨时区会提前/滞后一天高亮） */
                <EarningsAnchorRow key={`${it.ticker}-${it.date}`} item={it} todayKey={nyToday} />
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
            emptyTitle={t('暂无关注标的')}
            skeleton={<MoverGridSkeleton cards={6} />}
          >
            <div className="grid grid-cols-1 gap-2.5 px-4 pb-4 pt-3 sm:grid-cols-2 md:px-5 md:pb-5">
              {movers.map((item, i) => (
                <WatchlistMoverCard key={item.ticker} item={item} index={i} preparation={readiness.byTicker.get(quoteSymbol(item.ticker))} statusReadFailed={Boolean(readiness.error)} />
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
            <>
            {/* 有旧数据时刷新失败 → 明示陈旧（与列表卡同一纪律） */}
            {ctaQ.error && (
              <StaleStrip onRetry={() => ctaQ.refresh()} refreshing={ctaQ.refreshing} className="mt-3" />
            )}
            <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
              {ctaInstruments.slice(0, 4).map((ins) => (
                <div key={ins.instrument} className="rounded-md bg-paper-2 px-3 py-2">
                  {/* 标的名按 instrument 键走本地词典：直渲染后端中文 label
                      在英文界面会漏翻（审计 i18n 漏点 1） */}
                  <p className="truncate text-caption text-ink-500">{instrumentName(ins.instrument, ins.label)}</p>
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
            </>
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
  auxError,
  auxRefreshing,
  onRetryAux,
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
  auxError: boolean;
  auxRefreshing: boolean;
  onRetryAux: () => void;
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

      {/* 辅助指标按需展开；缺失读数仍遵守原有数据纪律，不补零。 */}
      {(strength?.aggregateAvailable === true || (signalMetrics && signalMetrics.length > 0)) && (
        <details className="group/readings mt-4 border-t border-line/70 pt-3" data-testid="home-supporting-metrics">
          <summary className="disclosure-trigger flex cursor-pointer list-none items-center justify-between gap-2 rounded-md py-1 text-caption font-medium text-ink-600 outline-none hover:text-ink-800 focus-visible:ring-2 focus-visible:ring-brand-400/40 [&::-webkit-details-marker]:hidden">
            <span>{t('辅助读数')}</span>
            <Icon name="chevron-down" size={13} className="text-ink-400 transition-transform group-open/readings:rotate-180" />
          </summary>
          <div className="mt-2 rounded-lg bg-paper-2/60 p-3">
            {strength?.aggregateAvailable === true && (
              <p className="text-caption text-ink-600">
                {t('平均强度 {avg} · ≥85 {n} 只', { avg: strength.avgScore.toFixed(1), n: strength.ge85Count })}
              </p>
            )}
            {signalMetrics && signalMetrics.length > 0 && (
              <div className={cn('grid grid-cols-2 gap-x-4 gap-y-2', strength?.aggregateAvailable === true && 'mt-3')}>
                {signalMetrics.slice(0, 4).map((metric) => (
                  <p key={metric.label} className="flex items-baseline justify-between gap-2 text-micro text-ink-500">
                    <span className="min-w-0 truncate">{metric.label}</span>
                    <span className="metric-value shrink-0 text-ink-700 tnum">
                      {Number.isInteger(metric.value) ? metric.value : metric.value.toFixed(2)}
                    </span>
                  </p>
                ))}
              </div>
            )}
          </div>
        </details>
      )}
      {auxError && (
        <StaleStrip
          onRetry={onRetryAux}
          refreshing={auxRefreshing}
          label={t('部分读数刷新失败，显示上次成功的结果')}
          className="mt-3"
        />
      )}
    </section>
  );
}

/** 雷达信号卡：TickerLogo+类型胶囊+相对时间 / 名称+价+涨跌 / 强度条。
 *  不加 aria-label：整行可见内容自然组成可访问名（审计）。 */
function RadarSignalCard({ signal: s, index: i }: { signal: BreakoutSignal; index: number }) {
  useQuoteSymbols([s.ticker]);
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR_SECTION, ease: EASE_PAPER, delay: staggerDelay(i) }}
    >
      <Link to={`/stock/${encodeURIComponent(s.ticker)}`} className="card-surface card-hover block rounded-lg p-3">
        <div className="flex items-center gap-2">
          <TickerLogo ticker={s.ticker} size={24} />
          <span className="shrink-0 font-mono text-caption font-semibold text-ink-800">{s.ticker}</span>
          <span className="min-w-0 truncate rounded-md bg-paper-2 px-2 py-1 text-micro font-medium text-ink-600">
            {s.label}
          </span>
          <span className="ml-auto shrink-0 text-micro text-ink-400">{fmtRelative(s.at)}</span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-caption text-ink-500">{s.name}</span>
          <span className="shrink-0 font-mono text-caption text-ink-800 tnum">
            <LivePrice symbol={s.ticker} fallback={s.price} />
          </span>
          <LiveChange symbol={s.ticker} fallback={s.changePct} size="sm" className="shrink-0" />
        </div>
        <div className="mt-2">
          <StrengthBar score={s.strengthScore} width={56} showScore />
        </div>
      </Link>
    </motion.div>
  );
}

/** 财报日期锚定行：w-11 日期块（纽约日=今天时高亮）+ 代码/名称 + timing chip + EPS 预期 */
function EarningsAnchorRow({ item: it, todayKey }: { item: EarningsItem; todayKey: string }) {
  const anchor = dateAnchorParts(it.date);
  const isToday = it.date.slice(0, 10) === todayKey;
  const eps = typeof it.epsEstimate === 'number' && Number.isFinite(it.epsEstimate) ? it.epsEstimate : null;
  return (
    <Link
      to={`/stock/${encodeURIComponent(it.ticker)}`}
      className="flex items-center gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-paper-2/70 focus-visible:bg-paper-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-400/60 md:px-5"
    >
      <span
        className={cn(
          'w-11 shrink-0 rounded-[9px] py-1.5 text-center',
          isToday ? 'bg-brand-50' : 'bg-paper-2/80',
        )}
      >
        <span
          className={cn(
            'block font-mono text-body-s font-semibold tnum',
            isToday ? 'text-brand-700' : 'text-ink-900',
          )}
        >
          {anchor ? anchor.day : '—'}
        </span>
        <span className="block text-micro text-ink-400">{anchor?.monthShort ?? ''}</span>
      </span>
      <TickerLogo ticker={it.ticker} size={28} />
      <p className="min-w-0 flex-1 truncate">
        <span className="font-mono text-caption font-semibold text-ink-800">{it.ticker}</span>
        <span className="ml-2 text-caption text-ink-500">{it.name}</span>
      </p>
      <span className="flex shrink-0 items-center gap-1.5">
        {eps !== null && (
          <span className="hidden font-mono text-micro text-ink-500 tnum sm:block">
            {t('EPS 预期 {v}', { v: eps.toFixed(2) })}
          </span>
        )}
        <span className="rounded-md bg-paper-2 px-2 py-1 text-micro text-ink-600">
          {it.timing === 'bmo' ? t('盘前') : it.timing === 'amc' ? t('盘后') : t('时间待定')}
        </span>
      </span>
    </Link>
  );
}

/** 自选异动：当日涨跌与最长 30 交易日日线分开标明，长期图仅取真实日线。 */
function WatchlistMoverCard({ item, index: i, preparation, statusReadFailed }: { item: WatchlistItem; index: number; preparation?: StockDataStatus; statusReadFailed: boolean }) {
  useQuoteSymbols([item.ticker]);
  const trend = item.dailyTrend && item.dailyTrend.length > 1 ? item.dailyTrend : null;
  const spark = trend?.map((point) => point.close) ?? null;
  const periodChange = spark ? (spark[spark.length - 1] / spark[0] - 1) * 100 : null;
  const signalLabel = item.signals?.[0]?.label ?? null;
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR_SECTION, ease: EASE_PAPER, delay: staggerDelay(i) }}
    >
      <Link to={`/stock/${encodeURIComponent(item.ticker)}`} className="card-surface card-hover block overflow-hidden rounded-lg p-4" data-testid="watchlist-mover-card">
        <div className="flex items-center gap-2">
          <TickerLogo ticker={item.ticker} size={24} />
          <span className="shrink-0 font-mono text-caption font-semibold text-ink-800">{item.ticker}</span>
          <span className="min-w-0 flex-1 truncate text-caption text-ink-500">{item.name}</span>
          <span className="flex shrink-0 items-center gap-1.5">
            <span className="text-micro text-ink-400">{t('当日')}</span>
            <LiveChange symbol={item.ticker} fallback={item.changePct} fallbackAt={item.updatedAt} size="sm" />
          </span>
        </div>
        <div className="mt-2 flex items-end justify-between gap-2">
          <span className="metric-value text-data-l text-ink-900 tnum">
            <LivePrice symbol={item.ticker} fallback={item.price} fallbackAt={item.updatedAt} />
          </span>
          <span className="text-micro text-ink-400">{trend ? t('近 {count} 个交易日', { count: trend.length }) : t('日线走势')}</span>
        </div>
        {spark && trend && periodChange !== null ? (
          <figure className="mt-3" data-testid="watchlist-daily-trend" aria-label={t('{ticker} 日线走势，{start} 至 {end}，区间涨跌 {change}%', { ticker: item.ticker, start: trend[0].date, end: trend[trend.length - 1].date, change: periodChange.toFixed(2) })}>
            <Sparkline data={spark} width={320} height={88} change={periodChange} variant="area" stretch className="h-[88px] w-full" />
            <figcaption className="mt-1 flex items-center justify-between gap-2 font-mono text-micro text-ink-400 tnum">
              <span>{trend[0].date.slice(5)} — {trend[trend.length - 1].date.slice(5)}</span>
              <span className="flex items-center gap-1.5"><span className="font-sans">{t('区间')}</span><ChangeBadge value={periodChange} size="sm" /></span>
            </figcaption>
          </figure>
        ) : (
          <div className="mt-3 flex h-[112px] items-center justify-center rounded-sm bg-paper-2 px-4 text-center text-caption text-ink-400">
            {statusReadFailed ? t('暂无日线走势，准备状态读取失败')
              : preparation?.resources.dailyChart.available ? t('日线已准备，正在更新图表')
                : preparation?.status === 'failed' || preparation?.refreshStatus === 'failed' ? t('日线准备失败，后台将稍后重试')
                  : t('后台正在准备日线，完成后自动显示')}
          </div>
        )}
        <div className="mt-2 flex items-center justify-between gap-2">
          <StrengthBar score={item.strengthScore} width={56} />
          {signalLabel && <span className="truncate text-micro text-ink-400">{signalLabel}</span>}
        </div>
      </Link>
    </motion.div>
  );
}

/** 雷达卡片格骨架：2 列镜像真实卡（logo+胶囊 / 名称+徽标 / 强度条） */
function SignalGridSkeleton({ cards }: { cards: number }) {
  return (
    <div
      className="grid grid-cols-1 gap-2.5 px-4 pb-4 pt-3 sm:grid-cols-2 md:px-5 md:pb-5 xl:grid-cols-2"
      aria-hidden="true"
    >
      {Array.from({ length: cards }, (_, i) => (
        <div key={i} className="card-surface rounded-lg p-3">
          <div className="flex items-center gap-2">
            <SkeletonBlock className="size-6 rounded-sm" />
            <SkeletonBlock className="h-3 w-12" />
            <SkeletonBlock className="h-4 w-14 rounded-xs" />
            <SkeletonBlock className="ml-auto h-3 w-10" />
          </div>
          <div className="mt-2 flex items-center gap-2">
            <SkeletonBlock className="h-3 flex-1" />
            <SkeletonBlock className="h-3 w-14" />
            <SkeletonBlock className="h-4 w-12 rounded-xs" />
          </div>
          <SkeletonBlock className="mt-2.5 h-[3px] w-14 rounded-pill" />
        </div>
      ))}
    </div>
  );
}

/** 自选卡骨架：镜像完整日线图区，加载前后保持卡片高度。 */
function MoverGridSkeleton({ cards }: { cards: number }) {
  return (
    <div className="grid grid-cols-1 gap-2.5 px-4 pb-4 pt-3 sm:grid-cols-2 md:px-5 md:pb-5" aria-hidden="true">
      {Array.from({ length: cards }, (_, i) => (
        <div key={i} className="card-surface rounded-lg p-4">
          <div className="flex items-center gap-2">
            <SkeletonBlock className="size-6 rounded-sm" />
            <SkeletonBlock className="h-3 w-12" />
            <SkeletonBlock className="h-3 flex-1" />
            <SkeletonBlock className="h-4 w-12 rounded-xs" />
          </div>
          <div className="mt-2 flex items-end justify-between gap-2">
            <SkeletonBlock className="h-6 w-24" />
            <SkeletonBlock className="h-6 w-24" />
          </div>
          <SkeletonBlock className="mt-3 h-[112px] w-full rounded-sm" />
          <SkeletonBlock className="mt-2.5 h-[3px] w-14 rounded-pill" />
        </div>
      ))}
    </div>
  );
}
