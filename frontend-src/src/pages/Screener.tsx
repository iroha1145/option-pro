/**
 * §02 选股扫描（screener.md 完整实现）
 * B0 页头带（上次扫描 / 扫描历史 popover / owner strength_refresh）
 * B1 筛选工作台（周期/偏好/分档/预设/板块/价格/成交额/TopN + 真实等待态扫描钮）
 * B2 结果区（统计行 + 参数回显 chips + 三态排序 Segmented + 结果表/卡片流 + 行展开）
 * B3 右侧栏（市场形态 6 维 / 强度剖面 / 评分方法 / 空结果引导）
 * 状态：未扫描 empty-scan.svg · 扫描中骨架 · 无命中 · 503 快照不可用（保留上次结果）
 * 数据：strengthApi.scan / market / profilesMeta + catalystsApi.batchSummaries72h（单次批量）+ signalsApi.stock
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { strengthApi, type StrengthScanEnvelope } from '@/api/modules/strength';
import { catalystsApi } from '@/api/modules/catalysts';
import { signalsApi } from '@/api/modules/signals';
import { runtimeApi, type StrengthRefreshParameters } from '@/api/modules/runtime';
import { ApiError, isMock } from '@/api/client';
import type { ScreenerRow, SectorOption, StrengthProfile } from '@/api/types';
import { usePolling } from '@/hooks/usePolling';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { useShell } from '@/components/Layout';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtTimeHHMMSS } from '@/lib/format';
import {
  MACRO_SHADOW_HINT,
  MACRO_TONE_LABEL,
  macroToneOf,
  type MacroTone,
} from '@/lib/macroFit';
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
import { buildStrengthScanRequest } from '@/components/screener/scanRequest';
import {
  DEFAULT_FILTERS,
  DOLLAR_VOL_OPTIONS,
  EMPTY_CATALYST,
  PROFILE_CN,
  SORT_CN,
  TIMEFRAME_CN,
  catalystSummaryUsable,
  countByTier,
  tierOf,
  type CatalystSummary,
  type RowSignalsState,
  type DetailCache,
  type ScanFilters,
  type ScanHistoryEntry,
  type SortMode,
  type Tier,
  type TierFilter,
} from '@/components/screener/types';
import { t as __t } from '../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];
const PAGE_SIZE = 20;
/** 契约 /catalysts/tickers/batch 的匿名上限；超过就必须切片。 */
const CATALYST_BATCH_SIZE = 20;

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

  /**
   * 分档计数必须描述候选池，而不是这次返回的这几行（审计 P2-10）。
   *
   * 初次调用不传 top，后端默认只返回 20 条；旧实现拿这 20 行统计 S/A/B/C/D，却在
   * 旁边展示真实股票池数量，于是出现「股票池 300 只，五档加起来 20 只」。后端现在
   * 在截取 top 之前统计整池分布；旧快照没有该字段时回退到当前行并如实标注。
   */
  const universe = useMemo(() => {
    const snapshotRows = universeQ.data?.rows ?? [];
    const distribution = universeQ.data?.tierDistribution ?? null;
    return {
      tierCounts: distribution
        ? {
            all: distribution.total,
            S: distribution.S,
            A: distribution.A,
            B: distribution.B,
            C: distribution.C,
          }
        : countByTier(snapshotRows.map((r) => r.strengthScore)),
      tierCountsCoverPool: distribution !== null,
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
  const [lastScanAt, setLastScanAt] = useState<number | null>(null);
  const [scanDurationMs, setScanDurationMs] = useState(0);
  const [history, setHistory] = useState<ScanHistoryEntry[]>([]);
  const [sortMode, setSortMode] = useState<SortMode>('deterministic');
  // 宏观适配是**可选列**，默认关：它是影子字段，不该占据默认视图。开关同时决定
  // 是否显示分档筛选 —— 一个不显示的列没法解释筛掉的行是按什么筛的。
  const [showMacro, setShowMacro] = useState(false);
  const [macroToneFilter, setMacroToneFilter] = useState<MacroTone | 'all'>('all');
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [flashes, setFlashes] = useState<Record<string, 'up' | 'down'>>({});
  const [refreshingStrength, setRefreshingStrength] = useState(false);

  const [details, setDetails] = useState<DetailCache>({});
  const detailsRef = useRef<DetailCache>({});
  const [catalysts, setCatalysts] = useState<Record<string, CatalystSummary>>({});
  const catalystsRef = useRef<Record<string, CatalystSummary>>({});
  const [signalsMap, setSignalsMap] = useState<Record<string, RowSignalsState>>({});
  const signalsRef = useRef<Record<string, RowSignalsState>>({});
  const scanSeq = useRef(0);

  const dirty = scanState === 'done' && !filtersEqual(draft, applied);

  /* ---------------- 执行扫描 ---------------- */
  const runScan = useCallback(async (
    filters: ScanFilters,
    options: { forceRefresh?: boolean } = {},
  ) => {
    const seq = ++scanSeq.current;
    setScanState('scanning');
    setScanError(null);
    const startedAt = Date.now();
    // 仅演示数据保留可见扫描过程；真实接口完成后立即呈现结果。
    const minMs = isMock ? 800 + Math.random() * 700 : 0;
    try {
      const { apiParams: params, refreshParameters: requested } = buildStrengthScanRequest(filters);

      const refreshSnapshot = async () => {
        const action = await runtimeApi.workerAction('strength_refresh', requested);
        if (!strengthParametersMatch(action.details.parameters, requested)) {
          throw new ApiError(409, __t('另一组筛选条件正在扫描或冷却，请稍后重试'), {
            bizCode: 'strength_parameters_busy',
            payload: action,
          });
        }
        if (!action.requestId) throw new ApiError(502, __t('扫描任务未能启动'));
        if (action.status !== 'completed') await runtimeApi.waitForWorkerAction(action.requestId);
      };

      // 普通“扫描”只读取后台已生成的精确参数快照。仅在该快照确实缺失时，
      // Owner 才补触发一次 worker；“刷新强度分”按钮则明确执行强制刷新。
      if (isOwner && !isMock && options.forceRefresh) {
        await refreshSnapshot();
      }
      let result: StrengthScanEnvelope;
      try {
        result = await strengthApi.scanEnvelope(params, Boolean(options.forceRefresh));
      } catch (error) {
        const snapshotMissing =
          error instanceof ApiError
          && error.code === 503
          && error.bizCode === 'strength_snapshot_unavailable';
        if (!isOwner || isMock || options.forceRefresh || !snapshotMissing) throw error;
        await refreshSnapshot();
        result = await strengthApi.scanEnvelope(params, true);
      }
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
      setScanState('done');
      setHistory((h) => [{ at: Date.now(), count: result.rows.length, durationMs, summary: summarizeFilters(filters) }, ...h].slice(0, 5));
      return true;
    } catch (e) {
      if (scanSeq.current !== seq) return;
      setScanError(e instanceof ApiError ? e : new ApiError(500, e instanceof Error ? e.message : __t('扫描失败')));
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
    // 宏观分档筛选。刻意在 tier 之后、topN 之前，和其他客户端条件同一层。
    //
    // 没有宏观读数的行会被这个筛选**排除**，而不是当成中性留下 —— 留下就等于宣称
    // 它是中性，而后端返回 null 正是为了不说这句话。上面的提示会说明少了多少行。
    if (macroToneFilter !== 'all') {
      out = out.filter((r) => macroToneOf(r.macroFit, r.macroTailwind) === macroToneFilter);
    }
    if (applied.topN > 0) out = out.slice(0, applied.topN);
    return out;
  }, [filteredBase, applied.tier, applied.topN, macroToneFilter]);

  /**
   * 催化排序的前置条件（审计 P1-06）。
   *
   * 旧实现存在循环依赖：先排序 → 得到当前页 → 只为当前页拉催化摘要 → 再排序。
   * 未访问过的股票默认时间 -1、影响分 0，于是同一份数据会因为用户翻过第几页而
   * 得到不同排名；真正有重大新闻但初始强度不在第一页的股票永远进不了第一页。
   *
   * 因此：切到催化排序时先为全部候选股票批量取摘要，取齐之前不参与正式排名。
   */
  /** 宏观筛选打开时被排除的行数：算在 tier 之后、宏观之前的那一层上。 */
  const macroUnreadCount = useMemo(() => {
    const tierPool =
      applied.tier === 'all'
        ? filteredBase
        : filteredBase.filter((r) => tierOf(r.strengthScore) === applied.tier);
    return tierPool.filter((r) => macroToneOf(r.macroFit, r.macroTailwind) === null).length;
  }, [filteredBase, applied.tier]);

  const catalystSortActive = sortMode !== 'deterministic';
  const missingCatalystTickers = useMemo(() => {
    if (!catalystSortActive) return [];
    const now = Date.now();
    return filtered
      .map((row) => row.ticker)
      .filter((ticker) => !catalystSummaryUsable(catalysts[ticker], now));
  }, [catalystSortActive, filtered, catalysts]);
  const preparingCatalystSort = catalystSortActive && missingCatalystTickers.length > 0;

  /* ---------------- 三态排序（deterministic / latest / impact） ---------------- */
  const sorted = useMemo(() => {
    const out = [...filtered];
    const byScore = (a: ScreenerRow, b: ScreenerRow) =>
      b.strengthScore - a.strengthScore || Math.abs(b.changePct ?? 0) - Math.abs(a.changePct ?? 0) || a.ticker.localeCompare(b.ticker);
    // 摘要没取齐就维持确定性顺序：用缺失值排名会让结果取决于访问过哪些分页。
    if (sortMode === 'deterministic' || preparingCatalystSort) {
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
  }, [filtered, sortMode, catalysts, preparingCatalystSort]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageRows = useMemo(
    () => sorted.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [sorted, safePage],
  );

  /* ---------------- 催化剂 72h 汇总（每批 ≤20 只；禁止逐行请求） ---------------- */
  /** 分批抓取并合并；contract 每次最多 20 只，因此按 20 切片顺序发出。 */
  const loadCatalystSummaries = useCallback(
    async (tickers: string[], isCancelled: () => boolean) => {
      for (let offset = 0; offset < tickers.length; offset += CATALYST_BATCH_SIZE) {
        if (isCancelled()) return;
        const slice = tickers.slice(offset, offset + CATALYST_BATCH_SIZE);
        const fetchedAt = Date.now();
        const patch: Record<string, CatalystSummary> = {};
        try {
          const map = await catalystsApi.batchSummaries72h(slice);
          slice.forEach((t) => {
            const s = map[t];
            // 响应缺该股条目视作真实 0（契约 batch 对无新闻股返回空 items，同义）
            patch[t] = s
              ? { loaded: true, fetchedAt, ...s }
              : { ...EMPTY_CATALYST, loaded: true, fetchedAt };
          });
        } catch {
          // 批量接口失败：failed 标记 → 徽标如实显「—」（不显 0，不逐行重试）
          slice.forEach((t) => {
            patch[t] = { ...EMPTY_CATALYST, loaded: true, failed: true, fetchedAt };
          });
        }
        if (isCancelled()) return;
        catalystsRef.current = { ...catalystsRef.current, ...patch };
        setCatalysts(catalystsRef.current);
      }
    },
    [],
  );

  const pageTickerKey = pageRows.map((r) => r.ticker).join(',');
  useEffect(() => {
    if (scanState !== 'done') return;
    const now = Date.now();
    const tickers = pageTickerKey
      .split(',')
      .filter((t) => t && !catalystSummaryUsable(catalystsRef.current[t], now));
    if (tickers.length === 0) return;
    let cancelled = false;
    void loadCatalystSummaries(tickers, () => cancelled);
    return () => {
      cancelled = true;
    };
  }, [scanState, pageTickerKey, loadCatalystSummaries]);

  /* 催化排序：为全部候选股票取齐摘要，而不是只取当前页（审计 P1-06）。 */
  const missingCatalystKey = missingCatalystTickers.join(',');
  useEffect(() => {
    if (scanState !== 'done' || !catalystSortActive || !missingCatalystKey) return;
    let cancelled = false;
    void loadCatalystSummaries(missingCatalystKey.split(','), () => cancelled);
    return () => {
      cancelled = true;
    };
  }, [scanState, catalystSortActive, missingCatalystKey, loadCatalystSummaries]);

  /* ---------------- 行展开 + 信号懒加载 ---------------- */
  const onToggle = useCallback((ticker: string) => {
    setExpanded((prev) => {
      const next = prev === ticker ? null : ticker;
      // 失败不再写入空数组（审计 P2-13）：那与「真实没有信号」无法区分，而且会
      // 永久占住该键，以后展开同一股票也不会重试。
      if (next && signalsRef.current[next] === undefined) {
        signalsRef.current = { ...signalsRef.current, [next]: { state: 'loading' } };
        setSignalsMap(signalsRef.current);
        signalsApi
          .stock(next)
          .then((sg) => {
            signalsRef.current = {
              ...signalsRef.current,
              [next]: { state: 'success', signals: sg },
            };
            setSignalsMap(signalsRef.current);
          })
          .catch(() => {
            signalsRef.current = { ...signalsRef.current, [next]: { state: 'error' } };
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
      const ok = await runScan(applied, { forceRefresh: true });
      if (!ok) {
        toast.error(__t('刷新失败'), __t('扫描未完成，请查看结果区的错误提示'));
        return;
      }
      toast.success(__t('强度扫描完成'), __t('已读取最新扫描结果'));
      universeQ.refresh();
      marketQ.refresh();
    } catch (e) {
      toast.error(__t('触发失败'), e instanceof ApiError ? e.message : __t('扫描服务暂不可用'));
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
  /* count-up 减量：命中数直接呈现终值 */
  const hitCount = filtered.length;

  /**
   * 客户端筛选的真实作用范围（审计 P1-05）。
   *
   * 后端把 top 硬限在 120，价格上限 / 多板块 / 分档 / 最低分都是客户端条件；
   * 已评分候选超过返回行数时，这些条件只在前 N 名内生效，界面必须说清楚，
   * 而不是让用户以为扫描是完整的。
   */
  const truncatedScope = useMemo(() => {
    if (!scanMeta || rows === null) return null;
    const returned = scanMeta.rows.length;
    if (scanMeta.screenedCount <= returned) return null;
    return { returned, screened: scanMeta.screenedCount };
  }, [scanMeta, rows]);
  const hitsByTier = useMemo(() => {
    const acc: Record<Tier, number> = { S: 0, A: 0, B: 0, C: 0, D: 0 };
    filteredBase.forEach((r) => {
      acc[tierOf(r.strengthScore)] += 1;
    });
    return acc;
  }, [filteredBase]);

  /**
   * 评分说明必须描述实际使用的评分风格（审计 P2-11）。
   *
   * 实际扫描用的是 applied.profile；旧实现却按 applied.presetId 查找，presetId 为空
   * （手动切换风格、未选预设）时直接回落到 profiles[0]，于是选了「进取」或「稳健」，
   * 下面的评分方法与权重说明仍属于第一套配置。预设编号只用于识别快捷预设。
   */
  const activeProfile: StrengthProfile | null = useMemo(() => {
    if (!profiles || profiles.length === 0) return null;
    return (
      profiles.find((p) => p.id === applied.profile)
      ?? profiles.find((p) => p.id === applied.presetId)
      ?? null
    );
  }, [profiles, applied.profile, applied.presetId]);

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
        title={__t("选股扫描")}
        description={__t("按真实行情扫描主题股票池，可查看扫描时间与股票池规模。")}
        meta={
          <>
            <span className="hidden text-right sm:block">
              <span className="block text-micro text-ink-400">{__t('上次扫描')}</span>
              <span className="font-mono text-caption text-ink-600 tnum" suppressHydrationWarning>
                {lastScanAt ? fmtTimeHHMMSS(lastScanAt) : '—'}
              </span>
            </span>
            <ScanHistoryPopover history={history} />
            {isOwner && (
              <button
                onClick={() => void onStrengthRefresh()}
                disabled={refreshingStrength || scanState === 'scanning'}
                title={__t("手动触发一次强度扫描（需 Owner）")}
                className="flex h-9 items-center gap-2 rounded-md border border-line bg-card px-3 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Icon name="refresh" size={15} className={refreshingStrength ? 'animate-spin-once' : ''} />
                {__t('刷新强度分')}
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
          dirty={dirty}
          onScan={onScanClick}
        />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* B2 结果区（8 列） */}
        <section className="lg:col-span-8" aria-label={__t("扫描结果")}>
          {/* 结果统计行 */}
          <div className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-2">
            {scanState === 'scanning' ? (
              <span className="flex items-center gap-2 text-body-s text-ink-500">
                <span className="size-3.5 animate-spin rounded-full border-2 border-brand-600/25 border-t-brand-600" aria-hidden="true" />
                {__t('正在扫描…')}
              </span>
            ) : scanState === 'done' || (scanState === 'error' && rows) ? (
              <>
                <h2 className="font-display text-[18px] leading-[24px] text-ink-900">
                  {__t('命中')} <span className="font-mono tnum">{hitCount}</span> {__t('只')}
                </h2>
                <span className="font-mono text-caption text-ink-400 tnum">{__t('耗时')} {(scanDurationMs / 1000).toFixed(1)}s</span>
                {scanMeta && (
                  <span className="font-mono text-micro text-ink-400 tnum">
                    {__t('股票池')} {scanMeta.universeCount} {__t('/ 已评分')} {scanMeta.screenedCount}
                  </span>
                )}
                {/* 客户端条件只作用在后端返回的强度前 N 名上（审计 P1-05）：
                    后端把 top 硬限在 120，因此候选池更大时结果不是全市场筛选。 */}
                {truncatedScope && (
                  <span
                    className="rounded-xs bg-warn-50 px-1.5 py-px text-micro text-warn-600"
                    title={__t('价格上限、多板块、分档与最低分是客户端条件，只能作用在后端返回的这 {returned} 行上；已评分候选共 {screened} 只。', { returned: truncatedScope.returned, screened: truncatedScope.screened })}
                  >
                    {__t('仅在强度前')} {truncatedScope.returned} {__t('名内筛选')}
                  </span>
                )}
                {preparingCatalystSort && (
                  <span className="inline-flex items-center gap-1.5 rounded-xs bg-paper-2 px-1.5 py-px text-micro text-ink-500">
                    <span className="size-2.5 animate-spin rounded-full border-[1.5px] border-brand-600/25 border-t-brand-600" aria-hidden="true" />
                    {__t('正在准备排序数据 · 剩余')} {missingCatalystTickers.length}
                  </span>
                )}
                {scanMeta?.stale && (
                  <span className="rounded-xs bg-warn-50 px-1.5 py-px text-micro text-warn-600">
                    {__t('数据未刷新')}{scanMeta.snapshotSavedAt ? ` · ${new Date(scanMeta.snapshotSavedAt).toLocaleString('zh-CN')}` : ''}
                  </span>
                )}
                {chips.map((c) => (
                  <span
                    key={c.key}
                    className="inline-flex items-center gap-1 rounded-xs border border-line bg-card-warm px-1.5 py-0.5 text-micro text-ink-500"
                  >
                    {c.label}
                    <button onClick={c.onRemove} aria-label={__t('移除条件 {label}', { label: c.label })} className="text-ink-300 transition-colors hover:text-down-600">
                      <Icon name="x" size={10} />
                    </button>
                  </span>
                ))}
              </>
            ) : (
              <h2 className="font-display text-[18px] leading-[24px] text-ink-900">{__t('扫描结果')}</h2>
            )}
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {/* 可选列开关。关掉时同时清掉宏观筛选：否则行会按一个看不见的条件被筛掉。 */}
              <button
                type="button"
                aria-pressed={showMacro}
                onClick={() => {
                  setShowMacro((on) => {
                    if (on) setMacroToneFilter('all');
                    return !on;
                  });
                  setPage(1);
                }}
                title={__t('{title}：{body}{note}', { title: __t(MACRO_SHADOW_HINT.title), body: __t(MACRO_SHADOW_HINT.body), note: __t(MACRO_SHADOW_HINT.note) })}
                className={cn(
                  'flex h-8 items-center gap-1.5 rounded-pill border px-3 text-caption transition-colors duration-fast',
                  showMacro
                    ? 'border-brand-600 bg-brand-100 font-medium text-brand-700'
                    : 'border-line bg-card text-ink-500 hover:border-line-strong hover:text-ink-800',
                )}
              >
                <Icon name="layers" size={13} />
                {__t('宏观适配')}
              </button>
              {showMacro && (
                <Segmented<MacroTone | 'all'>
                  options={[
                    { value: 'all' as const, label: __t('全部') },
                    { value: 'tailwind' as const, label: MACRO_TONE_LABEL.tailwind },
                    { value: 'neutral' as const, label: MACRO_TONE_LABEL.neutral },
                    { value: 'headwind' as const, label: MACRO_TONE_LABEL.headwind },
                  ]}
                  value={macroToneFilter}
                  onChange={(value) => {
                    setMacroToneFilter(value);
                    setPage(1);
                  }}
                />
              )}
              <Segmented<SortMode>
                options={(['deterministic', 'latest', 'impact'] as const).map((v) => ({ value: v, label: SORT_CN[v] }))}
                value={sortMode}
                onChange={setSortMode}
              />
            </div>
          </div>
          {/* 宏观筛选会排除没有读数的行；数量说清楚，别让人以为那些股票不存在。 */}
          {showMacro && macroToneFilter !== 'all' && macroUnreadCount > 0 && (
            <p className="mt-2 text-micro text-ink-400">
              {macroUnreadCount} {__t('只无宏观读数，已排除（不按中性计）')}
            </p>
          )}

          {/* 结果主体 */}
          <div className="mt-4">
            {scanState === 'idle' ? (
              /* 未扫描空态 */
              <div className="card-surface">
                <EmptyState
                  image="/empty-scan.svg"
                  title={__t("设定条件，开始一次扫描")}
                  description={__t("或从预设策略一键开始")}
                  action={
                    <div className="flex flex-col items-center gap-3">
                      <button
                        onClick={onScanClick}
                        className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                      >
                        <Icon name="crosshair" size={14} />
                        {__t('开始扫描')}
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
                    title={scanError?.code === 503 ? __t('扫描数据不可用') : __t('扫描失败')}
                    description={scanError?.code === 503 ? __t('稍后刷新再试') : scanError?.message}
                    action={
                      <button
                        onClick={() => void runScan(applied)}
                        className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                      >
                        {__t('重试')}
                      </button>
                    }
                  />
                </div>
                {rows && (
                  <div className="mt-4">
                    <p className="mb-2 flex items-center gap-2 text-caption text-ink-400">
                      <span className="rounded-xs bg-warn-50 px-1.5 py-px font-mono text-micro text-warn-600">{__t('已过期')}</span>
                      {__t('上次成功扫描于')} <span className="font-mono tnum">{lastScanAt ? fmtTimeHHMMSS(lastScanAt) : '—'}</span>
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
                        showMacro={showMacro}
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
                  title={__t("当前条件无命中")}
                  description={__t("尝试放宽条件，或移除部分过滤器")}
                  action={
                    <div className="flex flex-wrap justify-center gap-2">
                      {(applied.tier !== 'all' || applied.minScore != null) && (
                        <SuggestButton label={__t("放宽一档")} onClick={() => patchApplied({ tier: 'all', minScore: null, presetId: null })} />
                      )}
                      {applied.sectors.length > 0 && <SuggestButton label={__t("清除板块")} onClick={() => patchApplied({ sectors: [] })} />}
                      {(applied.priceMin != null || applied.priceMax != null) && (
                        <SuggestButton label={__t("清除价格区间")} onClick={() => patchApplied({ priceMin: null, priceMax: null })} />
                      )}
                      {applied.minDollarVol > 0 && <SuggestButton label={__t("清除成交额下限")} onClick={() => patchApplied({ minDollarVol: 0 })} />}
                      <SuggestButton label={__t("重置全部条件")} onClick={() => patchApplied({ ...DEFAULT_FILTERS })} />
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
                    showMacro={showMacro}
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
                    page={safePage}
                    /* 移动端也要给：漏掉它的话「宏观顺风」筛选照样生效、列表照样
                       被过滤，但卡片上一个宏观读数都不显示 —— 用户看不出这些票
                       为什么留下来了。 */
                    showMacro={showMacro}
                  />
                  {/* 移动端分页 */}
                  {totalPages > 1 && (
                    <div className="mt-4 flex items-center justify-center gap-2">
                      <PagerButton disabled={safePage <= 1} onClick={() => setPage(safePage - 1)} label={__t("上一页")} />
                      <span className="font-mono text-caption text-ink-500 tnum">
                        {safePage} / {totalPages}
                      </span>
                      <PagerButton disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)} label={__t("下一页")} />
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
          aria-label={__t("侧栏")}
        >
          {marketQ.data ? (
            <MarketRegimeCard market={marketQ.data} />
          ) : marketQ.error ? (
            <div className="card-surface p-5">
              <p className="eyebrow">{__t('市场形态 · MARKET REGIME')}</p>
              <p className="mt-3 text-body-s text-ink-500">{marketQ.error.code === 503 ? __t('数据暂不可用 · 稍后刷新再试') : marketQ.error.message}</p>
              <button
                onClick={marketQ.refresh}
                className="mt-3 flex items-center gap-1.5 rounded-md border border-line px-3 py-1.5 text-caption text-ink-600 transition-colors hover:border-brand-400 hover:text-brand-600"
              >
                <Icon name="refresh" size={13} />
                {__t('重试')}
              </button>
            </div>
          ) : (
            <SkeletonCard />
          )}
          <TierHistogram hits={hitsByTier} market={marketQ.data} activeTier={draft.tier} onSelect={onTierFromHistogram} />
          <MethodCard
            profile={activeProfile}
            loading={profilesQ.loading}
            error={!!profilesQ.error}
            onRetry={profilesQ.refresh}
          />
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
                <p className="eyebrow">{__t('无命中引导')}</p>
                <p className="mt-2.5 text-body-s text-ink-500">{__t('当前条件过严，没有标的进入结果集。')}</p>
                <button
                  onClick={() => patchApplied({ tier: 'all', minScore: null, presetId: null })}
                  className="mt-3 flex items-center gap-1.5 rounded-md bg-brand-600 px-3 py-1.5 text-caption font-medium text-white transition-[filter] hover:brightness-105"
                >
                  <Icon name="filter-funnel" size={13} />
                  {__t('放宽一档试试')}
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
  if (f.tier !== 'all') chips.push({ key: 'tier', label: __t('{tier} 档', { tier: f.tier }), onRemove: () => patch({ tier: 'all' }) });
  if (f.timeframe !== 'all') chips.push({ key: 'tf', label: __t('周期 {tf}', { tf: TIMEFRAME_CN[f.timeframe] }), onRemove: () => patch({ timeframe: 'all' }) });
  if (f.profile !== 'balanced') chips.push({ key: 'pf', label: __t('偏好 {profile}', { profile: PROFILE_CN[f.profile] }), onRemove: () => patch({ profile: 'balanced' }) });
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
    chips.push({ key: 'price', label: __t('价格 {lo}–{hi}', { lo, hi }), onRemove: () => patch({ priceMin: null, priceMax: null }) });
  }
  if (f.minDollarVol > 0) {
    const opt = DOLLAR_VOL_OPTIONS.find((o) => o.value === f.minDollarVol);
    chips.push({ key: 'dv', label: __t('成交额 {v}', { v: opt?.label ?? `≥${fmtCompact(f.minDollarVol)}` }), onRemove: () => patch({ minDollarVol: 0 }) });
  }
  if (f.minScore != null) chips.push({ key: 'ms', label: __t('强度 ≥{n}', { n: f.minScore }), onRemove: () => patch({ minScore: null }) });
  if (f.presetId) {
    const name = profiles?.find((p) => p.id === f.presetId)?.name ?? f.presetId;
    chips.push({ key: 'preset', label: __t('预设 {name}', { name }), onRemove: () => patch({ presetId: null }) });
  }
  return chips;
}

function summarizeFilters(f: ScanFilters): string {
  const parts: string[] = [];
  if (f.tier !== 'all') parts.push(__t('{tier} 档', { tier: f.tier }));
  if (f.timeframe !== 'all') parts.push(TIMEFRAME_CN[f.timeframe]);
  parts.push(PROFILE_CN[f.profile]);
  if (f.topN > 0) parts.push(`Top ${f.topN}`);
  if (f.sectors.length > 0) parts.push(__t('板块 {n} 项', { n: f.sectors.length }));
  if (f.priceMin != null || f.priceMax != null) parts.push(__t('价格区间'));
  if (f.minDollarVol > 0) parts.push(__t('成交额≥{v}', { v: fmtCompact(f.minDollarVol) }));
  if (f.minScore != null) parts.push(__t('强度≥{n}', { n: f.minScore }));
  return parts.join(' · ') || __t('默认条件');
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
