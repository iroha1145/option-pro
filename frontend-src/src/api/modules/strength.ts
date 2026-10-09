/** 强度域：GET /api/strength/market · /profiles · /scan?params */
import { get, mockOr, toQuery } from '../client';
import { sharedGlobalGet } from '../sharedRead';
import { marketGet } from '../marketRead';
import { asRec, pickB, pickN, pickS, pickLabel, unwrap, type Rec } from '../live';
import * as fx from '@/mocks/fixtures';
import type {
  MarketRegimeInfo,
  MarketStrength,
  ScreenerRow,
  ScreenerSubscoreDim,
  SectorOption,
  StrengthBand,
  StrengthProfile,
  StrengthProfilesMeta,
} from '../types';
import { t } from '../../i18n/core.ts';

/**
 * 扫描参数：band/sector/minScore/sort/order 为 UI 侧筛选（live 下客户端套用）；
 * timeframe/profile/top/sector_id/min_price/min_avg_dollar_volume/include_options/universe
 * 为契约参数（api-contract §strength），live 原样下发。
 */
/**
 * 全候选池的分档分布（审计 P2-10）。
 * 契约 tier_distribution 由后端在截取 top 之前统计；缺失时为 null —— 不能拿
 * 返回的这几行去冒充整池分布。
 */
export interface TierDistribution {
  S: number;
  A: number;
  B: number;
  C: number;
  D: number;
  /** 分数缺失的行；它们不是 D 档。 */
  unscored: number;
  scored: number;
  total: number;
}

export interface StrengthScanEnvelope {
  rows: ScreenerRow[];
  universeCount: number;
  screenedCount: number;
  universe?: string | null;
  coverage?: {
    directoryCount: number | null;
    excludedCount: number | null;
    missingSessionCount: number | null;
    shortHistoryCount: number | null;
    scoredCount: number | null;
    status: string | null;
  };
  /** 后端统计的整池分档；旧快照没有这个字段时为 null。 */
  tierDistribution: TierDistribution | null;
  stale: boolean;
  sourceStatus: string | null;
  staleReason: string | null;
  asOf: string | null;
  scoreDataThrough: string | null;
  scoreVersion: string | null;
  scanCompletedAt: string | null;
  snapshotSavedAt: string | null;
  cacheExpiresAt: string | null;
  priceProvider: string | null;
  effectiveAlgorithm: string | null;
  algorithmVersion: string | null;
  scoreBasis: string | null;
  fallbackReason: string | null;
  resolvedTimeframe: string | null;
  purpose: string | null;
  historicalExample: boolean;
  synthetic: boolean;
  servedSession: string | null;
  listKind: string | null;
  emptyEligibleReason: string | null;
  eligibleN: number | null;
  watchN: number | null;
  compositeN: number | null;
  observationN: number | null;
  filterSupport?: {
    minPrice: boolean | null;
    minAvgDollarVolume: boolean | null;
  };
}

export interface ScanParams {
  band?: 'strong' | 'mid' | 'weak' | 'all';
  /** 板块过滤：live 传契约 sector_id；mock 传 sector 名（applyParams 双向匹配） */
  sector?: string;
  minScore?: number;
  sort?: 'score' | 'changePct' | 'ticker';
  order?: 'asc' | 'desc';
  /* ---- 契约参数（mock 忽略） ---- */
  universe?: string;
  timeframe?: 'short' | 'mid' | 'long' | 'all';
  profile?: 'conservative' | 'balanced' | 'aggressive';
  top?: number; // 5–120
  sector_id?: string;
  min_price?: number;
  min_avg_dollar_volume?: number;
  include_options?: boolean;
  ranking_algorithm?: 'production' | 'a0_mid_long' | 'eod_limited_v1' | 'follow_default';
  list_kind?: 'observation' | 'composite';
}

