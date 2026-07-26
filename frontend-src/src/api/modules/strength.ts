/** 强度域：GET /api/strength/market · /profiles · /scan?params */
import { get, mockOr, toQuery } from '../client';
import { sharedGlobalGet } from '../sharedRead';
import { marketGet } from '../marketRead';
import { asRec, pickB, pickN, pickS, pickLabel, unwrap, type Rec } from '../live';
import { mapMacroFitDrivers } from '../macroFields';
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
  /** 后端统计的整池分档；旧快照没有这个字段时为 null。 */
  tierDistribution: TierDistribution | null;
  stale: boolean;
  asOf: string | null;
  snapshotSavedAt: string | null;
  priceProvider: string | null;
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
}

function applyParams(rows: ScreenerRow[], p: ScanParams): ScreenerRow[] {
  let out = [...rows];
  if (p.band && p.band !== 'all') out = out.filter((r) => r.band === p.band);
  // sector 入参可能是契约 sector_id（live）或板块名（mock）：双向匹配
  if (p.sector && p.sector !== 'all') out = out.filter((r) => r.sector === p.sector || r.sectorId === p.sector);
  if (p.minScore !== undefined) out = out.filter((r) => r.strengthScore >= p.minScore!);
  const sort = p.sort ?? 'score';
  const dir = p.order === 'asc' ? 1 : -1;
  out.sort((a, b) => {
    if (sort === 'ticker') return a.ticker.localeCompare(b.ticker) * dir;
    if (sort === 'changePct') {
      // 涨跌幅缺失稳定排在末尾，而不是当成 0% 混进真实平盘股票中间（审计 P2-14）。
      const left = a.changePct;
      const right = b.changePct;
      if (left === null && right === null) return a.ticker.localeCompare(b.ticker);
      if (left === null) return 1;
      if (right === null) return -1;
      return (left - right) * dir || a.ticker.localeCompare(b.ticker);
    }
    return (a.strengthScore - b.strengthScore) * dir;
  });
  return out;
}

/**
 * 契约 StrengthRow（snake_case）→ UI ScreenerRow；契约缺失字段不编造（见 AUDIT-live.md）。
 * 关键对齐：change_pct（非 changePct/change_percent）· sector_name/sector_id ·
 * 分项 = 契约周期/质量分 score_short/score_mid/score_long/breakout_quality_score（subscoreDims 携带真实标签）。
 */
function mapScanRow(r: Record<string, unknown>): ScreenerRow | null {
  const ticker = pickS(r, 'ticker');
  const score = pickN(r, 'strengthScore', 'final_score', 'strength_score', 'score');
  const price = pickN(r, 'price');
  // 价格或评分缺失的行不能用 0 冒充真实扫描结果。
  if (!ticker || score === null || price === null) return null;
  const band: StrengthBand = score >= 85 ? 'strong' : score >= 60 ? 'mid' : 'weak';
  const dims: ScreenerSubscoreDim[] = [
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
    price,
    // 契约键为 change_pct；缺失如实为 null（UI 显「—」，不显 +0.00%）
    changePct: pickN(r, 'changePct', 'change_pct', 'change_percent'),
    strengthScore: score,
    avgDollarVolume20d: pickN(r, 'avg_dollar_volume_20d'),
    band,
    // 兼容槽位（消费层优先 subscoreDims；此处仅按周期分近似填充，注释如实标注）
    // 缺失保持 null：补 0 会让回退路径把「没有数据」画成「该项 0 分」。
    subscores: {
      trend: dims[2].value,
      momentum: dims[0].value,
      volume: dims[1].value,
      volatility: dims[3].value,
    },
    subscoreDims: dims,
    sparkline: [], // 契约 StrengthRow 无 sparkline（行展开按需拉日 K，见 RowExpansion）
    // 影子字段：缺失一律保持 null / 空数组，绝不兜成 50 或「中性」。后端在覆盖度
    // 不足时返回 null 正是为了区分「说不出来」和「中性」，前端补一手就白做了。
    macroFit: pickN(r, 'macro_fit_shadow'),
    macroTailwind: pickS(r, 'macro_tailwind'),
    macroFitConfidence: pickN(r, 'macro_fit_confidence'),
    macroSupporting: mapMacroFitDrivers(r.macro_supporting_factors),
    macroOpposing: mapMacroFitDrivers(r.macro_opposing_factors),
    macroTechnicalGap: pickN(r, 'macro_technical_gap'),
  };
}

