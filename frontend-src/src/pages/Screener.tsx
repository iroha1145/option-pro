/**
 * §02 选股扫描（screener.md 完整实现）
 * B0 页头带（上次扫描 / 扫描历史 popover / owner strength_refresh）
 * B1 筛选工作台（周期/偏好/分档/预设/板块/价格/成交额/TopN + 进度形变扫描钮）
 * B2 结果区（统计行 + 参数回显 chips + 三态排序 Segmented + 结果表/卡片流 + 行展开）
 * B3 右侧栏（市场形态 6 维 / 强度剖面 / 评分方法 / 空结果引导）
 * 状态：未扫描 empty-scan.svg · 扫描中骨架 · 无命中 · 503 快照不可用（保留上次结果）
 * 数据：strengthApi.scan / market / profilesMeta + catalystsApi.batchSummaries72h（单次批量）+ stocksApi.detail（成交额推导）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { strengthApi, type ScanParams, type StrengthScanEnvelope } from '@/api/modules/strength';
import { catalystsApi } from '@/api/modules/catalysts';
import { stocksApi } from '@/api/modules/stocks';
import { runtimeApi, type StrengthRefreshParameters } from '@/api/modules/runtime';
import { ApiError, isMock } from '@/api/client';
import type { ScreenerRow, SectorOption, Signal, StrengthProfile } from '@/api/types';
import { usePolling } from '@/hooks/usePolling';
import { useAccess } from '@/hooks/useAccess';
import { useCountUp } from '@/hooks/useCountUp';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtTimeHHMMSS } from '@/lib/format';
import Icon from '@/components/icons';
import PageHeader from '@/components/shared/PageHeader';
import Segmented from '@/components/shared/Segmented';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonCard, SkeletonRows } from '@/components/shared/Skeleton';
import FilterWorkbench from '@/components/screener/FilterWorkbench';
import MarketRegimeCard from '@/components/screener/MarketRegimeCard';
import ResultTable from '@/components/screener/ResultTable';
import ResultCards from '@/components/screener/ResultCards';
import ScanHistoryPopover from '@/components/screener/ScanHistoryPopover';
import { MethodCard, TierHistogram } from '@/components/screener/SideCards';
import {
  DEFAULT_FILTERS,
  DOLLAR_VOL_OPTIONS,
  EMPTY_CATALYST,
  PROFILE_CN,
  SORT_CN,
  TIMEFRAME_CN,
  countByTier,
  tierOf,
  type CatalystSummary,
  type DetailCache,
  type ScanFilters,
  type ScanHistoryEntry,
  type SortMode,
  type Tier,
  type TierFilter,
} from '@/components/screener/types';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];
const PAGE_SIZE = 20;

type ScanState = 'idle' | 'scanning' | 'done' | 'error';

/* 预设策略 → 偏好映射 + 强度下限（契约枚举 conservative/balanced/aggressive 直接落偏好） */
function withPreset(base: ScanFilters, id: string): ScanFilters {
  if (id === 'conservative' || id === 'balanced' || id === 'aggressive') {
    return { ...base, presetId: id, profile: id, minScore: null };
  }
  if (id === 'breakout') return { ...base, presetId: id, profile: 'aggressive', minScore: 70 };
  if (id === 'lowvol') return { ...base, presetId: id, profile: 'conservative', minScore: null };
  return { ...base, presetId: id, profile: 'balanced', minScore: null };
}

function filtersEqual(a: ScanFilters, b: ScanFilters): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function strengthParametersMatch(actual: unknown, expected: StrengthRefreshParameters): boolean {
  if (!actual || typeof actual !== 'object') return false;
  const value = actual as Record<string, unknown>;
  return (
    value.universe === expected.universe &&
    value.timeframe === expected.timeframe &&
    value.profile === expected.profile &&
    value.top === expected.top &&
    value.sector_id === expected.sector_id &&
    value.min_price === expected.min_price &&
    value.min_avg_dollar_volume === expected.min_avg_dollar_volume &&
    value.include_options === expected.include_options
  );
}