function applyParams(rows: ScreenerRow[], p: ScanParams): ScreenerRow[] {
  let out = [...rows];
  if (p.band && p.band !== 'all') out = out.filter((r) => r.band === p.band);
  if (p.sector && p.sector !== 'all') out = out.filter((r) => r.sector === p.sector || r.sectorId === p.sector);
  if (p.minScore !== undefined) out = out.filter((r) => r.strengthScore >= p.minScore!);
  const sort = p.sort ?? 'score';
  const dir = p.order === 'asc' ? 1 : -1;
  out.sort((a, b) => {
    if (sort === 'ticker') return a.ticker.localeCompare(b.ticker) * dir;
    if (sort === 'changePct') {
      const left = a.changePct;
      const right = b.changePct;
      if (left === null && right === null) return a.ticker.localeCompare(b.ticker);
      if (left === null) return 1;
      if (right === null) return -1;
      return (left - right) * dir || a.ticker.localeCompare(b.ticker);
    }
    return (a.strengthScore - b.strengthScore) * dir || a.ticker.localeCompare(b.ticker);
  });
  return out;
}

/**
 * 契约 StrengthRow（snake_case）→ UI ScreenerRow；契约缺失字段不编造（见 AUDIT-live.md）。
 * 关键对齐：change_pct（非 changePct/change_percent）· sector_name/sector_id ·
 * 分项 = 契约周期/质量分 score_short/score_mid/score_long/breakout_quality_score（subscoreDims 携带真实标签）。
 */
function mapFactorDims(raw: unknown): ScreenerSubscoreDim[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const dims = raw
    .map((item) => {
      const rec = asRec(item);
      const key = pickS(rec, 'key');
      const label = pickLabel(rec, 'label') ?? key;
      if (!key || !label) return null;
      return { key, label, value: pickN(rec, 'value') };
    })
    .filter((item): item is ScreenerSubscoreDim => item !== null);
  return dims.length > 0 ? dims : null;
}

function mapScanRow(r: Record<string, unknown>): ScreenerRow | null {
  const ticker = pickS(r, 'ticker');
  const score = pickN(r, 'strengthScore', 'final_score', 'strength_score', 'score');
  const priceUnknown = pickB(r, 'price_unknown', 'priceUnknown') ?? false;
  const price = pickN(r, 'price');
  // 价格或评分缺失的行不能用 0 冒充真实扫描结果。收盘快照可显式标 price_unknown。
  if (!ticker || score === null) return null;
  if (price === null && !priceUnknown) return null;
  const band: StrengthBand = score >= 85 ? 'strong' : score >= 60 ? 'mid' : 'weak';
  const factorDims = mapFactorDims(r.factor_dims ?? r.factorDims);
  const dims: ScreenerSubscoreDim[] = factorDims ?? [
    { key: 'score_short', label: t('短期'), value: pickN(r, 'score_short') },
    { key: 'score_mid', label: t('中期'), value: pickN(r, 'score_mid') },
    { key: 'score_long', label: t('长期'), value: pickN(r, 'score_long') },
    { key: 'breakout_quality_score', label: t('突破质量'), value: pickN(r, 'breakout_quality_score') },
  ];
  return {
    ticker,
    name: pickLabel(r, 'name') ?? ticker,
    sector: pickLabel(r, 'sector_name', 'primary_sector_name', 'sector') ?? '',
    sectorId: pickS(r, 'sector_id', 'primary_sector_id') ?? undefined,
    price: price ?? 0,
    priceUnknown,
    priceAsOf: pickS(r, 'price_as_of', 'quote_as_of', 'daily_data_through'),
    dailyDataThrough: pickS(r, 'daily_data_through'),
    // 契约键为 change_pct；缺失如实为 null（UI 显「—」，不显 +0.00%）
    changePct: pickN(r, 'changePct', 'change_pct', 'change_percent'),
    strengthScore: score,
    avgDollarVolume20d: pickN(r, 'avg_dollar_volume_20d'),
    band,
    subscoreDims: dims,
    sparkline: [], // 契约 StrengthRow 无 sparkline（行展开按需拉日 K，见 RowExpansion）
    dollarVolumeUnknown: pickB(r, 'dollar_volume_unknown', 'dollarVolumeUnknown') ?? false,
    dollarVolumeProxyAvailable: pickB(r, 'dollar_volume_proxy_available') ?? false,
    dollarLiquidityVerified: pickB(r, 'dollar_liquidity_verified') ?? false,
    volumeSessionVerified: pickB(r, 'volume_session_verified') ?? false,
    effectiveWeights: Object.fromEntries(Object.entries(asRec(r.effective_weights)).filter((entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1]))),
    scoreComponents: Object.fromEntries(Object.entries(asRec(r.score_components)).filter((entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1]))),
    qualification: pickS(r, 'qualification'),
    status: pickS(r, 'status'),
    rejectionReasons: Array.isArray(r.rejection_reasons)
      ? (r.rejection_reasons as unknown[]).filter((item): item is string => typeof item === 'string')
      : Array.isArray(r.rejectionReasons)
        ? (r.rejectionReasons as unknown[]).filter((item): item is string => typeof item === 'string')
        : [],
    listKind: pickS(r, 'list_kind', 'listKind'),
    observationOnly: pickB(r, 'observation_only', 'observationOnly') ?? false,
    observationFamilyCount: pickN(r, 'observation_family_count', 'observationFamilyCount') ?? undefined,
    observationFamilyScores: (() => {
      const raw = asRec(r.observation_family_scores ?? r.observationFamilyScores);
      if (Object.keys(raw).length === 0) return undefined;
      return Object.fromEntries(Object.entries(raw).map(([key, value]) => [key, typeof value === 'number' ? value : null]));
    })(),
    familyLabel: pickLabel(r, 'family_label', 'familyLabel'),
    algorithmId: pickS(r, 'algorithm_id', 'algorithmId'),
    stockOrEtfTrack: pickS(r, 'stock_or_etf_track', 'stockOrEtfTrack'),
    knownSupport: pickN(r, 'known_support', 'knownSupport'),
    knownResistance: pickN(r, 'known_resistance', 'knownResistance'),
    plannedInvalidation: pickN(r, 'planned_invalidation', 'plannedInvalidation'),
  };
}

