/**
 * §06 新闻催化剂（catalysts.md 完整实现）
 * 状态 hero · 热点带 · 市场焦点周期 · 标签页（feed/stocks/calendar/sources，URL 同步）
 * 过滤器条（URL query）· 新闻详情抽屉（AI 分析任务状态机）· 空态/骨架/503/移动端
 */
import { startTransition, useCallback, useMemo, useOptimistic, useState } from 'react';
import { useSearchParams } from 'react-router';
import PageHeader from '@/components/shared/PageHeader';
import Segmented from '@/components/shared/Segmented';
import Icon from '@/components/icons';
import { fmtTimeHHMMSS } from '@/lib/format';
import StatusHero from '@/components/catalysts/StatusHero';
import AnalysisProgressCard from '@/components/catalysts/AnalysisProgressCard';
import HotspotsStrip from '@/components/catalysts/HotspotsStrip';
import FocusCycleCard from '@/components/catalysts/FocusCycleCard';
import ManagePanel from '@/components/catalysts/ManagePanel';
import FilterBar from '@/components/catalysts/FilterBar';
import { DEFAULT_FILTERS, sanitizeThemeId, type CatalystFilters } from '@/components/catalysts/filters';
import FeedPanel from '@/components/catalysts/FeedPanel';
import StocksPanel from '@/components/catalysts/StocksPanel';
import CalendarPanel from '@/components/catalysts/CalendarPanel';
import SourcesPanel from '@/components/catalysts/SourcesPanel';
import NewsDrawer from '@/components/catalysts/NewsDrawer';
import { clearCatalystReadCache } from '@/components/catalysts/api';
import type { CatalystNewsItem, NewsAnalysisStatus, NewsClassification } from '@/components/catalysts/api';
import { t as __t } from '../i18n/core.ts';

type TabId = 'feed' | 'stocks' | 'calendar' | 'sources';

const TABS: { id: TabId; label: string }[] = [
  { id: 'feed', label: __t('新闻流') },
  { id: 'stocks', label: __t('股票影响') },
  { id: 'calendar', label: __t('经济日历') },
  { id: 'sources', label: __t('数据源') },
];

/* URL 参数必须逐项校验（审计 P2-23）：分类与状态此前用强制类型断言直接透传，
   数值经 Number() 后未过滤 NaN —— 损坏的书签或手写查询串会让筛选器进入 UI
   无法正常生成的状态。 */
const CLASSIFICATIONS: readonly NewsClassification[] = ['bullish', 'bearish', 'neutral'];
const ANALYSIS_STATUSES: readonly NewsAnalysisStatus[] = [
  'pending',
  'queued',
  'in_progress',
  'completed',
  'insufficient_context',
  'failed',
];

/** 落在 [min,max] 内的有限数；缺失或非法一律回退到 fallback，NaN 不得通过。 */
function boundedNumber(raw: string | null, min: number, max: number, fallback: number): number {
  if (raw === null || raw.trim() === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, value));
}

function oneOf<T extends string>(raw: string | null, allowed: readonly T[]): '' | T {
  return raw !== null && (allowed as readonly string[]).includes(raw) ? (raw as T) : '';
}

