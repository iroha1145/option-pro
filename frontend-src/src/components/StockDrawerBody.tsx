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
import { SkeletonBlock, SkeletonText } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { getDetail } from '@/components/detail/api';
import KlineChart from '@/components/detail/KlineChart';
import TrendBiasPanel from '@/components/detail/TrendBiasPanel';
import SignalList from '@/components/detail/SignalList';
import OptionsPanel from '@/components/detail/OptionsPanel';
import NewsPanel from '@/components/detail/NewsPanel';
import AiAnalysisCard from '@/components/detail/AiAnalysisCard';
import KeyStats from '@/components/detail/KeyStats';
import type { StockDetail } from '@/api/types';

type TabKey = 'signals' | 'options' | 'news';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'signals', label: '信号' },
  { key: 'options', label: '期权链' },
  { key: 'news', label: '新闻' },
];

/* ---------------- S0 头部 ---------------- */
function PriceHeader({ detail }: { detail: StockDetail }) {
  const { data: market } = usePolling(() => marketApi.status(), 60_000, []);
  const shown = useCountUp(detail.price);
  const prevPrice = useRef(detail.price);
  const [flash, setFlash] = useState<'up' | 'down' | null>(null);

  useEffect(() => {
    if (detail.price !== prevPrice.current) {
      setFlash(detail.price > prevPrice.current ? 'up' : 'down');
      prevPrice.current = detail.price;
      const t = setTimeout(() => setFlash(null), 600);
      return () => clearTimeout(t);
    }
  }, [detail.price]);

  return (
    <motion.header
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center gap-3">
        <TickerLogo ticker={detail.ticker} size={40} />
        <div className="min-w-0">
          <p className="flex flex-wrap items-baseline gap-x-2.5">
            <span className="font-display text-[22px] leading-[28px] font-bold text-ink-900">{detail.ticker}</span>
            <span className="text-body-s text-ink-500">{detail.name}</span>
          </p>
          <div className="mt-1 flex items-center gap-2">
            <span className="rounded-xs border border-line-strong bg-card-warm px-1.5 py-px text-micro text-ink-500">
              {detail.sector}
            </span>
            {market && <SessionLED session={market.session} label={`${market.label} · 延迟15分钟`} />}
          </div>
        </div>
        <div className="ml-auto text-right">
          <p className="eyebrow">强度分</p>
          <StrengthBar score={detail.strengthScore} width={72} className="mt-1.5" />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          <p
            className={cn(
              'rounded-sm px-1 font-mono text-data-xxl text-ink-900 tnum',
              flash === 'up' && 'animate-tick-flash-up',
              flash === 'down' && 'animate-tick-flash-down',
            )}
          >
            ${fmtPrice(shown)}
          </p>
          <div className="flex items-center gap-2 pb-1.5">
            <ChangeBadge value={detail.changePct} />
            <span className={cn('font-mono text-data-m tnum', detail.change >= 0 ? 'text-up-700' : 'text-down-700')}>
              {detail.change >= 0 ? '+' : '−'}{fmtPrice(Math.abs(detail.change))}
            </span>
          </div>
        </div>
        <p className="pb-1.5 text-right font-mono text-micro text-ink-500 tnum">
          成交量 {fmtCompact(detail.volume)} · 市值 ${fmtCompact(detail.marketCap)}
        </p>
      </div>

      <p className="mt-2 text-micro text-ink-400">
        报价更新于 <span className="font-mono tnum">{fmtTimeHHMMSS(new Date(detail.updatedAt))}</span> · 延迟行情
      </p>
    </motion.header>
  );
}

/* ---------------- Tab 头（2px 指示条滑动） ---------------- */
function TabHeader({ tab, onChange }: { tab: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <div role="tablist" className="sticky top-0 z-10 flex gap-1 border-b border-line bg-card/95 backdrop-blur-sm">
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
  const { data } = usePolling(() => breakoutsApi.byTicker(ticker), null, [ticker]);
  const items = (data ?? []).slice(0, 3);
  return (
    <div className="card-surface p-5">
      <p className="eyebrow">BREAKOUT EVENTS</p>
      <h3 className="mt-1.5 text-h3 text-ink-900">相关突破事件</h3>
      {items.length === 0 ? (
        <p className="mt-3 flex items-center gap-2 text-body-s text-ink-400">
          <Icon name="radar" size={16} className="text-ink-300" />
          近 72 小时无事件 · 雷达仍在盯
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
  const { data: detail, loading, error, refresh } = usePolling(() => getDetail(ticker), 60_000, [ticker]);
  const [tab, setTab] = useState<TabKey>('signals');
  const isMobile = useIsMobile();

  useEffect(() => setTab('signals'), [ticker]);

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

  if (error || !detail) {
    const is404 = error?.code === 404;
    return (
      <EmptyState
        variant="error"
        image="/empty-chart.svg"
        title={is404 ? '代码不存在' : '该标的快照不可用'}
        description={is404 ? `${ticker} 不在当前代码池中` : '接口未覆盖此能力，留空而非编造'}
        action={
          is404 ? (
            <Link
              to="/watchlist"
              className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white hover:brightness-105"
            >
              返回自选
            </Link>
          ) : (
            <button
              onClick={refresh}
              className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white hover:brightness-105"
            >
              重试
            </button>
          )
        }
        className="py-16"
      />
    );
  }

  const tabs = (
    <div className={layout === 'page' ? 'mt-8' : 'mt-6'}>
      <TabHeader tab={tab} onChange={setTab} />
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
              <TrendBiasPanel ticker={detail.ticker} />
              <div>
                <p className="eyebrow mb-3">RECENT SIGNALS</p>
                <SignalList ticker={detail.ticker} />
              </div>
              <AiAnalysisCard ticker={detail.ticker} />
            </div>
          )}
          {tab === 'options' && <OptionsPanel ticker={detail.ticker} />}
          {tab === 'news' && <NewsPanel ticker={detail.ticker} />}
        </motion.div>
      </AnimatePresence>
    </div>
  );

  if (layout === 'page') {
    return (
      <div>
        <PriceHeader detail={detail} />
        <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="lg:col-span-8">
            <div className="card-surface p-5">
              <KlineChart ticker={detail.ticker} prevClose={detail.prevClose} height={420} />
            </div>
          </div>
          <aside className="space-y-6 lg:col-span-4">
            <KeyStats detail={detail} />
            <SidebarEvents ticker={detail.ticker} />
          </aside>
        </div>
        {tabs}
        <SourceNote className="mt-8" text="来源：Optix Research · 延迟行情 · 影响分非收益 · 置信度非胜率" />
      </div>
    );
  }

  return (
    <div className="px-5 pb-8 pt-5 md:px-6">
      <PriceHeader detail={detail} />
      <div className="mt-5">
        <KlineChart ticker={detail.ticker} prevClose={detail.prevClose} height={isMobile ? 260 : 320} />
      </div>
      {tabs}
      <SourceNote className="mt-6" />
    </div>
  );
}