/** live 仅下发契约白名单参数（sector → sector_id），其余 UI 参数客户端套用 */
function liveScan(params: ScanParams, force = false): Promise<StrengthScanEnvelope> {
  const qs = toQuery({
    universe: params.universe ?? 'all_market',
    timeframe: params.timeframe,
    profile: params.profile,
    top: params.top,
    sector_id: params.sector_id ?? params.sector,
    min_price: params.min_price,
    min_avg_dollar_volume: params.min_avg_dollar_volume,
    include_options: params.include_options,
    ranking_algorithm: params.ranking_algorithm,
    list_kind: params.list_kind,
  });
  return marketGet(`/strength/scan${qs ? `?${qs}` : ''}`, {
    ttlMs: 30_000,
    // The cached body embeds freshness metadata. Reusing it after its TTL on
    // a failed GET would falsely confirm old scores as newly checked/fresh.
    staleMs: 30_000,
    force,
  }).then((d) => {
    const env = asRec(d);
    const rows = unwrap(d, 'rows', 'results')
      .map(mapScanRow)
      .filter((row): row is ScreenerRow => row !== null);
    const sources = asRec(env.data_sources);
    return {
      rows: applyParams(rows, params),
      universeCount: pickN(env, 'universe_count', 'universeCount') ?? rows.length,
      screenedCount: pickN(env, 'screened_count', 'screenedCount') ?? rows.length,
      universe: pickS(env, 'universe'),
      coverage: {
        directoryCount: pickN(asRec(env.coverage), 'directory_count'),
        excludedCount: pickN(asRec(env.coverage), 'excluded_count'),
        missingSessionCount: pickN(asRec(env.coverage), 'missing_session_count'),
        shortHistoryCount: pickN(asRec(env.coverage), 'short_history_count'),
        scoredCount: pickN(asRec(env.coverage), 'scored_count'),
        status: pickS(asRec(env.coverage), 'status'),
      },
      tierDistribution: mapTierDistribution(env.tier_distribution ?? env.tierDistribution),
      stale: pickB(env, '_stale', 'stale') ?? false,
      sourceStatus: pickS(env, 'source_status'),
      staleReason: pickS(env, 'stale_reason'),
      asOf: pickS(env, 'as_of', 'score_data_through', 'data_through'),
      scoreDataThrough: pickS(env, 'score_data_through'),
      scoreVersion: pickS(env, 'score_version', 'scoring_version'),
      scanCompletedAt: pickS(env, 'scan_completed_at', 'snapshot_saved_at'),
      snapshotSavedAt: pickS(env, 'snapshot_saved_at'),
      cacheExpiresAt: pickS(env, 'cache_expires_at'),
      priceProvider: pickS(asRec(sources.prices), 'provider'),
      effectiveAlgorithm: pickS(env, 'effective_algorithm', 'effectiveAlgorithm'),
      algorithmVersion: pickS(env, 'algorithm_version', 'algorithmVersion'),
      scoreBasis: pickS(env, 'score_basis', 'scoreBasis'),
      fallbackReason: pickS(env, 'fallback_reason', 'fallbackReason'),
      resolvedTimeframe: pickS(env, 'resolved_timeframe', 'resolvedTimeframe'),
      purpose: pickS(env, 'purpose'),
      historicalExample: pickB(env, 'historical_example', 'historicalExample') ?? false,
      synthetic: pickB(env, 'synthetic') ?? false,
      servedSession: pickS(env, 'served_session', 'servedSession', 'as_of_session'),
      listKind: pickS(env, 'list_kind', 'listKind'),
      emptyEligibleReason: pickS(env, 'empty_eligible_reason', 'emptyEligibleReason'),
      eligibleN: pickN(env, 'eligible_n', 'eligibleN'),
      watchN: pickN(env, 'watch_n', 'watchN'),
      compositeN: pickN(env, 'composite_n', 'compositeN'),
      observationN: pickN(env, 'observation_n', 'observationN'),
      filterSupport: {
        minPrice: pickB(asRec(env.filter_support ?? env.filterSupport), 'min_price', 'minPrice'),
        minAvgDollarVolume: pickB(
          asRec(env.filter_support ?? env.filterSupport),
          'min_avg_dollar_volume',
          'minAvgDollarVolume',
        ),
      },
    };
  });
}

