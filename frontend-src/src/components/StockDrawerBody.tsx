/**
 * 股票详情内容（stock-detail.md；抽屉与 /stock/:t 整页共用）
 * S0 头部：TickerLogo/名称/大价格 Data-XXL(count-up + tick-flash)/ChangeBadge/时段 chip/quote_as_of
 * S1 K线主图（KlineChart：蜡烛/点阵面积 + range Segmented + quote_only/_stale/503）
 * S2 Tab 区：信号（trend_bias 仪表 + 信号卡 + AI 股票分析）· 期权链 · 相关新闻
 * layout="page"：12 列——图表 8 列 + 侧栏（关键数据 + 相关突破事件）4 列，Tab 落通栏下方
 */
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { marketApi } from '@/api/modules/market';
import { breakoutsApi } from '@/api/modules/breakouts';
import { usePolling } from '@/hooks/usePolling';
import { useCountUp } from '@/hooks/useCountUp';
import { useIsMobile } from '@/hooks/use-mobile';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtPrice, fmtRelative, fmtTimeHHMMSS } from '@/lib/format';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import SessionLED from '@/components/shared/SessionLED';
import StrengthBar from '@/components/shared/StrengthBar';
import SignalChip from '@/components/shared/SignalChip';
import SourceNote from '@/components/shared/SourceNote';
import EmptyState from '@/components/shared/EmptyState';
import InfoHint from '@/components/shared/InfoHint';
import MacroFitPanel from '@/components/shared/MacroFitPanel';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonBlock, SkeletonText } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { getDetail, prefetchStockDetailPanels } from '@/components/detail/api';
import KlineChart from '@/components/detail/KlineChart';
import TrendBiasPanel from '@/components/detail/TrendBiasPanel';
import SignalList from '@/components/detail/SignalList';
import OptionsPanel from '@/components/detail/OptionsPanel';
import NewsPanel from '@/components/detail/NewsPanel';
import AiAnalysisCard from '@/components/detail/AiAnalysisCard';
import ManualStockPull from '@/components/detail/ManualStockPull';
import KeyStats from '@/components/detail/KeyStats';
import type { StockDetail } from '@/api/types';
import { t, t as __t } from '../i18n/core.ts';

type TabKey = 'signals' | 'options' | 'news';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'signals', label: __t('信号') },
  { key: 'options', label: __t('期权链') },
  { key: 'news', label: __t('新闻') },
];

/** live 缺失数值字段（类型为 number 但运行时可为 null）如实显「—」 */
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const compactOr = (v: number | null | undefined): string => (isNum(v) ? fmtCompact(v) : '—');

/* ---------------- S0 头部 ---------------- */
function PriceHeader({ detail }: { detail: StockDetail }) {
  const { data: market } = usePolling(() => marketApi.status(), 60_000, []);
  const shown = useCountUp(detail.price);
  const prevPrice = useRef(detail.price);
  const [flash, setFlash] = useState<'up' | 'down' | null>(null);

  useEffect(() => {
    if (detail.price !== prevPrice.current) {
      const direction = detail.price > prevPrice.current ? 'up' : 'down';
      prevPrice.current = detail.price;
      const start = window.setTimeout(() => setFlash(direction), 0);
      const finish = window.setTimeout(() => setFlash(null), 600);
      return () => {
        window.clearTimeout(start);
        window.clearTimeout(finish);
      };
    }
  }, [detail.price]);

  return (
    <motion.header
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <TickerLogo ticker={detail.ticker} size={40} />
        <div className="min-w-0">
          <p className="flex flex-wrap items-baseline gap-x-2.5">
            <span className="font-display text-[22px] leading-[28px] font-bold text-ink-900">{detail.ticker}</span>
            <span className="text-body-s text-ink-500">{detail.name}</span>
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="rounded-xs border border-line-strong bg-card-warm px-1.5 py-px text-micro text-ink-500">
              {detail.sector}
            </span>
            {market && <SessionLED session={market.session} label={t('{label} · 延迟 15 分钟', { label: market.label })} />}
          </div>
        </div>
        <div className="ml-auto text-right">
          <p className="eyebrow">
            {__t('强度分')}
            <InfoHint hint={SCORE_HINTS.strengthComposite} side="bottom" align="end" size={12} className="ml-1" />
          </p>
          <StrengthBar score={detail.strengthScore} width={72} className="mt-1.5" />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-full flex flex-wrap items-end gap-x-3 gap-y-2">
          <p
            className={cn(
              'rounded-sm px-1 font-mono text-[clamp(30px,10vw,44px)] font-medium leading-none tracking-[-0.02em] text-ink-900 tnum',
              flash === 'up' && 'animate-tick-flash-up',
              flash === 'down' && 'animate-tick-flash-down',
            )}
          >
            ${fmtPrice(shown)}
          </p>
          <div className="flex items-center gap-2 pb-1.5">
            <ChangeBadge value={detail.changePct} />
            {isNum(detail.change) && (
              <span className={cn('font-mono text-data-m tnum', detail.change >= 0 ? 'text-up-700' : 'text-down-700')}>
                {detail.change >= 0 ? '+' : '−'}{fmtPrice(Math.abs(detail.change))}
              </span>
            )}
          </div>
        </div>
        <p className="pb-1.5 text-right font-mono text-micro text-ink-500 tnum">
          {__t('成交量')} {compactOr(detail.volume)} {__t('· 市值')} {isNum(detail.marketCap) ? `$${fmtCompact(detail.marketCap)}` : '—'}
        </p>
      </div>

      <p className="mt-2 text-micro text-ink-400">
        {__t('报价更新于')} <span className="font-mono tnum">{fmtTimeHHMMSS(new Date(detail.updatedAt))}</span>
        {__t(' · 延迟行情')}
      </p>
    </motion.header>
  );
}

