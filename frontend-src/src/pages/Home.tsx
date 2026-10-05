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
import type { BreakoutSignal, EarningsItem, IndexQuote, MarketSession, WatchlistItem } from '@/api/types';
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
import { useTickFlash } from '@/hooks/useTickFlash';
import { useStockDataStatus } from '@/hooks/useStockDataStatus';
import type { StockDataStatus } from '@/lib/stockDataStatus';
import { quoteSymbol } from '@/lib/quoteSymbol';
import { MARKET_LABEL, MARKET_TO_SESSION } from '@/lib/marketSession';
import { useNow } from '@/hooks/useNow';
import { cn } from '@/lib/utils';
import { EASE_PAPER, GROW_X } from '@/lib/motion';
import { fmtCountdown, fmtNyTime, fmtRelative, fmtTimeHHMMSS } from '@/lib/format';
import { instrumentName, signed } from '@/components/cta/ctaMeta';
import EconomicCalendarCard from '@/components/catalysts/EconomicCalendarCard';
import PageHeader from '@/components/shared/PageHeader';
import StaleStrip from '@/components/shared/StaleStrip';
import StockDataCoverage from '@/components/shared/StockDataCoverage';
import SessionLED from '@/components/shared/SessionLED';
import SoftBadge from '@/components/shared/SoftBadge';
import { strengthBarClass } from '@/lib/strengthColor';
import { exNum, isFeaturedRow, type EarningsRow } from '@/components/earnings/types';
import ChangeBadge from '@/components/shared/ChangeBadge';
import IndexCard, { IndexCardSkeleton } from '@/components/shared/IndexCard';
import TickerLogo from '@/components/shared/TickerLogo';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonBlock, SkeletonCard, SkeletonRows } from '@/components/shared/Skeleton';
import Sparkline from '@/components/charts/Sparkline';
import Icon from '@/components/icons';
import { BusyIcon } from '@/components/shared/IconSwap';
import { pageRegionProps } from '@/lib/pageRegion';
import { localeTag, t } from '../i18n/core.ts';

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

/* 指数带：窄于 360px 时两列，其余手机与 sm–lg 三列，xl 起一行排满 */
const INDEX_GRID = 'grid grid-cols-2 gap-2 min-[360px]:grid-cols-3 sm:gap-3 xl:[grid-template-columns:repeat(auto-fit,minmax(170px,1fr))]';
const indexKey = (quote: IndexQuote) => quote.code;
const indexPrice = (quote: IndexQuote) => quote.price;

/** 列表错峰档位：i*0.04 封顶 0.3（雷达强度条、自选卡同一节奏） */
function staggerDelay(i: number): number {
  return Math.min(i * 0.04, 0.3);
}

function RetryButton({ onClick, refreshing }: { onClick: () => void; refreshing: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={refreshing}
      aria-busy={refreshing}
      className="btn-primary"
    >
      <BusyIcon busy={refreshing} size={14} tone="on-accent" />
      {t('重试')}
    </button>
  );
}

/** 区块卡统一外壳：text-h3 标题 + 右侧 brand-700「查看全部」；纵向弹性布局，
 *  底部数据时间行用 mt-auto 贴底——同一行的两张卡被栅格拉到等高后，底边与页脚都对齐。 */