/** 契约 tier_distribution → UI；任一档缺失即整体判为不可用，不做部分拼装。 */
function mapTierDistribution(raw: unknown): TierDistribution | null {
  const r = asRec(raw);
  if (Object.keys(r).length === 0) return null;
  const keys = ['S', 'A', 'B', 'C', 'D', 'unscored', 'scored', 'total'] as const;
  const values = keys.map((key) => pickN(r, key));
  if (values.some((value) => value === null)) return null;
  return Object.fromEntries(
    keys.map((key, index) => [key, values[index] as number]),
  ) as unknown as TierDistribution;
}

/**
 * 后端 warnings 里两条是拼出来的（资产对名 + 固定后缀、缺失键列表 + 固定前缀），
 * 整句进词典永远缺译。按这两个稳定模式拆出参数走插值词条；其余整句查表，
 * 缺译回退原文——与 t() 全站口径一致。
 */
function localizeRegimeWarning(warning: string): string {
  const weak = /^(.+)偏弱，强势未充分扩散$/.exec(warning);
  if (weak) return t('{name}偏弱，强势未充分扩散', { name: weak[1] });
  const missing = /^可选市场数据不完整：(.+)$/.exec(warning);
  if (missing) return t('可选市场数据不完整：{list}', { list: missing[1] });
  return t(warning);
}

/** 契约 market_regime（六维分 + label + warnings）→ MarketRegimeInfo；缺失如实 null */
function mapRegime(env: Rec): MarketRegimeInfo | null {
  const regime = asRec(env.market_regime);
  if (Object.keys(regime).length === 0) return null;
  return {
    score: pickN(regime, 'score', 'partial_score'),
    // label / spreadLabel 是后端下发的中文档位名，经词典本地化
    label: pickLabel(regime, 'label'),
    spreadLabel: pickLabel(regime, 'risk_on_spread_label'),
    warnings: Array.isArray(regime.warnings)
      ? (regime.warnings as unknown[])
          .filter((x): x is string => typeof x === 'string')
          .map(localizeRegimeWarning)
      : [],
    dims: {
      indexTrend: pickN(regime, 'index_trend_score'),
      momentum: pickN(regime, 'market_momentum_score'),
      breadth: pickN(regime, 'market_breadth_score'),
      volume: pickN(regime, 'market_volume_score'),
      riskAppetite: pickN(regime, 'risk_appetite_score'),
      riskOnSpread: pickN(regime, 'risk_on_spread_score'),
    },
    asOf: pickS(env, 'as_of') ?? pickS(regime, 'as_of'),
  };
}