export default function Screener() {
  const { isOwner } = useAccess();
  const { openTicker } = useShell();
  const toast = useToast();

  /* ---------------- 基础数据（消费层） ---------------- */
  const universeQ = usePolling(() => strengthApi.scanEnvelope({ band: 'all', sort: 'score', order: 'desc' }), null);
  const marketQ = usePolling(() => strengthApi.market(), 300_000);
  const profilesQ = usePolling(() => strengthApi.profilesMeta(), null);
  const profiles = profilesQ.data?.profiles ?? null;

  const universe = useMemo(() => {
    const snapshotRows = universeQ.data?.rows ?? [];
    return {
      tierCounts: countByTier(snapshotRows.map((r) => r.strengthScore)),
      sectors: [...new Set(snapshotRows.map((r) => r.sector))].sort((a, b) => a.localeCompare(b, 'zh-CN')),
      count: universeQ.data?.universeCount ?? snapshotRows.length,
    };
  }, [universeQ.data]);

  /* 板块选项：live 取 /strength/profiles 的板块字典（id+中文名，扫描下发 id）；mock 回退扫描行 sector 名 */
  const sectorOptions = useMemo<SectorOption[]>(() => {
    const fromMeta = profilesQ.data?.sectors ?? [];
    if (fromMeta.length > 0) return fromMeta;
    return universe.sectors.map((s) => ({ id: s, name: s }));
  }, [profilesQ.data, universe.sectors]);

  /* ---------------- 扫描状态机 ---------------- */
  const [draft, setDraft] = useState<ScanFilters>(DEFAULT_FILTERS);
  const [applied, setApplied] = useState<ScanFilters>(DEFAULT_FILTERS);
  const [scanState, setScanState] = useState<ScanState>('idle');
  const [rows, setRows] = useState<ScreenerRow[] | null>(null);
  const [scanError, setScanError] = useState<ApiError | null>(null);
  const [scanMeta, setScanMeta] = useState<StrengthScanEnvelope | null>(null);
  const [progress, setProgress] = useState(0);
  const [lastScanAt, setLastScanAt] = useState<number | null>(null);
  const [scanDurationMs, setScanDurationMs] = useState(0);
  const [history, setHistory] = useState<ScanHistoryEntry[]>([]);
  const [sortMode, setSortMode] = useState<SortMode>('deterministic');
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [flashes, setFlashes] = useState<Record<string, 'up' | 'down'>>({});
  const [refreshingStrength, setRefreshingStrength] = useState(false);

  const [details, setDetails] = useState<DetailCache>({});
  const detailsRef = useRef<DetailCache>({});
  const [catalysts, setCatalysts] = useState<Record<string, CatalystSummary>>({});
  const catalystsRef = useRef<Record<string, CatalystSummary>>({});
  const [signalsMap, setSignalsMap] = useState<Record<string, Signal[]>>({});
  const signalsRef = useRef<Record<string, Signal[]>>({});
  const scanSeq = useRef(0);

  const dirty = scanState === 'done' && !filtersEqual(draft, applied);

  /* ---------------- 扫描进度补间（形变进度条） ---------------- */
  useEffect(() => {
    if (scanState !== 'scanning') return;
    setProgress(0);
    const t0 = Date.now();
    const id = setInterval(() => {
      const el = Date.now() - t0;
      setProgress(Math.min(92, Math.round(92 * (1 - Math.exp(-el / 420)))));
    }, 90);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanState, scanSeq.current]);

  /* ---------------- 执行扫描 ---------------- */
  const runScan = useCallback(async (filters: ScanFilters) => {
    const seq = ++scanSeq.current;
    setScanState('scanning');
    setScanError(null);
    const startedAt = Date.now();
    // 仅演示数据保留可见扫描过程；真实接口完成后立即呈现结果。
    const minMs = isMock ? 800 + Math.random() * 700 : 0;
    try {
      // 后端必须先返回足够大的真实候选集，再应用只存在于客户端的条件；
      // 否则先截 TopN 会漏掉价格上限、多板块、分档或最低分过滤后的合资格股票。
      const hasClientOnlyNarrowing =
        filters.priceMax != null ||
        filters.sectors.length > 1 ||
        filters.tier !== 'all' ||
        filters.minScore != null;
      const apiTop = filters.topN <= 0 || hasClientOnlyNarrowing ? 120 : filters.topN;
      const params = {
        band: 'all',
        sort: 'score',
        order: 'desc',
        universe: 'themes',
        timeframe: filters.timeframe,
        profile: filters.profile,
        top: apiTop,
        sector_id: filters.sectors.length === 1 ? filters.sectors[0] : undefined,
        min_price: filters.priceMin ?? 0,
        min_avg_dollar_volume: filters.minDollarVol,
        include_options: true,
        ...(filters.minScore != null ? { minScore: filters.minScore } : {}),
      } as ScanParams;

      // Owner 扫描先提交精确参数。后端可能复用另一个仍在执行/冷却中的扫描；
      // 只有返回参数完全一致时才等待并读取快照，避免把旧参数结果冒充为本次刷新。
      if (isOwner) {
        const requested: StrengthRefreshParameters = {
          universe: 'themes',
          timeframe: filters.timeframe,
          profile: filters.profile,
          top: apiTop,
          sector_id: filters.sectors.length === 1 ? filters.sectors[0] : null,
          min_price: filters.priceMin ?? 0,
          min_avg_dollar_volume: filters.minDollarVol,
          include_options: true,
        };
        const action = await runtimeApi.workerAction('strength_refresh', requested);
        if (!strengthParametersMatch(action.details.parameters, requested)) {
          throw new ApiError(409, '另一组筛选条件正在扫描或冷却，请稍后重试', {
            bizCode: 'strength_parameters_busy',
            payload: action,
          });
        }
        if (!action.requestId) throw new ApiError(502, '后台扫描未返回 request_id');
        if (action.status !== 'completed') await runtimeApi.waitForWorkerAction(action.requestId);
      }
      const result = await strengthApi.scanEnvelope(params);
      const elapsed = Date.now() - startedAt;
      if (elapsed < minMs) await new Promise((r) => setTimeout(r, minMs - elapsed));
      if (scanSeq.current !== seq) return;
      const durationMs = Date.now() - startedAt;
      setRows((prev) => {
        if (prev) {
          const prevMap = new Map(prev.map((r) => [r.ticker, r.price]));
          const f: Record<string, 'up' | 'down'> = {};
          result.rows.forEach((r) => {
            const p = prevMap.get(r.ticker);
            if (p !== undefined && p !== r.price) f[r.ticker] = r.price > p ? 'up' : 'down';
          });
          if (Object.keys(f).length) {
            setFlashes(f);
            setTimeout(() => setFlashes({}), 700);
          }
        }
        return result.rows;
      });
      setScanMeta(result);
      const detailPatch: DetailCache = Object.fromEntries(
        result.rows.map((row) => [row.ticker, { dollarVolume: row.avgDollarVolume20d ?? null }]),
      );
      detailsRef.current = { ...detailsRef.current, ...detailPatch };
      setDetails(detailsRef.current);
      setApplied(filters);
      setDraft(filters);
      setLastScanAt(Date.now());
      setScanDurationMs(durationMs);
      setPage(1);
      setExpanded(null);
      setProgress(100);
      setScanState('done');
      setHistory((h) => [{ at: Date.now(), count: result.rows.length, durationMs, summary: summarizeFilters(filters) }, ...h].slice(0, 5));
      return true;
    } catch (e) {
      if (scanSeq.current !== seq) return;
      setScanError(e instanceof ApiError ? e : new ApiError(500, e instanceof Error ? e.message : '扫描失败'));
      setScanState('error');
      return false;
    }
  }, [isOwner]);

  /* 成交额直接使用后端 avg_dollar_volume_20d，不再逐股请求并用当日成交额冒充。 */

  /* ---------------- 过滤（applied 快照，客户端） ---------------- */
  const filteredBase = useMemo(() => {
    if (!rows) return [];
    const f = applied;
    let out = rows;
    // f.sectors 存板块 id（live 契约 sector_id / mock 回退 name）：按 id 或名双向匹配
    if (f.sectors.length > 0) out = out.filter((r) => f.sectors.includes(r.sectorId ?? r.sector) || f.sectors.includes(r.sector));
    if (f.priceMin != null) out = out.filter((r) => r.price >= (f.priceMin ?? 0));
    if (f.priceMax != null) out = out.filter((r) => r.price <= (f.priceMax ?? Infinity));
    if (f.minScore != null) out = out.filter((r) => r.strengthScore >= (f.minScore ?? 0));
    if (f.minDollarVol > 0) {
      out = out.filter((r) => {
        const dollarVolume = r.avgDollarVolume20d;
        return dollarVolume !== null && dollarVolume !== undefined && dollarVolume >= f.minDollarVol;
      });
    }
    return out;
  }, [rows, applied]);

  const filtered = useMemo(() => {
    let out = filteredBase;
    if (applied.tier !== 'all') out = out.filter((r) => tierOf(r.strengthScore) === applied.tier);
    if (applied.topN > 0) out = out.slice(0, applied.topN);
    return out;
  }, [filteredBase, applied.tier, applied.topN]);

  /* ---------------- 三态排序（deterministic / latest / impact） ---------------- */
  const sorted = useMemo(() => {
    const out = [...filtered];
    const byScore = (a: ScreenerRow, b: ScreenerRow) =>
      b.strengthScore - a.strengthScore || Math.abs(b.changePct ?? 0) - Math.abs(a.changePct ?? 0) || a.ticker.localeCompare(b.ticker);
    if (sortMode === 'deterministic') {
      out.sort(byScore);
    } else if (sortMode === 'latest') {
      const ts = (r: ScreenerRow) => {
        const c = catalysts[r.ticker];
        return c?.latestAt ? new Date(c.latestAt).getTime() : -1;
      };
      out.sort((a, b) => ts(b) - ts(a) || byScore(a, b));
    } else {
      const impact = (r: ScreenerRow) => {
        const c = catalysts[r.ticker];
        return c ? c.pos - c.neg : 0;
      };
      const count = (r: ScreenerRow) => catalysts[r.ticker]?.count ?? 0;
      out.sort((a, b) => impact(b) - impact(a) || count(b) - count(a) || byScore(a, b));
    }
    return out;
  }, [filtered, sortMode, catalysts]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageRows = useMemo(
    () => sorted.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [sorted, safePage],
  );

  /* ---------------- 催化剂 72h 汇总（当前页 ≤20 只一次批量 POST；禁止逐行请求） ---------------- */
  const pageTickerKey = pageRows.map((r) => r.ticker).join(',');
  useEffect(() => {
    if (scanState !== 'done') return;
    const tickers = pageTickerKey.split(',').filter((t) => t && catalystsRef.current[t] === undefined);
    if (tickers.length === 0) return;
    let cancelled = false;
    void (async () => {
      const patch: Record<string, CatalystSummary> = {};
      try {
        const map = await catalystsApi.batchSummaries72h(tickers);
        tickers.forEach((t) => {
          const s = map[t];
          // 响应缺该股条目视作真实 0（契约 batch 对无新闻股返回空 items，同义）
          patch[t] = s ? { loaded: true, ...s } : { ...EMPTY_CATALYST, loaded: true };
        });
      } catch {
        // 批量接口失败：failed 标记 → 徽标如实显「—」（不显 0，不逐行重试）
        tickers.forEach((t) => {
          patch[t] = { ...EMPTY_CATALYST, loaded: true, failed: true };
        });
      }
      if (cancelled) return;
      catalystsRef.current = { ...catalystsRef.current, ...patch };
      setCatalysts(catalystsRef.current);
    })();
    return () => {
      cancelled = true;
    };
  }, [scanState, pageTickerKey]);

  /* ---------------- 行展开 + 信号懒加载 ---------------- */
  const onToggle = useCallback((ticker: string) => {
    setExpanded((prev) => {
      const next = prev === ticker ? null : ticker;
      if (next && signalsRef.current[next] === undefined) {
        stocksApi
          .signals(next)
          .then((sg) => {
            signalsRef.current = { ...signalsRef.current, [next]: sg };
            setSignalsMap(signalsRef.current);
          })
          .catch(() => {
            signalsRef.current = { ...signalsRef.current, [next]: [] };
            setSignalsMap(signalsRef.current);
          });
      }
      return next;
    });
  }, []);

  /* ---------------- owner：strength_refresh ---------------- */
  const onStrengthRefresh = useCallback(async () => {
    setRefreshingStrength(true);
    try {
      const ok = await runScan(applied);
      if (!ok) {
        toast.error('刷新失败', '后台扫描未完成，请查看结果区错误');
        return;
      }
      toast.success('强度扫描完成', '已读取 worker 生成的精确参数快照');
      universeQ.refresh();
      marketQ.refresh();
    } catch (e) {
      toast.error('触发失败', e instanceof ApiError ? e.message : 'worker 不可用');
    } finally {
      setRefreshingStrength(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applied, runScan, toast]);

  /* ---------------- 交互回调 ---------------- */
  const onScanClick = useCallback(() => void runScan(draft), [draft, runScan]);

  const patchApplied = useCallback((p: Partial<ScanFilters>) => {
    setApplied((a) => {
      const next = { ...a, ...p };
      setDraft(next);
      return next;
    });
    setPage(1);
  }, []);

  const onTierFromHistogram = useCallback((t: TierFilter) => {
    setDraft((d) => ({ ...d, tier: t, presetId: null, minScore: null }));
    setApplied((a) => ({ ...a, tier: t, presetId: null, minScore: null }));
    setPage(1);
  }, []);

  const onPresetQuick = useCallback(
    (id: string) => {
      const f = withPreset(draft, id);
      void runScan(f);
    },
    [draft, runScan],
  );

  /* ---------------- 派生展示数据 ---------------- */
  const hitCount = useCountUp(filtered.length, 900);
  const hitsByTier = useMemo(() => {
    const acc: Record<Tier, number> = { S: 0, A: 0, B: 0, C: 0, D: 0 };
    filteredBase.forEach((r) => {
      acc[tierOf(r.strengthScore)] += 1;
    });
    return acc;
  }, [filteredBase]);

  const activeProfile: StrengthProfile | null = useMemo(() => {
    if (!profiles || profiles.length === 0) return null;
    return profiles.find((p) => p.id === applied.presetId) ?? profiles[0];
  }, [profiles, applied.presetId]);

  const chips = useMemo(
    () => buildChips(applied, profiles, sectorOptions, patchApplied),
    [applied, profiles, sectorOptions, patchApplied],
  );

  const animKey = `${scanSeq.current}:${safePage}`;

  /* ================= 渲染 ================= */
  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="02"
        eyebrow="SCREENER · STRENGTH SCAN"
        title="选股扫描"
        description="基于真实行情接口扫描主题股票池，快照来源与时效可追溯。"
        meta={
          <>
            <span className="hidden text-right sm:block">
              <span className="block text-micro text-ink-400">上次扫描</span>
              <span className="font-mono text-caption text-ink-600 tnum" suppressHydrationWarning>
                {lastScanAt ? fmtTimeHHMMSS(lastScanAt) : '—'}
              </span>
            </span>
            <ScanHistoryPopover history={history} />
            {isOwner && (
              <button
                onClick={() => void onStrengthRefresh()}
                disabled={refreshingStrength || scanState === 'scanning'}
                title="触发 worker strength_refresh（owner）"
                className="flex h-9 items-center gap-2 rounded-md border border-line bg-card px-3 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Icon name="refresh" size={15} className={refreshingStrength ? 'animate-spin-once' : ''} />
                刷新强度分
              </button>
            )}
          </>
        }
      />

      {/* B1 筛选工作台 */}
      <div className="mt-6">
        <FilterWorkbench
          draft={draft}
          onChange={setDraft}
          universe={universe}
          sectorOptions={sectorOptions}
          presets={profiles}
          presetsFailed={!!profilesQ.error}
          scanning={scanState === 'scanning'}
          progress={progress}
          dirty={dirty}
          onScan={onScanClick}
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* B2 结果区（8 列） */}
        <section className="lg:col-span-8" aria-label="扫描结果">
          {/* 结果统计行 */}
          <div className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-2">
            {scanState === 'scanning' ? (
              <span className="flex items-center gap-2 text-body-s text-ink-500">
                <span className="size-3.5 animate-spin rounded-full border-2 border-brand-600/25 border-t-brand-600" aria-hidden="true" />
                正在扫描…
              </span>
            ) : scanState === 'done' || (scanState === 'error' && rows) ? (
              <>
                <h2 className="font-display text-[18px] leading-[24px] text-ink-900">
                  命中 <span className="font-mono tnum">{Math.round(hitCount)}</span> 只
                </h2>
                <span className="font-mono text-caption text-ink-400 tnum">· 耗时 {(scanDurationMs / 1000).toFixed(1)}s</span>
                {scanMeta && (
                  <span className="font-mono text-micro text-ink-400 tnum">
                    · 股票池 {scanMeta.universeCount} · 已评分 {scanMeta.screenedCount}
                    {scanMeta.priceProvider ? ` · ${scanMeta.priceProvider}` : ''}
                  </span>
                )}
                {scanMeta?.stale && (
                  <span className="rounded-xs bg-warn-50 px-1.5 py-px text-micro text-warn-600">
                    过期快照{scanMeta.snapshotSavedAt ? ` · ${new Date(scanMeta.snapshotSavedAt).toLocaleString('zh-CN')}` : ''}
                  </span>
                )}
                {chips.map((c) => (
                  <span
                    key={c.key}
                    className="inline-flex items-center gap-1 rounded-xs border border-line bg-card-warm px-1.5 py-0.5 text-micro text-ink-500"
                  >
                    {c.label}
                    <button onClick={c.onRemove} aria-label={`移除条件 ${c.label}`} className="text-ink-300 transition-colors hover:text-down-600">
                      <Icon name="x" size={10} />
                    </button>
                  </span>
                ))}
              </>
            ) : (
              <h2 className="font-display text-[18px] leading-[24px] text-ink-900">扫描结果</h2>
            )}
            <div className="ml-auto">
              <Segmented<SortMode>
                options={(['deterministic', 'latest', 'impact'] as const).map((v) => ({ value: v, label: SORT_CN[v] }))}
                value={sortMode}
                onChange={setSortMode}
              />
            </div>
          </div>

          {/* 结果主体 */}
          <div className="mt-4">
            {scanState === 'idle' ? (
              /* 未扫描空态 */
              <div className="card-surface">
                <EmptyState
                  image="/empty-scan.svg"
                  title="设定条件，开始一次扫描"
                  description="或从预设策略一键开始"
                  action={
                    <div className="flex flex-col items-center gap-3">
                      <button
                        onClick={onScanClick}
                        className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                      >
                        <Icon name="crosshair" size={14} />
                        开始扫描
                      </button>
                      {profiles && (
                        <div className="flex flex-wrap justify-center gap-2">
                          {profiles.map((p) => (
                            <button
                              key={p.id}
                              onClick={() => onPresetQuick(p.id)}
                              className="flex h-8 items-center gap-1.5 rounded-pill border border-line bg-card px-3 text-caption text-ink-500 transition-colors duration-fast hover:border-brand-400/60 hover:text-brand-600"
                            >
                              <Icon name="spark-ai" size={13} className="text-ink-300" />
                              {p.name}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  }
                />
              </div>
            ) : scanState === 'scanning' && !rows ? (
              /* 扫描中骨架 */
              <div className="card-surface">
                <SkeletonRows rows={8} />
              </div>
            ) : scanState === 'error' ? (
              /* 503 / 错误态：保留上次成功结果（已过期角标） */
              <>
                <div className="card-surface">
                  <EmptyState
                    variant="error"
                    image="/empty-chart.svg"
                    title={scanError?.code === 503 ? '扫描快照不可用' : '扫描失败'}
                    description={scanError?.code === 503 ? '留空优于编造' : scanError?.message}
                    action={
                      <button
                        onClick={() => void runScan(applied)}
                        className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                      >
                        重试
                      </button>
                    }
                  />
                </div>
                {rows && (
                  <div className="mt-4">
                    <p className="mb-2 flex items-center gap-2 text-caption text-ink-400">
                      <span className="rounded-xs bg-warn-50 px-1.5 py-px font-mono text-micro text-warn-600">已过期</span>
                      上次成功扫描于 <span className="font-mono tnum">{lastScanAt ? fmtTimeHHMMSS(lastScanAt) : '—'}</span>
                    </p>
                    <div className="hidden md:block">
                      <ResultTable
                        rows={pageRows}
                        startIndex={(safePage - 1) * PAGE_SIZE}
                        page={safePage}
                        totalPages={totalPages}
                        onPageChange={setPage}
                        expanded={expanded}
                        onToggle={onToggle}
                        catalysts={catalysts}
                        details={details}
                        flashes={flashes}
                        weights={activeProfile?.weights ?? null}
                        signals={signalsMap}
                        onOpenDetail={openTicker}
                        animKey={animKey}
                        stale
                      />
                    </div>
                  </div>
                )}
              </>
            ) : sorted.length === 0 ? (
              /* 无命中 */
              <div className="card-surface">
                <EmptyState
                  icon="search"
                  title="当前条件无命中"
                  description="尝试放宽条件，或移除部分过滤器"
                  action={
                    <div className="flex flex-wrap justify-center gap-2">
                      {(applied.tier !== 'all' || applied.minScore != null) && (
                        <SuggestButton label="放宽一档" onClick={() => patchApplied({ tier: 'all', minScore: null, presetId: null })} />
                      )}
                      {applied.sectors.length > 0 && <SuggestButton label="清除板块" onClick={() => patchApplied({ sectors: [] })} />}
                      {(applied.priceMin != null || applied.priceMax != null) && (
                        <SuggestButton label="清除价格区间" onClick={() => patchApplied({ priceMin: null, priceMax: null })} />
                      )}
                      {applied.minDollarVol > 0 && <SuggestButton label="清除成交额下限" onClick={() => patchApplied({ minDollarVol: 0 })} />}
                      <SuggestButton label="重置全部条件" onClick={() => patchApplied({ ...DEFAULT_FILTERS })} />
                    </div>
                  }
                />
              </div>
            ) : (
              /* 正常结果 */
              <>
                <div className={cn('hidden md:block', scanState === 'scanning' && 'opacity-60')}>
                  <ResultTable
                    rows={pageRows}
                    startIndex={(safePage - 1) * PAGE_SIZE}
                    page={safePage}
                    totalPages={totalPages}
                    onPageChange={setPage}
                    expanded={expanded}
                    onToggle={onToggle}
                    catalysts={catalysts}
                    details={details}
                    flashes={flashes}
                    weights={activeProfile?.weights ?? null}
                    signals={signalsMap}
                    onOpenDetail={openTicker}
                    animKey={animKey}
                  />
                </div>
                <div className={cn('md:hidden', scanState === 'scanning' && 'opacity-60')}>
                  <ResultCards
                    rows={pageRows}
                    expanded={expanded}
                    onToggle={onToggle}
                    catalysts={catalysts}
                    details={details}
                    weights={activeProfile?.weights ?? null}
                    signals={signalsMap}
                    onOpenDetail={openTicker}
                    animKey={animKey}
                  />
                  {/* 移动端分页 */}
                  {totalPages > 1 && (
                    <div className="mt-4 flex items-center justify-center gap-2">
                      <PagerButton disabled={safePage <= 1} onClick={() => setPage(safePage - 1)} label="上一页" />
                      <span className="font-mono text-caption text-ink-500 tnum">
                        {safePage} / {totalPages}
                      </span>
                      <PagerButton disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)} label="下一页" />
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </section>

        {/* B3 右侧栏（4 列吸顶；768–1023 双列落入 B2 下） */}
        <aside
          className="grid grid-cols-1 gap-4 self-start md:grid-cols-2 lg:sticky lg:top-[116px] lg:col-span-4 lg:grid-cols-1"
          aria-label="侧栏"
        >
          {marketQ.data ? (
            <MarketRegimeCard market={marketQ.data} />
          ) : marketQ.error ? (
            <div className="card-surface p-5">
              <p className="eyebrow">市场形态 · MARKET REGIME</p>
              <p className="mt-3 text-body-s text-ink-500">{marketQ.error.code === 503 ? '快照暂不可用 · 留空优于编造' : marketQ.error.message}</p>
              <button
                onClick={marketQ.refresh}
                className="mt-3 flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-caption text-ink-600 transition-colors hover:border-brand-400 hover:text-brand-600"
              >
                <Icon name="refresh" size={13} />
                重试
              </button>
            </div>
          ) : (
            <SkeletonCard />
          )}
          <TierHistogram hits={hitsByTier} market={marketQ.data} activeTier={draft.tier} onSelect={onTierFromHistogram} />
          <MethodCard profile={activeProfile} />
          <AnimatePresence>
            {scanState === 'done' && sorted.length === 0 && (
              <motion.div
                key="relax"
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, transition: { duration: 0.16 } }}
                transition={{ duration: 0.48, ease: EASE_PAPER }}
                className="card-surface p-5"
              >
                <p className="eyebrow">无命中引导</p>
                <p className="mt-2.5 text-body-s text-ink-500">当前条件过严，没有标的进入结果集。</p>
                <button
                  onClick={() => patchApplied({ tier: 'all', minScore: null, presetId: null })}
                  className="mt-3 flex items-center gap-1.5 rounded-md bg-brand-600 px-3 py-1.5 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                >
                  <Icon name="filter-funnel" size={13} />
                  放宽一档试试
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </aside>
      </div>
    </div>
  );
}

/* ---------------- 参数回显 chips ---------------- */
interface EchoChip {
  key: string;
  label: string;
  onRemove: () => void;
}

function buildChips(
  f: ScanFilters,
  profiles: StrengthProfile[] | null,
  sectorOptions: SectorOption[],
  patch: (p: Partial<ScanFilters>) => void,
): EchoChip[] {
  const chips: EchoChip[] = [];
  if (f.tier !== 'all') chips.push({ key: 'tier', label: `${f.tier} 档`, onRemove: () => patch({ tier: 'all' }) });
  if (f.timeframe !== 'all') chips.push({ key: 'tf', label: `周期 ${TIMEFRAME_CN[f.timeframe]}`, onRemove: () => patch({ timeframe: 'all' }) });
  if (f.profile !== 'balanced') chips.push({ key: 'pf', label: `偏好 ${PROFILE_CN[f.profile]}`, onRemove: () => patch({ profile: 'balanced' }) });
  if (f.topN > 0) chips.push({ key: 'top', label: `Top ${f.topN}`, onRemove: () => patch({ topN: 0 }) });
  f.sectors.forEach((s) =>
    chips.push({
      key: `sec-${s}`,
      // sectors 存 id：回显中文名（mock id=name 等价）
      label: sectorOptions.find((o) => o.id === s)?.name ?? s,
      onRemove: () => patch({ sectors: f.sectors.filter((x) => x !== s) }),
    }),
  );
  if (f.priceMin != null || f.priceMax != null) {
    const lo = f.priceMin != null ? `$${f.priceMin}` : '—';
    const hi = f.priceMax != null ? `$${f.priceMax}` : '—';
    chips.push({ key: 'price', label: `价格 ${lo}–${hi}`, onRemove: () => patch({ priceMin: null, priceMax: null }) });
  }
  if (f.minDollarVol > 0) {
    const opt = DOLLAR_VOL_OPTIONS.find((o) => o.value === f.minDollarVol);
    chips.push({ key: 'dv', label: `成交额 ${opt?.label ?? `≥${fmtCompact(f.minDollarVol)}`}`, onRemove: () => patch({ minDollarVol: 0 }) });
  }
  if (f.minScore != null) chips.push({ key: 'ms', label: `强度 ≥${f.minScore}`, onRemove: () => patch({ minScore: null }) });
  if (f.presetId) {
    const name = profiles?.find((p) => p.id === f.presetId)?.name ?? f.presetId;
    chips.push({ key: 'preset', label: `预设 ${name}`, onRemove: () => patch({ presetId: null }) });
  }
  return chips;
}

function summarizeFilters(f: ScanFilters): string {
  const parts: string[] = [];
  if (f.tier !== 'all') parts.push(`${f.tier} 档`);
  if (f.timeframe !== 'all') parts.push(TIMEFRAME_CN[f.timeframe]);
  parts.push(PROFILE_CN[f.profile]);
  if (f.topN > 0) parts.push(`Top ${f.topN}`);
  if (f.sectors.length > 0) parts.push(`板块 ${f.sectors.length} 项`);
  if (f.priceMin != null || f.priceMax != null) parts.push('价格区间');
  if (f.minDollarVol > 0) parts.push(`成交额≥${fmtCompact(f.minDollarVol)}`);
  if (f.minScore != null) parts.push(`强度≥${f.minScore}`);
  return parts.join(' · ') || '默认条件';
}

function SuggestButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex h-8 items-center rounded-pill border border-line bg-card px-3 text-caption text-ink-500 transition-colors duration-fast hover:border-brand-400/60 hover:text-brand-600"
    >
      {label}
    </button>
  );
}

function PagerButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="flex h-8 items-center rounded-md border border-line bg-card px-3 text-caption text-ink-600 transition-colors hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {label}
    </button>
  );
}
