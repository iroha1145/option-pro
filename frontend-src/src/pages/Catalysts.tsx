/**
 * §06 新闻催化剂（catalysts.md 完整实现）
 * 状态 hero · 热点带 · 市场焦点周期 · 标签页（feed/stocks/calendar/sources，URL 同步）
 * 过滤器条（URL query）· 新闻详情抽屉（AI 分析任务状态机）· 空态/骨架/503/移动端
 */
import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { motion } from 'framer-motion';
import PageHeader from '@/components/shared/PageHeader';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtTimeHHMMSS } from '@/lib/format';
import StatusHero from '@/components/catalysts/StatusHero';
import AnalysisProgressCard from '@/components/catalysts/AnalysisProgressCard';
import HotspotsStrip from '@/components/catalysts/HotspotsStrip';
import FocusCycleCard from '@/components/catalysts/FocusCycleCard';
import ManagePanel from '@/components/catalysts/ManagePanel';
import FilterBar from '@/components/catalysts/FilterBar';
import { DEFAULT_FILTERS, type CatalystFilters } from '@/components/catalysts/filters';
import FeedPanel from '@/components/catalysts/FeedPanel';
import StocksPanel from '@/components/catalysts/StocksPanel';
import CalendarPanel from '@/components/catalysts/CalendarPanel';
import SourcesPanel from '@/components/catalysts/SourcesPanel';
import NewsDrawer from '@/components/catalysts/NewsDrawer';
import type { CatalystNewsItem, NewsAnalysisStatus, NewsClassification } from '@/components/catalysts/api';

type TabId = 'feed' | 'stocks' | 'calendar' | 'sources';

const TABS: { id: TabId; label: string }[] = [
  { id: 'feed', label: '新闻流' },
  { id: 'stocks', label: '股票影响' },
  { id: 'calendar', label: '经济日历' },
  { id: 'sources', label: '数据源' },
];

function parseFilters(sp: URLSearchParams): CatalystFilters {
  const w = Number(sp.get('window'));
  return {
    ticker: sp.get('ticker') ?? '',
    windowHours: [6, 24, 72, 168].includes(w) ? w : DEFAULT_FILTERS.windowHours,
    classification: (sp.get('cls') ?? '') as '' | NewsClassification,
    analysisStatus: (sp.get('status') ?? '') as '' | NewsAnalysisStatus,
    minConfidence: Math.min(90, Math.max(0, Number(sp.get('conf') ?? 0))) / 100,
    minAbsImpact: Math.min(5, Math.max(0, Number(sp.get('impact') ?? 0))),
    multiSourceOnly: sp.get('multi') === '1',
    themeId: sp.get('theme'),
  };
}

