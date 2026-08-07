/**
 * /stock/:ticker 个股研究整页（v2 · 参考日股工作台 StockDetail 卡片重排）
 *
 * 行0: 返回 + S0 价格头 + 手动拉取
 * 行1: K线(8列, 叠加技术点位) + 右栏(4列: 关键数据 / 技术指标 / 相关突破事件)
 * 行2: 趋势偏向 + 近期信号(7列) + K线结构分析(5列)
 * 行3: 宏观适配 + AI 股票分析（AI 卡持有付费任务轮询，常驻挂载不随区块卸载）
 * 行4: 期权链（通栏）
 * 行5: 相关新闻（通栏）
 *
 * 旧抽屉形态已撤：全站 openTicker 一律导航到本页（审计 #12 的任务丢失
 * 问题随抽屉一并消失——本页不再有按 tab 卸载的区块）。
 */
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { usePolling } from '@/hooks/usePolling';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import MacroFitPanel from '@/components/shared/MacroFitPanel';
import InfoHint from '@/components/shared/InfoHint';
import { SkeletonBlock, SkeletonText } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { getDetail, getTechnicalStructure, prefetchStockDetailPanels } from '@/components/detail/api';
import PriceHeader from '@/components/detail/PriceHeader';
import SidebarEvents from '@/components/detail/SidebarEvents';
import KlineChart from '@/components/detail/KlineChart';
import TechnicalPanel from '@/components/detail/TechnicalPanel';
import StructurePanel from '@/components/detail/StructurePanel';
import TrendBiasPanel from '@/components/detail/TrendBiasPanel';
import SignalList from '@/components/detail/SignalList';
import OptionsPanel from '@/components/detail/OptionsPanel';
import NewsPanel from '@/components/detail/NewsPanel';
import AiAnalysisCard from '@/components/detail/AiAnalysisCard';
import ManualStockPull from '@/components/detail/ManualStockPull';
import KeyStats from '@/components/detail/KeyStats';
import { STRUCTURE_HINTS } from '@/lib/structureHints';
import { t, t as __t } from '../i18n/core.ts';