/* ---------------- Tab 头（2px 指示条滑动） ---------------- */
function TabHeader({
  tab,
  onChange,
  pageLayout = false,
}: {
  tab: TabKey;
  onChange: (t: TabKey) => void;
  /** 整页形态：sticky 参考的是视口，需让出 sticky Navbar 的高度（审计 2.4.2）。
      抽屉形态 sticky 相对抽屉滚动容器，保持 top-0。 */
  pageLayout?: boolean;
}) {
  return (
    <div
      role="tablist"
      className={cn(
        'sticky z-10 flex gap-1 border-b border-line bg-card/95 backdrop-blur-sm',
        pageLayout ? 'top-12 md:top-16' : 'top-0',
      )}
    >
      {TABS.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={tab === t.key}
          onClick={() => onChange(t.key)}
          className={cn(
            'relative px-4 py-2.5 text-body-s font-medium transition-colors duration-fast',
            tab === t.key ? 'text-ink-900' : 'text-ink-400 hover:text-ink-600',
          )}
        >
          {t.label}
          {tab === t.key && (
            <motion.span
              layoutId="detail-tab-underline"
              className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-brand-600"
              transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            />
          )}
        </button>
      ))}
    </div>
  );
}

/* ---------------- 侧栏：相关突破事件（≤3） ---------------- */
function SidebarEvents({ ticker }: { ticker: string }) {
  const { data, loading, error, refresh } = usePolling(() => breakoutsApi.byTicker(ticker), null, [ticker]);
  const items = (data ?? []).slice(0, 3);
  /* loading / error / empty 分开（审计 2.2.2）；空态文案不再写「近 72 小时」——
     后端按 last_seen_at 返回全部历史事件的最近 50 条，没有时间窗（审计 2.3.1）。 */
  return (
    <div className="card-surface p-5">
      <p className="eyebrow">BREAKOUT EVENTS</p>
      <h3 className="mt-1.5 text-h3 text-ink-900">{__t('相关突破事件')}</h3>
      {loading && !data ? (
        <div className="mt-3 space-y-2" aria-hidden="true">
          <span className="skeleton-shimmer block h-4 w-full rounded-xs" />
          <span className="skeleton-shimmer block h-4 w-2/3 rounded-xs" />
        </div>
      ) : error && !data ? (
        <p className="mt-3 flex items-center gap-2 text-body-s text-ink-400">
          <Icon name="doc-quote" size={16} className="text-ink-300" />
          {__t('突破事件读取失败')}
          <button
            onClick={() => refresh()}
            className="ml-auto rounded-md border border-line px-2 py-0.5 text-micro text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
          >
            {__t('重试')}
          </button>
        </p>
      ) : items.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-body-s text-ink-400">
          <Icon name="radar" size={16} className="text-ink-300" />
          {__t('暂无突破事件记录 · 雷达仍在盯')}
        </p>
      ) : (
        <ul className="mt-3 space-y-2.5">
          {items.map((e) => (
            <li key={e.id} className="flex items-center gap-2.5">
              <SignalChip type={e.type} label={e.label} />
              <span className="font-mono text-caption text-ink-800 tnum">{fmtPrice(e.price)}</span>
              <span className="ml-auto text-micro text-ink-400">{fmtRelative(e.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------------- 主体 ---------------- */
export default function StockDrawerBody({ ticker, layout = 'drawer' }: { ticker: string; layout?: 'drawer' | 'page' }) {
  const [dataRevision, setDataRevision] = useState(0);
  const forceDetailRef = useRef({ ticker, force: false });
  const {
    data: detail,
    loading,
    error,
    refreshing,
    refresh,
  } = usePolling(
    () => {
      const force = (
        forceDetailRef.current.ticker === ticker
        && forceDetailRef.current.force
      );
      forceDetailRef.current = { ticker, force: false };
      return getDetail(ticker, force);
    },
    60_000,
    [ticker, dataRevision],
  );
  // K 线和信号面板都挂在下面的 `loading` 分支之后，可它们各取各的数据，详情对象里
  // 一个字段都不用 —— 于是两段互不依赖的往返被排成了串行：先等详情，再开始要 bars。
  //
  // 这里只把请求提前发出。面板挂载时命中的是 marketGet 的 in-flight promise 或它的
  // 缓存，请求总数不变，只是早了一个往返。换成「在 loading 分支里也挂一个 KlineChart」
  // 反而会出问题：两个分支的树结构不同，React 会卸载重挂，那次轮询就真的发两遍。
  useEffect(() => {
    prefetchStockDetailPanels(ticker);
  }, [ticker]);
  const [tabState, setTabState] = useState<{ ticker: string; tab: TabKey }>({
    ticker,
    tab: 'signals',
  });
  const tab = tabState.ticker === ticker ? tabState.tab : 'signals';
  const setTab = (next: TabKey) => setTabState({ ticker, tab: next });
  const isMobile = useIsMobile();

  const handlePulled = () => {
    forceDetailRef.current = { ticker, force: true };
    setDataRevision((value) => value + 1);
  };

  if (loading) {
    return (
      <div className="space-y-5 p-6" aria-busy="true">
        <div className="flex items-center gap-3">
          <SkeletonBlock className="size-10 rounded-sm" />
          <div className="flex-1 space-y-2">
            <SkeletonBlock className="h-4 w-32" />
            <SkeletonBlock className="h-3 w-20" />
          </div>
        </div>
        <SkeletonBlock className="h-12 w-48" />
        <SkeletonBlock className="h-[320px] w-full rounded-md" />
        <SkeletonText lines={4} />
      </div>
    );
  }

  /* 失败但仍有上一轮 detail 时继续展示（同文件下方信号区的 error && !data
     正是这个语义）：一次 408/断网就把价格头、K 线和 Tab 连内部状态一起卸载，
     对 60s 轮询来说代价完全不对称。 */
  if (!detail) {
    const is404 = error?.code === 404;
    const loginExpired = error?.code === 401;
    const rateLimited = error?.code === 429;
    const publicSnapshotMissing = error?.bizCode === 'public_snapshot_unavailable';
    const manualRecovery = publicSnapshotMissing || (!error && !detail);
    return (
      <EmptyState
        variant="empty"
        image="/empty-chart.svg"
        title={
          is404
            ? __t('代码不存在')
            : loginExpired
              ? __t('登录状态已失效')
              : rateLimited
                ? __t('请求较频繁')
                : manualRecovery
                  ? __t('该标的暂无完整数据')
                  : __t('行情服务暂不可用')
        }
        description={
          is404
            ? t('{ticker} 不在当前股票目录中', { ticker })
            : loginExpired
              ? __t('请重新登录后读取个股详情')
            : publicSnapshotMissing
              ? __t('该股票暂无数据，可手动获取最新行情、日线与技术指标')
              : `${error?.message || __t('暂时取不到该股票的行情数据')}${
                  error?.retryAfter ? t(' · {n} 秒后可重试', { n: Math.ceil(error.retryAfter) }) : ''
                }`
        }
        action={
          is404 ? (
            <Link
              to="/watchlist"
              className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi hover:brightness-105"
            >
              {__t('返回自选')}
            </Link>
          ) : loginExpired ? (
            <Link
              to="/login"
              state={{ from: `${window.location.pathname}${window.location.search}` }}
              className="inline-flex min-h-11 items-center justify-center rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi hover:brightness-105"
            >
              {__t('重新登录')}
            </Link>
          ) : manualRecovery ? (
            <ManualStockPull ticker={ticker} onPulled={handlePulled} />
          ) : (
            <button
              type="button"
              onClick={() => refresh()}
              disabled={refreshing}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter,opacity] hover:brightness-105 disabled:cursor-wait disabled:opacity-60"
            >
              <Icon name="refresh" size={14} />
              {refreshing ? __t('正在重试') : __t('重试')}
            </button>
          )
        }
        className="py-16"
      />
    );
  }

  /* 概览未覆盖该标的：基础行情来自强度扫描行，如实提示口径 */
  const scopeBanner = detail.snapshotScope === 'strength-row' && (
    <div className="mt-3 rounded-md border border-warn-600/25 bg-warn-50 px-3 py-2" role="status">
      <p className="flex items-start gap-2 text-caption leading-[18px] text-warn-600">
        <Icon name="flag" size={13} className="mt-px shrink-0" />
        {__t('当前只有筛选结果里的基础行情，日线与技术指标按实际情况显示')}
      </p>
      <ManualStockPull ticker={detail.ticker} onPulled={handlePulled} compact className="mt-2" />
    </div>
  );

  const tabs = (
    <div className={layout === 'page' ? 'mt-8' : 'mt-6'}>
      <TabHeader tab={tab} onChange={setTab} pageLayout={layout === 'page'} />
      <AnimatePresence mode="wait">
        <motion.div
          key={tab}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1, transition: { duration: 0.2 } }}
          exit={{ opacity: 0, transition: { duration: 0.12 } }}
          className="pt-5"
        >
          {tab === 'signals' && (
            <div className="space-y-6">
              <TrendBiasPanel ticker={detail.ticker} refreshVersion={dataRevision} />
              {/* 宏观适配放在信号页而不是关键数据侧栏：抽屉形态没有侧栏，放这里两种
                  布局共用一处。措辞说「该板块」—— 暴露画像是板块级的。 */}
              <div className="card-surface p-5">
                <MacroFitPanel
                  score={detail.macroFit}
                  tailwind={detail.macroTailwind}
                  confidence={detail.macroFitConfidence}
                  supporting={detail.macroSupporting}
                  opposing={detail.macroOpposing}
                  technicalGap={detail.macroTechnicalGap}
                  status={detail.macroShadowStatus}
                />
              </div>
              <div>
                <p className="eyebrow mb-3">RECENT SIGNALS</p>
                <SignalList ticker={detail.ticker} refreshVersion={dataRevision} />
              </div>
            </div>
          )}
          {tab === 'options' && <OptionsPanel ticker={detail.ticker} />}
          {tab === 'news' && <NewsPanel ticker={detail.ticker} />}
        </motion.div>
      </AnimatePresence>
      {/* AI 分析卡挂载与页签解耦：它持有付费任务的轮询与结果，若放在按 tab
          键控的 AnimatePresence 里，切到「期权链」就整卡卸载——任务与结果随
          组件销毁，切回只剩「开始分析」，全站又没有任务列表可找回，重点会再
          扣一次额度。非 signals 页签只隐藏不卸载；按 ticker 键控在换标的时
          正常重置。 */}
      <div className={tab === 'signals' ? 'mt-6' : 'hidden'}>
        <AiAnalysisCard key={detail.ticker} ticker={detail.ticker} />
      </div>
    </div>
  );
  const providerNote = [
    __t('行情为延迟数据'),
    __t('影响分表示新闻方向，不是收益预测'),
    __t('置信度是模型的把握程度，不是胜率'),
  ].join(' · ');

  if (layout === 'page') {
    return (
      <div>
        <PriceHeader detail={detail} />
        {scopeBanner}
        {detail.snapshotScope !== 'strength-row' && (
          <ManualStockPull ticker={detail.ticker} onPulled={handlePulled} compact className="mt-3" />
        )}
        <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="lg:col-span-8">
            <div className="card-surface p-5">
              <KlineChart ticker={detail.ticker} prevClose={detail.prevClose} height={420} refreshVersion={dataRevision} />
            </div>
          </div>
          <aside className="space-y-6 lg:col-span-4">
            <KeyStats detail={detail} />
            <SidebarEvents ticker={detail.ticker} />
          </aside>
        </div>
        {tabs}
        <SourceNote className="mt-8" text={providerNote} />
      </div>
    );
  }

  return (
    <div className="px-5 pb-8 pt-5 md:px-6">
      <PriceHeader detail={detail} />
      {scopeBanner}
      {detail.snapshotScope !== 'strength-row' && (
        <ManualStockPull ticker={detail.ticker} onPulled={handlePulled} compact className="mt-3" />
      )}
      <div className="mt-5">
        <KlineChart
          ticker={detail.ticker}
          prevClose={detail.prevClose}
          height={isMobile ? 260 : 320}
          refreshVersion={dataRevision}
        />
      </div>
      {tabs}
      <SourceNote className="mt-6" text={providerNote} />
    </div>
  );
}