export default function Catalysts() {
  const [searchParams, setSearchParams] = useSearchParams();

  /* URL 为唯一事实来源 */
  const tab: TabId = useMemo(() => {
    const t = searchParams.get('tab');
    return TABS.some((x) => x.id === t) ? (t as TabId) : 'feed';
  }, [searchParams]);
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);

  const syncUrl = useCallback(
    (f: CatalystFilters, t: TabId) => {
      const p = new URLSearchParams();
      if (t !== 'feed') p.set('tab', t);
      if (f.ticker) p.set('ticker', f.ticker);
      if (f.windowHours !== DEFAULT_FILTERS.windowHours) p.set('window', String(f.windowHours));
      if (f.classification) p.set('cls', f.classification);
      if (f.analysisStatus) p.set('status', f.analysisStatus);
      if (f.minConfidence > 0) p.set('conf', String(Math.round(f.minConfidence * 100)));
      if (f.minAbsImpact > 0) p.set('impact', String(f.minAbsImpact));
      if (f.multiSourceOnly) p.set('multi', '1');
      if (f.themeId) p.set('theme', f.themeId);
      setSearchParams(p, { replace: true });
    },
    [setSearchParams],
  );

  const setTab = useCallback((t: TabId) => syncUrl(filters, t), [filters, syncUrl]);
  const setFilters = useCallback((f: CatalystFilters) => syncUrl(f, tab), [tab, syncUrl]);
  const clearFilters = useCallback(() => syncUrl({ ...DEFAULT_FILTERS }, tab), [tab, syncUrl]);

  /* 页头刷新 */
  const [refreshToken, setRefreshToken] = useState(0);
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [spinning, setSpinning] = useState(false);
  const onRefresh = useCallback(() => {
    setSpinning(true);
    setRefreshToken((v) => v + 1);
    window.setTimeout(() => setSpinning(false), 650);
  }, []);

  /* feed 计数 / 抽屉回写 */
  const [total, setTotal] = useState<number | null>(null);
  const [patches, setPatches] = useState<Record<string, CatalystNewsItem>>({});
  const onNewsUpdate = useCallback((item: CatalystNewsItem) => {
    setPatches((prev) => ({ ...prev, [item.newsId]: item }));
  }, []);
  const onTotalChange = useCallback((n: number | null) => {
    setTotal(n);
    setLastLoadedAt(Date.now());
  }, []);

  /* 新闻详情抽屉 */
  const [selectedNewsId, setSelectedNewsId] = useState<string | null>(null);

  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="06"
        eyebrow="CATALYSTS · NEWS FLOW"
        title="新闻催化剂"
        description="每一条新闻，都是一次重新定价的开始。"
        meta={
          <>
            {lastLoadedAt && (
              <span className="hidden font-mono text-micro text-ink-400 tnum sm:inline" suppressHydrationWarning>
                更新 {fmtTimeHHMMSS(lastLoadedAt)}
              </span>
            )}
            <button
              onClick={onRefresh}
              className="flex h-9 items-center gap-2 rounded-md border border-line bg-card px-3 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
              title="刷新本页数据"
            >
              <Icon name="refresh" size={15} className={spinning ? 'animate-spin-once' : ''} />
              刷新
            </button>
          </>
        }
      />

      {/* 状态 hero：数据源状态 / 热点计算 / 分析可用性 */}
      <StatusHero />

      {/* Owner 专属：任务库真实计数，不以定时补间伪造单条进度 */}
      <AnalysisProgressCard />

      {/* B1 热点主题带（点击卡片打开代表新闻抽屉） */}
      <HotspotsStrip onOpenNews={setSelectedNewsId} />

      {/* B2 市场焦点周期卡 */}
      <div className="mt-6">
        <FocusCycleCard />
      </div>

      {/* B2.5 管理面板（owner 专属：数据刷新 / 后台任务 / 运行设置） */}
      <ManagePanel onDataRefreshed={onRefresh} />


      {/* 标签页（URL 同步 ?tab=） */}
      <div className="no-scrollbar mt-8 flex items-center gap-1 overflow-x-auto border-b border-line" role="tablist" aria-label="催化剂视图">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              'relative shrink-0 whitespace-nowrap px-3.5 py-2.5 text-body-s font-medium transition-colors duration-fast sm:px-4',
              tab === t.id ? 'text-brand-600' : 'text-ink-500 hover:text-ink-800',
            )}
          >
            {t.label}
            {tab === t.id && (
              <motion.span
                layoutId="catalysts-tab-underline"
                className="absolute inset-x-3 bottom-[-1px] h-[2px] rounded-full bg-brand-600"
                transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
              />
            )}
          </button>
        ))}
        {tab === 'feed' && total !== null && (
          <span className="ml-auto shrink-0 pb-1 font-mono text-micro text-ink-400 tnum">{total} 条</span>
        )}
      </div>

      {/* 过滤器条（feed / stocks 共享，写入 URL query） */}
      {(tab === 'feed' || tab === 'stocks') && (
        <FilterBar filters={filters} onChange={setFilters} total={tab === 'feed' ? total : null} filtered={JSON.stringify(filters) !== JSON.stringify(DEFAULT_FILTERS)} />
      )}

      {/* 面板 */}
      <div className="mt-4">
        {tab === 'feed' && (
          <FeedPanel
            filters={filters}
            onOpenNews={setSelectedNewsId}
            patches={patches}
            refreshToken={refreshToken}
            onTotalChange={onTotalChange}
            onClearFilters={clearFilters}
          />
        )}
        {tab === 'stocks' && <StocksPanel filters={filters} refreshToken={refreshToken} />}
        {tab === 'calendar' && <CalendarPanel refreshToken={refreshToken} />}
        {tab === 'sources' && <SourcesPanel refreshToken={refreshToken} />}
      </div>

      {/* 新闻详情抽屉 */}
      <NewsDrawer newsId={selectedNewsId} onClose={() => setSelectedNewsId(null)} onUpdate={onNewsUpdate} />
    </div>
  );
}