/** live 仅下发契约白名单参数（sector → sector_id），其余 UI 参数客户端套用 */
function liveScan(params: ScanParams, force = false): Promise<StrengthScanEnvelope> {
  const qs = toQuery({
    universe: params.universe,
    timeframe: params.timeframe,
    profile: params.profile,
    top: params.top,
    sector_id: params.sector_id ?? params.sector,
    min_price: params.min_price,
    min_avg_dollar_volume: params.min_avg_dollar_volume,
    include_options: params.include_options,
  });
  return marketGet(`/strength/scan${qs ? `?${qs}` : ''}`, {
    ttlMs: 30_000,
    staleMs: 15 * 60_000,
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
      tierDistribution: mapTierDistribution(env.tier_distribution ?? env.tierDistribution),
      stale: pickB(env, '_stale', 'stale') ?? false,
      asOf: pickS(env, 'as_of', 'score_data_through', 'data_through'),
      snapshotSavedAt: pickS(env, 'snapshot_saved_at'),
      priceProvider: pickS(asRec(sources.prices), 'provider'),
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

/** 契约 market_regime（六维分 + label + warnings）→ MarketRegimeInfo；缺失如实 null */
function mapRegime(env: Rec): MarketRegimeInfo | null {
  const regime = asRec(env.market_regime);
  if (Object.keys(regime).length === 0) return null;
  return {
    score: pickN(regime, 'score', 'partial_score'),
    label: pickS(regime, 'label'),
    spreadLabel: pickS(regime, 'risk_on_spread_label'),
    warnings: Array.isArray(regime.warnings)
      ? (regime.warnings as unknown[]).filter((x): x is string => typeof x === 'string')
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
  const avgScore = pickN(r, 'avgScore', 'avg_score');
  const ge85Count = pickN(r, 'ge85Count', 'ge85_count');
  const histogram = Array.isArray(r.histogram)
    ? (r.histogram as unknown[]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    : [];
  const aggregateAvailable = avgScore !== null && ge85Count !== null && histogram.length > 0;
  return {
    // 兼容旧消费层的数值槽位；aggregateAvailable=false 时界面不得读取或展示。
    // null 通过边界类型转换保留，避免把 market_regime.score 冒充全市场均分。
    avgScore: avgScore as number,
    ge85Count: ge85Count as number,
    histogram,
    aggregateAvailable,
    ...(regime ? { regime } : {}),
  };
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
 * name 用枚举中文名，description 留空，weights 缺失（UI 隐藏权重条）。
 * 对象数组形状（mock 契约扩展）保留原映射。
 */
function mapProfiles(d: unknown): StrengthProfile[] {
  return unwrap(d, 'profiles').map((p) => {
    if (typeof (p as unknown) === 'string') {
      const id = p as unknown as string;
      return { id, name: PROFILE_NAME_CN[id] ?? id, description: '' };
    }
    const w = asRec(p.weights);
    const hasW = ['trend', 'momentum', 'volume', 'volatility'].some((k) => pickN(w, k) !== null);
    return {
      id: pickS(p, 'id') ?? '',
      name: pickLabel(p, 'name') ?? '',
      description: pickLabel(p, 'description') ?? '',
      ...(hasW
        ? {
            weights: {
              trend: pickN(w, 'trend') ?? 0,
              momentum: pickN(w, 'momentum') ?? 0,
              volume: pickN(w, 'volume') ?? 0,
              volatility: pickN(w, 'volatility') ?? 0,
            },
          }
        : {}),
    };
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
  profiles: (): Promise<StrengthProfile[]> =>
    mockOr(() => fx.getStrengthProfiles(), () => get('/strength/profiles').then(mapProfiles)),
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
          asOf: null,
          snapshotSavedAt: null,
          priceProvider: 'mock fixtures',
        };
      },
      () => liveScan(params, force),
    ),
  scan: (params: ScanParams = {}): Promise<ScreenerRow[]> =>
    mockOr(() => applyParams(fx.runStrengthScan(), params), () => liveScan(params).then((result) => result.rows)),
};