function parseFilters(sp: URLSearchParams): CatalystFilters {
  const w = Number(sp.get('window'));
  return {
    ticker: (sp.get('ticker') ?? '').slice(0, 12),
    windowHours: [6, 24, 72, 168].includes(w) ? w : DEFAULT_FILTERS.windowHours,
    classification: oneOf(sp.get('cls'), CLASSIFICATIONS),
    analysisStatus: oneOf(sp.get('status'), ANALYSIS_STATUSES),
    minConfidence: boundedNumber(sp.get('conf'), 0, 90, 0) / 100,
    minAbsImpact: boundedNumber(sp.get('impact'), 0, 5, 0),
    multiSourceOnly: sp.get('multi') === '1',
    themeId: sanitizeThemeId(sp.get('theme')),
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
  // 输入须立即回显；地址导航可能延后提交，不能用旧参数覆盖正在键入的字符。
  // 列表仍读取已提交的地址参数，输入反馈随同一次导航自动收敛。
  const [inputFilters, setInputFilters] = useOptimistic(filters);

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

  const setTab = useCallback((t: TabId) => syncUrl(inputFilters, t), [inputFilters, syncUrl]);
  const setFilters = useCallback((f: CatalystFilters) => {
    startTransition(() => {
      setInputFilters(f);
      syncUrl(f, tab);
    });
  }, [setInputFilters, syncUrl, tab]);
  const clearFilters = useCallback(() => setFilters({ ...DEFAULT_FILTERS }), [setFilters]);

  /* 页头刷新 */
  const [refreshToken, setRefreshToken] = useState(0);
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [spinning, setSpinning] = useState(false);
  const onRefresh = useCallback(() => {
    setSpinning(true);
    clearCatalystReadCache(); // 手动刷新必须穿透客户端读缓存
    setRefreshToken((v) => v + 1);
    window.setTimeout(() => setSpinning(false), 650);
  }, []);

  /* feed 计数 / 抽屉回写 */
  const [total, setTotal] = useState<number | null>(null);
  const [patches, setPatches] = useState<Record<string, CatalystNewsItem>>({});
  const onNewsUpdate = useCallback((item: CatalystNewsItem) => {
    setPatches((prev) => ({ ...prev, [item.newsId]: item }));
  }, []);
  /* 只有真正成功的一轮才更新时间戳（审计 P2-22）：旧实现在失败分支也调用
     onTotalChange(null)，于是用户看到一个很新的更新时间，而本轮数据根本没加载成功。 */
  const onFeedResult = useCallback((result: { total: number | null; ok: boolean }) => {
    setTotal(result.total);
    if (result.ok) setLastLoadedAt(Date.now());
  }, []);

  /* 新闻详情抽屉 */
  const [selectedNewsId, setSelectedNewsId] = useState<string | null>(null);

  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="06"
        eyebrow="CATALYSTS · NEWS FLOW"
        title={__t("新闻催化剂")}
        description={__t("每一条新闻，都是一次重新定价的开始。")}
        meta={
          <>
            {lastLoadedAt && (
              <span className="hidden font-mono text-micro text-ink-400 tnum sm:inline" suppressHydrationWarning>
                {__t('更新')} {fmtTimeHHMMSS(lastLoadedAt)}
              </span>
            )}
            <button
              onClick={onRefresh}
              className="flex h-9 items-center gap-2 rounded-md border border-line bg-card px-3 text-caption text-ink-600 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
              title={__t("刷新本页数据")}
            >
              <Icon name="refresh" size={15} className={spinning ? 'animate-spin-once' : ''} />
              {__t('刷新')}
            </button>
          </>
        }
      />

      {/* 状态 hero：数据源状态 / 热点计算 / 分析可用性
          刷新令牌此前只传给四个标签内容，顶部采集状态、热点带与焦点周期不会
          立即重新加载，按钮文案与实际刷新范围不一致（审计 P2-21）。 */}
      <StatusHero refreshToken={refreshToken} />

      {/* Owner 专属：任务库真实计数，不以定时补间伪造单条进度 */}
      <AnalysisProgressCard />

      {/* B1 热点主题带（点击卡片打开代表新闻抽屉） */}
      <HotspotsStrip onOpenNews={setSelectedNewsId} refreshToken={refreshToken} />

      {/* B2 市场焦点周期卡 */}
      <div className="mt-6">
        <FocusCycleCard refreshToken={refreshToken} />
      </div>

      {/* B2.5 管理面板（owner 专属：数据刷新 / 后台任务 / 运行设置） */}
      <ManagePanel onDataRefreshed={onRefresh} />


      {/* 标签页（URL 同步 ?tab=） */}
      <div className="mt-8 min-w-0">
        <Segmented
          options={TABS.map(({ id, label }) => ({ value: id, label }))}
          value={tab}
          onChange={setTab}
          ariaLabel={__t('催化剂视图')}
          scrollable
        />
      </div>

      {/* 过滤器条（feed / stocks 共享，写入 URL query） */}
      {(tab === 'feed' || tab === 'stocks') && (
        <FilterBar filters={inputFilters} onChange={setFilters} total={tab === 'feed' ? total : null} filtered={JSON.stringify(inputFilters) !== JSON.stringify(DEFAULT_FILTERS)} />
      )}

      {/* 面板 */}
      <div className="mt-4">
        {tab === 'feed' && (
          <FeedPanel
            filters={filters}
            onOpenNews={setSelectedNewsId}
            patches={patches}
            refreshToken={refreshToken}
            onFeedResult={onFeedResult}
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