/** 契约 /strength/market = {as_of, market_regime:{…}} → UI MarketStrength（直读真实六维，缺失不编造） */
function mapMarket(d: unknown): MarketStrength {
  const r = asRec(d);
  const regime = mapRegime(r);
  return { ...(regime ? { regime } : {}) };
}

/** 契约 profile 枚举 → 中文名（与 screener PROFILE_CN 同口径） */
const PROFILE_NAME_CN: Record<string, string> = {
  conservative: t('稳健'),
  balanced: t('均衡'),
  aggressive: t('进取'),
};

/**
 * 契约 /strength/profiles → UI StrengthProfile[]。
 * 真实契约 profiles 为枚举字符串数组（无 name/description/weights）——不编造：
 * name 用枚举中文名；模拟数据使用相同的档位。
 */
function mapProfiles(d: unknown): StrengthProfile[] {
  return unwrap(d, 'profiles').flatMap((profile) => {
    if (typeof (profile as unknown) !== 'string') return [];
    const id = profile as unknown as string;
    return [{ id, name: PROFILE_NAME_CN[id] ?? id }];
  });
}

/** 契约 sectors:[{id,name}]（中文名）→ SectorOption[]；live 板块过滤下发 id */
function mapSectors(d: unknown): SectorOption[] {
  return unwrap(d, 'sectors')
    .map((s) => ({ id: pickS(s, 'id', 'sector_id') ?? '', name: pickLabel(s, 'name') ?? '' }))
    .filter((s) => s.id !== '' && s.name !== '');
}

export const strengthApi = {
  market: (): Promise<MarketStrength> => mockOr(() => fx.getMarketStrength(), () => sharedGlobalGet<unknown>('/strength/market').then(mapMarket)),
  /** profiles + 板块字典一次取齐（mock 无板块字典 → sectors:[]，消费层回退扫描行 sector 名） */
  profilesMeta: (): Promise<StrengthProfilesMeta> =>
    mockOr(
      () => ({ profiles: fx.getStrengthProfiles(), sectors: [] }),
      () => get('/strength/profiles').then((d) => ({ profiles: mapProfiles(d), sectors: mapSectors(d) })),
    ),
  scanEnvelope: (params: ScanParams = {}, force = false): Promise<StrengthScanEnvelope> =>
    mockOr(
      (): StrengthScanEnvelope => {
        const all = fx.runStrengthScan();
        // mock 下整池就是这批行，因此分布可以直接统计，不存在截断问题。
        const counts = { S: 0, A: 0, B: 0, C: 0, D: 0 };
        all.forEach((row) => {
          const score = row.strengthScore;
          const tier =
            score >= 90 ? 'S' : score >= 80 ? 'A' : score >= 70 ? 'B' : score >= 60 ? 'C' : 'D';
          counts[tier] += 1;
        });
        return {
          rows: applyParams(all, params),
          universeCount: all.length,
          screenedCount: all.length,
          tierDistribution: { ...counts, unscored: 0, scored: all.length, total: all.length },
          stale: false,
          sourceStatus: 'active',
          staleReason: null,
          asOf: null,
          scoreDataThrough: null,
          scoreVersion: null,
          scanCompletedAt: null,
          snapshotSavedAt: null,
          cacheExpiresAt: null,
          priceProvider: 'mock fixtures',
          effectiveAlgorithm: 'eod_limited_v1',
          algorithmVersion: 'eod-limited-v1.7',
          scoreBasis: 'synthetic factor display',
          fallbackReason: null,
          resolvedTimeframe: params.timeframe === 'all' ? 'mid' : params.timeframe ?? 'mid',
          purpose: null,
          historicalExample: false,
          synthetic: false,
          servedSession: null,
          listKind: params.list_kind ?? null,
          emptyEligibleReason: null,
          eligibleN: null,
          watchN: null,
          compositeN: null,
          observationN: null,
          filterSupport: {
            minPrice: true,
            minAvgDollarVolume: false,
          },
        };
      },
      () => liveScan(params, force),
    ),
};