export default function StockDetail() {
  const { ticker = '' } = useParams();
  const symbol = ticker.toUpperCase();
  const navigate = useNavigate();

  const [dataRevision, setDataRevision] = useState(0);
  const forceDetailRef = useRef({ ticker: symbol, force: false });
  const {
    data: detail,
    loading,
    error,
    refreshing,
    refresh,
  } = usePolling(
    () => {
      const force = (
        forceDetailRef.current.ticker === symbol
        && forceDetailRef.current.force
      );
      forceDetailRef.current = { ticker: symbol, force: false };
      return getDetail(symbol, force);
    },
    60_000,
    [symbol, dataRevision],
  );

  /* 技术结构与图表共用同一份日线：独立请求（5 分钟缓存），失败不拖累行情头。
     手动拉取后 force 一次；换标的不继承 force（与 detail 的 forceRef 同口径）。 */
  const forceTechRef = useRef({ ticker: symbol, force: false });
  const techQ = usePolling(
    () => {
      const force = forceTechRef.current.ticker === symbol && forceTechRef.current.force;
      forceTechRef.current = { ticker: symbol, force: false };
      return getTechnicalStructure(symbol, force);
    },
    null,
    [symbol, dataRevision],
  );
  const technical = techQ.data ?? null;

  // 并行预取：详情、K线、趋势偏向、技术结构四路互不依赖，别排成串行
  useEffect(() => {
    prefetchStockDetailPanels(symbol);
  }, [symbol]);

  const handlePulled = () => {
    forceDetailRef.current = { ticker: symbol, force: true };
    forceTechRef.current = { ticker: symbol, force: true };
    setDataRevision((value) => value + 1);
  };

  /* 返回：优先浏览器历史回退（回到雷达/板块/财报等来源页），无入站历史时回自选 */
  const goBack = () => {
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    if (idx > 0) navigate(-1);
    else navigate('/watchlist', { replace: true });
  };

  const backButton = (
    <button
      type="button"
      onClick={goBack}
      className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-card px-3 py-1.5 text-caption font-medium text-ink-600 shadow-btn transition-colors duration-fast hover:bg-paper-2 hover:text-ink-800"
    >
      <Icon name="chevron-right" size={14} className="rotate-180" />
      {__t('返回')}
    </button>
  );

  if (loading) {
    return (
      <div className="space-y-5" aria-busy="true">
        <div className="flex items-center justify-between">
          {backButton}
          <span className="eyebrow">STOCK · ${symbol}</span>
        </div>
        <div className="flex items-center gap-3">
          <SkeletonBlock className="size-10 rounded-sm" />
          <div className="flex-1 space-y-2">
            <SkeletonBlock className="h-4 w-32" />
            <SkeletonBlock className="h-3 w-20" />
          </div>
        </div>
        <SkeletonBlock className="h-12 w-48" />
        <SkeletonBlock className="h-[380px] w-full rounded-md" />
        <SkeletonText lines={4} />
      </div>
    );
  }

  /* 失败但仍有上一轮 detail 时继续展示：一次 408/断网不清空整页 */
  if (!detail) {
    const is404 = error?.code === 404;
    const loginExpired = error?.code === 401;
    const rateLimited = error?.code === 429;
    const publicSnapshotMissing = error?.bizCode === 'public_snapshot_unavailable';
    const manualRecovery = publicSnapshotMissing || (!error && !detail);
    return (
      <div>
        <div className="mb-6 flex items-center justify-between">
          {backButton}
          <span className="eyebrow">STOCK · ${symbol}</span>
        </div>
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
              ? t('{ticker} 不在当前股票目录中', { ticker: symbol })
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
              <ManualStockPull ticker={symbol} onPulled={handlePulled} />
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
      </div>
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

  const techFallback = techQ.loading && !technical ? (
    <div className="mt-3 space-y-2" aria-hidden="true">
      <span className="skeleton-shimmer block h-4 w-full rounded-xs" />
      <span className="skeleton-shimmer block h-4 w-3/4 rounded-xs" />
    </div>
  ) : techQ.error && !technical ? (
    <p className="mt-3 flex items-center gap-2 text-body-s text-ink-400">
      <Icon name="doc-quote" size={16} className="text-ink-300" />
      {__t('技术结构读取失败 · 不代表该股没有结构')}
      <button
        onClick={() => techQ.refresh()}
        className="ml-auto rounded-md border border-line px-2 py-0.5 text-micro text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
      >
        {__t('重试')}
      </button>
    </p>
  ) : null;

  const providerNote = [
    __t('行情为延迟数据'),
    __t('影响分表示新闻方向，不是收益预测'),
    __t('置信度是模型的把握程度，不是胜率'),
  ].join(' · ');

  return (
    <div>
      {/* 行0 */}
      <div className="mb-6 flex items-center justify-between">
        {backButton}
        <span className="eyebrow">STOCK · ${symbol}</span>
      </div>
      <PriceHeader detail={detail} />
      {scopeBanner}
      {detail.snapshotScope !== 'strength-row' && (
        <ManualStockPull ticker={detail.ticker} onPulled={handlePulled} compact className="mt-3" />
      )}

      {/* 行1: K线 + 右栏 */}
      <div className="mt-8 grid grid-cols-1 gap-6 xl:grid-cols-12">
        <div className="xl:col-span-8">
          <div className="card-surface p-5">
            <KlineChart
              ticker={detail.ticker}
              prevClose={detail.prevClose}
              height={420}
              refreshVersion={dataRevision}
              overlays={technical?.chart_overlays ?? null}
            />
            <p className="mt-2 flex items-center gap-1 text-micro text-ink-400">
              {__t('图例')}
              <InfoHint hint={STRUCTURE_HINTS.chart_overlays} align="start" size={12} />
              <span className="ml-1">{__t('阻力带 / 失效位 / 摆动点按日线结构自动标注')}</span>
            </p>
          </div>
        </div>
        <aside className="grid content-start gap-6 xl:col-span-4">
          <KeyStats detail={detail} />
          <div className="card-surface p-5">
            <p className="eyebrow">TECHNICALS</p>
            <h3 className="mt-1.5 text-h3 text-ink-900">{__t('技术指标')}</h3>
            {technical ? <TechnicalPanel technical={technical} /> : techFallback ?? <TechnicalPanel technical={null} />}
          </div>
          <SidebarEvents ticker={detail.ticker} />
        </aside>
      </div>

      {/* 行2: 趋势偏向 + 信号(7) · K线结构(5) */}
      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-12">
        <div className="card-surface p-5 xl:col-span-7">
          <p className="eyebrow">TREND BIAS · SIGNALS</p>
          <h3 className="mb-4 mt-1.5 text-h3 text-ink-900">{__t('趋势偏向与近期信号')}</h3>
          <TrendBiasPanel ticker={detail.ticker} refreshVersion={dataRevision} />
          <div className="mt-6">
            <p className="eyebrow mb-3">RECENT SIGNALS</p>
            <SignalList ticker={detail.ticker} refreshVersion={dataRevision} />
          </div>
        </div>
        <div className="card-surface p-5 xl:col-span-5">
          <p className="eyebrow">CHART STRUCTURE</p>
          <h3 className="mt-1.5 text-h3 text-ink-900">{__t('K线结构分析')}</h3>
          {technical ? <StructurePanel technical={technical} /> : techFallback ?? <StructurePanel technical={null} />}
        </div>
      </div>

      {/* 行3: 宏观适配 + AI 分析 */}
      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-12">
        <div className="card-surface p-5 xl:col-span-5">
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
        <div className="xl:col-span-7">
          <AiAnalysisCard key={detail.ticker} ticker={detail.ticker} />
        </div>
      </div>

      {/* 行4: 期权链 */}
      <div className="card-surface mt-6 p-5">
        <p className="eyebrow">OPTIONS CHAIN</p>
        <h3 className="mb-4 mt-1.5 text-h3 text-ink-900">{__t('期权链')}</h3>
        <OptionsPanel ticker={detail.ticker} />
      </div>

      {/* 行5: 相关新闻 */}
      <div className="card-surface mt-6 p-5">
        <p className="eyebrow">RELATED NEWS</p>
        <h3 className="mb-4 mt-1.5 text-h3 text-ink-900">{__t('相关新闻')}</h3>
        <NewsPanel ticker={detail.ticker} />
      </div>

      <SourceNote className="mt-8" text={providerNote} />
    </div>
  );
}