function SectionCard({
  title,
  to,
  updatedAt,
  children,
  className,
}: {
  title: string;
  to: string;
  /** 本区数据最近一次成功读取的时间；各区轮询节奏不同，各自标明 */
  updatedAt?: number | null;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('card-surface flex flex-col', className)} aria-label={title}>
      <div className="flex items-center justify-between gap-3 px-4 pb-1 pt-4 md:px-5 md:pt-5">
        <h2 className="text-h3 text-ink-900">{title}</h2>
        <Link
          to={to}
          /* 文字链只有 16px 高：粗指针下用上下内边距补到 44px，负外边距抵消，版面不动 */
          className="link-learn shrink-0 text-caption font-medium text-brand-700 transition-colors duration-fast hover:text-brand-600 [@media(pointer:coarse)]:-my-3.5 [@media(pointer:coarse)]:py-3.5"
        >
          {t('查看全部')}
          <span className="link-learn-chevron" aria-hidden="true">
            <Icon name="chevron-right" size={12} />
          </span>
        </Link>
      </div>
      {children}
      {updatedAt ? (
        <p className="mt-auto flex items-center gap-1.5 border-t border-line px-4 py-2.5 text-micro text-ink-400 md:px-5">
          {t('更新')}
          <span className="font-mono tnum">{fmtTimeHHMMSS(updatedAt)}</span>
        </p>
      ) : null}
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
    <div className="rounded-[var(--r-group)] bg-paper-2/70 py-2.5 text-center">
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
  const indexFlashes = useTickFlash(indicesQ.data, indexKey, indexPrice);
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
        meta={
          <>
            <SessionLED
              session={session}
              label={session && status?.market ? MARKET_LABEL[status.market] : undefined}
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
      <section
        className="mt-5 md:mt-8"
        aria-label={t('指数概览')}
        {...pageRegionProps(
          'home-indices',
          indicesQ.loading
            ? 'loading'
            : indicesQ.error && !indicesQ.data?.length
              ? 'error'
              : !(indicesQ.data?.length)
                ? 'empty'
                : 'content',
        )}
      >
        {indicesQ.loading ? (
          <div className={INDEX_GRID}>
            {Array.from({ length: 5 }, (_, i) => (
              <IndexCardSkeleton key={i} />
            ))}
          </div>
        ) : indicesQ.error && !indicesQ.data?.length ? (
          <div className="card-surface">
            <EmptyState
              variant="error"
              title={indicesQ.error.code === 503 ? t('数据暂不可用') : t('加载失败')}
              description={indicesQ.error.code === 503 ? t('暂无指数数据') : indicesQ.error.message}
              action={<RetryButton onClick={() => indicesQ.refresh()} refreshing={indicesQ.refreshing} />}
            />
          </div>
        ) : (
          <>
            {/* 有旧数据时刷新失败 → 明示陈旧，不清空指数卡（审计首页问题 5 补全） */}
            {indicesQ.error && (
              <StaleStrip onRetry={() => indicesQ.refresh()} refreshing={indicesQ.refreshing} className="mb-3" />
            )}
          <div className={INDEX_GRID}>
            {(indicesQ.data ?? []).map((q, i) => (
              <IndexCard
                key={q.code}
                quote={q}
                index={i}
                to={`/market?index=${q.code}`}
                /* sparkline mock-only：live 无指数 K 线端点，如实留空 */
                spark={isMock && q.changePct !== null ? getIndexIntraday(q.code, q.changePct) : null}
                flash={indexFlashes[q.code]}
              />
            ))}
          </div>
          </>
        )}
      </section>

      {/* 行2：市场状态 + 雷达信号。栅格默认 stretch，两卡同高、底边对齐；
          lg 以下雷达排到市场状态前面，手机首屏在指数之后就能看到信号
          （市场时段与更新时间页头已有）。 */}
      <div className="mt-6 grid grid-cols-1 gap-6 md:mt-8 lg:grid-cols-3">
        <MarketStatusPanel
          status={status}
          session={session}
          loading={statusQ.loading}
          error={statusQ.error}
          refreshing={statusQ.refreshing}
          onRetry={() => statusQ.refresh()}
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

        <SectionCard title={t('雷达信号')} to="/breakouts" updatedAt={breakoutsQ.lastUpdatedAt} className="order-first lg:order-none lg:col-span-2">
          <ListBody
            loading={breakoutsQ.loading}
            error={breakoutsQ.error}
            refreshing={breakoutsQ.refreshing}
            onRetry={() => breakoutsQ.refresh()}
            isEmpty={breakouts.length === 0}
            emptyTitle={t('暂无突破信号')}
            skeleton={<RadarListSkeleton rows={8} />}
          >
            <div className="mt-2 divide-y divide-line border-t border-line">
              {breakouts.map((s, i) => (
                <RadarSignalRow key={s.id} signal={s} index={i} />
              ))}
            </div>
          </ListBody>
        </SectionCard>
      </div>

      {/* 行3：财报临近 + 自选异动，与行2同一套 1/3 + 2/3 栅格，两卡同高 */}
      <div className="mt-6 grid grid-cols-1 gap-6 md:mt-8 lg:grid-cols-3">
        <SectionCard title={t('财报临近')} to="/earnings" updatedAt={earningsQ.lastUpdatedAt}>
          <ListBody
            loading={earningsQ.loading}
            error={earningsQ.error}
            refreshing={earningsQ.refreshing}
            onRetry={() => earningsQ.refresh()}
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

        <SectionCard title={t('关注池异动')} to="/watchlist" updatedAt={watchlistQ.lastUpdatedAt} className="lg:col-span-2">
          <ListBody
            loading={watchlistQ.loading}
            error={watchlistQ.error}
            refreshing={watchlistQ.refreshing}
            onRetry={() => watchlistQ.refresh()}
            isEmpty={movers.length === 0}
            emptyTitle={t('暂无关注标的')}
            skeleton={<MoverListSkeleton rows={5} />}
          >
            {/* 异动最大的一只作主条目（大图 + 区间说明 + 强度），其余为紧凑行；
                md 起主条目在左、紧凑行在右，中间一条发丝线 */}
            <div className={`mt-2 border-t border-line${movers.length > 1 ? ' md:grid md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]' : ''}`}>
              {movers[0] && (
                <WatchlistMoverLead
                  item={movers[0]}
                  index={0}
                  preparation={readiness.byTicker.get(quoteSymbol(movers[0].ticker))}
                  statusReadFailed={Boolean(readiness.error)}
                />
              )}
              {/* 只有一只时不留空的右栏和竖线，主条目占满整行 */}
              {movers.length > 1 && (
                <div className="divide-y divide-line border-t border-line md:border-l md:border-t-0">
                  {movers.slice(1).map((item, i) => (
                    <WatchlistMoverRow
                      key={item.ticker}
                      item={item}
                      index={i + 1}
                      preparation={readiness.byTicker.get(quoteSymbol(item.ticker))}
                      statusReadFailed={Boolean(readiness.error)}
                    />
                  ))}
                </div>
              )}
            </div>
          </ListBody>
        </SectionCard>
      </div>

      <EconomicCalendarCard />

      {/* 行4：CTA 趋势资金联动带。区块常驻：加载给骨架、失败给错误行、
          快照未发布给说明——整块消失会让「本来没有」与「没读到」不可分辨，
          还引发布局跳动（GPT-5.6-Pro 审计首页问题 5）。 */}
      <section className="mt-8" aria-label={t('CTA 趋势资金')}>
        <div className="card-surface p-4 md:p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-h3 text-ink-900">{t('CTA 趋势资金')}</h2>
            <Link
              to="/cta"
              className="link-learn shrink-0 text-caption font-medium text-brand-700 transition-colors duration-fast hover:text-brand-600 [@media(pointer:coarse)]:-my-3.5 [@media(pointer:coarse)]:py-3.5"
            >
              {t('查看全部')}
              <span className="link-learn-chevron" aria-hidden="true">
                <Icon name="chevron-right" size={12} />
              </span>
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
                ? t('CTA 估算尚未生成，首次计算完成后自动显示')
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
                      'mt-0.5 text-data-m tnum',
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
                  <p className="mt-0.5 text-micro text-ink-400 tnum">{signed(ins.flow_score)}</p>
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

  if (loading && !status) return <SkeletonCard className="h-full" />;
  if (error && !status) {
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
      {error && <StaleStrip onRetry={onRetry} refreshing={refreshing} className="mb-3" />}
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-h3 text-ink-900">{t('市场状态')}</h2>
        <SessionLED
          session={session}
          label={session && status?.market ? MARKET_LABEL[status.market] : undefined}
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
            <span className="metric-value text-data-m text-ink-900">
              {mean === null ? '—' : mean.toFixed(1)}
            </span>
            {bias && <span className="text-caption text-ink-500">{bias}</span>}
          </span>
        </div>
      </div>

      <p className="mt-3 text-micro text-ink-400">
        {t('扫描池')}
        {breadth.total !== null && <span className="tnum"> · {breadth.total}</span>}
      </p>
      <div className="mb-4 mt-1.5 grid grid-cols-3 gap-2">
        <MiniStat label={t('上涨')} value={breadth.adv} tone="up" />
        <MiniStat label={t('下跌')} value={breadth.dec} tone="down" />
        <MiniStat label={t('平盘')} value={breadth.flat} tone="flat" />
      </div>

      {/* 辅助指标直接展示；缺失读数仍遵守原有数据纪律，不补零。 */}
      {(strength?.aggregateAvailable === true || (signalMetrics && signalMetrics.length > 0)) && (
        <div className="mt-auto border-t border-line/70 pt-3" data-testid="home-supporting-metrics">
          <p className="text-caption font-medium text-ink-600">{t('辅助指标')}</p>
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
        </div>
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

/* 雷达信号行的栅格：lg 及以下两行（代码与公司、价格与涨跌 / 形态与时间、强度），
   xl 起一行七列定宽，上下行的形态、时间、价格、涨跌、强度各自对齐。 */
const RADAR_ROW_GRID =
  "grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 px-4 py-2.5 [grid-template-areas:'logo_id_quote'_'logo_meta_bar'] md:px-5 xl:grid-cols-[28px_minmax(0,1fr)_5rem_5rem_8rem_5.25rem_6.5rem] xl:gap-y-0 xl:[grid-template-areas:'logo_id_chip_time_price_change_bar']";

/** 强度读数：轨道作视口观察者，条用 GROW_X 从左长出（零面积的条自己观察会一直判不进视口）。
 *  读屏沿用 StrengthBar 的「强度分 {score}」/「强度分缺失」。 */
function GrowStrength({ score, delay = 0, className }: { score: number | null | undefined; delay?: number; className?: string }) {
  const valid = typeof score === 'number' && Number.isFinite(score);
  return (
    <span
      className={cn('inline-flex items-center gap-2', className)}
      aria-label={valid ? t('强度分 {score}', { score: score.toFixed(1) }) : t('强度分缺失')}
    >
      <motion.span
        className="strength-track block h-1 w-14 shrink-0 overflow-hidden rounded-pill"
        role="presentation"
        initial="hidden"
        whileInView="shown"
        viewport={{ once: true, amount: 0.4 }}
      >
        {valid && (
          <motion.span
            className={cn('strength-fill block h-full origin-left rounded-pill', strengthBarClass(score))}
            variants={GROW_X}
            transition={{ duration: 0.7, ease: EASE_PAPER, delay }}
            style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
          />
        )}
      </motion.span>
      <span className="w-8 text-right text-caption font-medium text-ink-600 tnum">{valid ? score.toFixed(1) : '—'}</span>
    </span>
  );
}

/** 雷达信号行：代码+公司 / 形态 / 相对时间 / 价格+涨跌 / 强度，行间发丝线分隔。
 *  不加 aria-label：整行可见内容自然组成可访问名（审计）。 */
function RadarSignalRow({ signal: s, index: i }: { signal: BreakoutSignal; index: number }) {
  useQuoteSymbols([s.ticker]);
  return (
    <Link
      to={`/stock/${encodeURIComponent(s.ticker)}`}
      className={`${RADAR_ROW_GRID} transition-colors duration-fast hover:bg-paper-2/70 focus-visible:bg-paper-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600`}
    >
      <TickerLogo ticker={s.ticker} size={28} className="[grid-area:logo]" />
      <span className="flex min-w-0 items-baseline gap-2 [grid-area:id]">
        <span className="shrink-0 font-mono text-caption font-semibold text-ink-800">{s.ticker}</span>
        <span className="min-w-0 truncate text-caption text-ink-500">{s.name}</span>
      </span>
      <span className="flex min-w-0 items-center gap-2 [grid-area:meta] xl:contents">
        <SoftBadge className="min-w-0 xl:justify-self-start xl:[grid-area:chip]">
          <span className="truncate">{s.label}</span>
        </SoftBadge>
        <span className="shrink-0 text-micro text-ink-400 xl:justify-self-end xl:[grid-area:time]">{fmtRelative(s.at)}</span>
      </span>
      <span className="flex items-center justify-end gap-2 [grid-area:quote] xl:contents">
        <span className="text-caption text-ink-800 tnum xl:justify-self-end xl:[grid-area:price]">
          <LivePrice symbol={s.ticker} fallback={s.price} className="justify-end" />
        </span>
        <LiveChange symbol={s.ticker} fallback={s.changePct} size="sm" className="shrink-0 xl:justify-self-end xl:[grid-area:change]" />
      </span>
      <GrowStrength score={s.strengthScore} delay={staggerDelay(i)} className="justify-self-end [grid-area:bar]" />
    </Link>
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
      className="flex items-center gap-3 px-4 py-2.5 transition-colors duration-fast hover:bg-paper-2/70 focus-visible:bg-paper-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600 md:px-5"
    >
      <span
        className={cn(
          'w-11 shrink-0 rounded-[var(--r-group)] py-1.5 text-center',
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
          <span className="hidden text-micro text-ink-500 tnum sm:block lg:hidden xl:block">
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

type MoverProps = { item: WatchlistItem; index: number; preparation?: StockDataStatus; statusReadFailed: boolean };

/** 日线走势：最多 30 个缓存日线，少于两点不画；区间涨跌按首末收盘计算。 */
function moverTrend(item: WatchlistItem) {
  const trend = item.dailyTrend && item.dailyTrend.length > 1 ? item.dailyTrend : null;
  const spark = trend?.map((point) => point.close) ?? null;
  const periodChange = spark ? (spark[spark.length - 1] / spark[0] - 1) * 100 : null;
  return { trend, spark, periodChange };
}

function moverTrendLabel(ticker: string, trend: { date: string }[], periodChange: number): string {
  return t('{ticker} 日线走势，{start} 至 {end}，区间涨跌 {change}%', {
    ticker,
    start: trend[0].date,
    end: trend[trend.length - 1].date,
    change: periodChange.toFixed(2),
  });
}

/** 还没有日线时区分「读取失败 / 已获取待重绘 / 获取失败 / 正在获取」。 */
function moverPendingText(preparation: StockDataStatus | undefined, statusReadFailed: boolean): string {
  if (statusReadFailed) return t('暂无日线走势，准备状态读取失败');
  if (preparation?.resources.dailyChart.available) return t('日线已获取，正在更新图表');
  if (preparation?.status === 'failed' || preparation?.refreshStatus === 'failed') return t('日线获取失败，稍后自动重试');
  return t('正在获取日线，完成后自动显示');
}

const MOVER_LINK =
  'transition-colors duration-fast hover:bg-paper-2/70 focus-visible:bg-paper-2/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-600';

/* LivePrice 的价格与「参考价/日期」标签同在一个外层：外层定小字，价格单独放大，
   标签不会跟着价格变成大字。 */
const LEAD_PRICE = 'text-micro [&>.tick-flash]:text-[24px] [&>.tick-flash]:leading-8';

/** 关注池主条目：当日涨跌与最长 30 交易日日线分开标明，长期图仅取真实日线。 */
function WatchlistMoverLead({ item, index: i, preparation, statusReadFailed }: MoverProps) {
  useQuoteSymbols([item.ticker]);
  const { trend, spark, periodChange } = moverTrend(item);
  const signalLabel = item.signals?.[0]?.label ?? null;
  return (
    <Link
      to={`/stock/${encodeURIComponent(item.ticker)}`}
      className={`flex flex-col px-4 py-4 md:h-full md:px-5 ${MOVER_LINK}`}
      data-testid="watchlist-mover-card"
    >
      <div className="flex items-center gap-2.5">
        <TickerLogo ticker={item.ticker} size={28} />
        <span className="shrink-0 font-mono text-caption font-semibold text-ink-800">{item.ticker}</span>
        <span className="min-w-0 flex-1 truncate text-caption text-ink-500">{item.name}</span>
        <span className="flex shrink-0 items-center gap-1.5">
          <span className="text-micro text-ink-400">{t('当日')}</span>
          <LiveChange symbol={item.ticker} fallback={item.changePct} fallbackAt={item.updatedAt} size="sm" />
        </span>
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <span className="metric-value min-w-0 text-ink-900">
          <LivePrice symbol={item.ticker} fallback={item.price} fallbackAt={item.updatedAt} className={LEAD_PRICE} />
        </span>
        <span className="shrink-0 text-micro text-ink-400">{trend ? t('近 {count} 个交易日', { count: trend.length }) : t('日线走势')}</span>
      </div>
      {spark && trend && periodChange !== null ? (
        /* md 起与右侧紧凑行并排：图随这一栏的高度长高，封顶 220px，再高就把 30 日的
           起伏放大成陡峰，多出的空间留在说明行与强度行之间。上下堆叠时固定 104px。 */
        <figure className="mt-3 flex flex-col md:flex-1" data-testid="watchlist-daily-trend" aria-label={moverTrendLabel(item.ticker, trend, periodChange)}>
          <Sparkline data={spark} width={480} height={104} change={periodChange} variant="area" stretch className="h-[104px] w-full md:h-auto md:max-h-[220px] md:min-h-[104px] md:flex-1" />
          <figcaption className="mt-1 flex items-center justify-between gap-2 font-mono text-micro text-ink-400 tnum">
            <span>{trend[0].date.slice(5)} — {trend[trend.length - 1].date.slice(5)}</span>
            <span className="flex items-center gap-1.5"><span className="font-sans">{t('区间')}</span><ChangeBadge value={periodChange} size="sm" /></span>
          </figcaption>
        </figure>
      ) : (
        <div className="mt-3 flex min-h-[122px] items-center justify-center rounded-sm bg-paper-2 px-4 text-center text-caption text-ink-400 md:flex-1">
          {moverPendingText(preparation, statusReadFailed)}
        </div>
      )}
      <div className="mt-3 flex items-center justify-between gap-3">
        <GrowStrength score={item.strengthScore} delay={staggerDelay(i)} />
        {signalLabel && <span className="min-w-0 truncate text-micro text-ink-400">{signalLabel}</span>}
      </div>
    </Link>
  );
}

/** 关注池紧凑行：代码 / 价格 / 当日与区间涨跌，右侧 80px 高的日线小图（图宽至少 150px）。 */
function WatchlistMoverRow({ item, preparation, statusReadFailed }: MoverProps) {
  useQuoteSymbols([item.ticker]);
  const { trend, spark, periodChange } = moverTrend(item);
  return (
    <Link
      to={`/stock/${encodeURIComponent(item.ticker)}`}
      className={`grid grid-cols-[minmax(0,1fr)_minmax(150px,46%)] items-center gap-3 px-4 py-2.5 md:px-5 ${MOVER_LINK}`}
      data-testid="watchlist-mover-card"
    >
      <div className="min-w-0">
        <p className="flex min-w-0 items-center gap-2">
          <TickerLogo ticker={item.ticker} size={20} />
          <span className="shrink-0 font-mono text-caption font-semibold text-ink-800">{item.ticker}</span>
          <span className="min-w-0 truncate text-caption text-ink-500">{item.name}</span>
        </p>
        <p className="mt-1.5 text-caption text-ink-800 tnum">
          <LivePrice symbol={item.ticker} fallback={item.price} fallbackAt={item.updatedAt} />
        </p>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="flex items-center gap-1.5">
            <span className="text-micro text-ink-400">{t('当日')}</span>
            <LiveChange symbol={item.ticker} fallback={item.changePct} fallbackAt={item.updatedAt} size="sm" />
          </span>
          {periodChange !== null && (
            <span className="flex items-center gap-1.5">
              <span className="text-micro text-ink-400">{t('区间')}</span>
              <ChangeBadge value={periodChange} size="sm" />
            </span>
          )}
        </p>
      </div>
      {spark && trend && periodChange !== null ? (
        <figure data-testid="watchlist-daily-trend" aria-label={moverTrendLabel(item.ticker, trend, periodChange)}>
          <Sparkline data={spark} width={220} height={80} change={periodChange} variant="area" stretch className="h-20 w-full" />
        </figure>
      ) : (
        <div className="flex h-20 items-center justify-center rounded-sm bg-paper-2 px-2 text-center text-micro text-ink-400">
          {moverPendingText(preparation, statusReadFailed)}
        </div>
      )}
    </Link>
  );
}

/** 雷达列表骨架：与真实行同一套栅格，加载前后行高不跳 */
function RadarListSkeleton({ rows }: { rows: number }) {
  return (
    <div className="mt-2 divide-y divide-line border-t border-line" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className={RADAR_ROW_GRID}>
          <SkeletonBlock className="size-7 rounded-md [grid-area:logo]" />
          <SkeletonBlock className="h-3 w-28 [grid-area:id]" />
          <SkeletonBlock className="h-4 w-24 rounded-xs [grid-area:meta] xl:[grid-area:chip]" />
          <SkeletonBlock className="h-4 w-28 justify-self-end rounded-xs [grid-area:quote] xl:[grid-area:price]" />
          <SkeletonBlock className="h-1 w-24 justify-self-end rounded-pill [grid-area:bar]" />
        </div>
      ))}
    </div>
  );
}

/** 关注池骨架：主条目（大图区）+ 紧凑行（右侧 80px 图区），加载前后高度一致。 */
function MoverListSkeleton({ rows }: { rows: number }) {
  return (
    <div className="mt-2 divide-y divide-line border-t border-line" aria-hidden="true">
      <div className="px-4 py-4 md:px-5">
        <div className="flex items-center gap-2.5">
          <SkeletonBlock className="size-7 rounded-md" />
          <SkeletonBlock className="h-3 w-12" />
          <SkeletonBlock className="h-3 flex-1" />
          <SkeletonBlock className="h-4 w-14 rounded-xs" />
        </div>
        <SkeletonBlock className="mt-2 h-8 w-28" />
        <SkeletonBlock className="mt-3 h-[122px] w-full rounded-sm" />
        <SkeletonBlock className="mt-3 h-1 w-24 rounded-pill" />
      </div>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(150px,46%)] items-center gap-3 px-4 py-2.5 md:px-5">
          <div className="space-y-2">
            <SkeletonBlock className="h-3 w-24" />
            <SkeletonBlock className="h-3 w-16" />
            <SkeletonBlock className="h-4 w-28 rounded-xs" />
          </div>
          <SkeletonBlock className="h-20 w-full rounded-sm" />
        </div>
      ))}
    </div>
  );
}
