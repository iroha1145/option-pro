/** 强度域：GET /api/strength/market · /profiles · /scan?params */
import { get, mockOr, toQuery } from '../client';
import { asRec, pickN, pickS, unwrap } from '../live';
import * as fx from '@/mocks/fixtures';
import type { MarketStrength, ScreenerRow, StrengthBand, StrengthProfile } from '../types';

/**
 * 扫描参数：band/sector/minScore/sort/order 为 UI 侧筛选（live 下客户端套用）；
 * timeframe/profile/top/sector_id/min_price/min_avg_dollar_volume/include_options/universe
 * 为契约参数（api-contract §strength），live 原样下发。
 */
export interface ScanParams {
  band?: 'strong' | 'mid' | 'weak' | 'all';
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
  if (p.sector && p.sector !== 'all') out = out.filter((r) => r.sector === p.sector);
  if (p.minScore !== undefined) out = out.filter((r) => r.strengthScore >= p.minScore!);
  const sort = p.sort ?? 'score';
  const dir = p.order === 'asc' ? 1 : -1;
  out.sort((a, b) => {
    if (sort === 'ticker') return a.ticker.localeCompare(b.ticker) * dir;
    if (sort === 'changePct') return (a.changePct - b.changePct) * dir;
    return (a.strengthScore - b.strengthScore) * dir;
  });
  return out;
}

/** 契约 StrengthRow（snake_case）→ UI ScreenerRow；契约缺失字段不编造（见 AUDIT-live.md） */
function mapScanRow(r: Record<string, unknown>): ScreenerRow {
  const score = pickN(r, 'strengthScore', 'final_score') ?? 0;
  const band: StrengthBand = score >= 85 ? 'strong' : score >= 60 ? 'mid' : 'weak';
  return {
    ticker: pickS(r, 'ticker') ?? '',
    name: pickS(r, 'name') ?? '',
    sector: pickS(r, 'sector') ?? '',
    price: pickN(r, 'price') ?? 0,
    changePct: pickN(r, 'changePct', 'change_percent') ?? 0,
    strengthScore: score,
    band,
    // 契约无 trend/momentum/volume/volatility 四维子分：以周期分近似（注释如实标注）
    subscores: {
      trend: pickN(r, 'score_long') ?? 0,
      momentum: pickN(r, 'score_short') ?? 0,
      volume: pickN(r, 'score_mid') ?? 0,
      volatility: pickN(r, 'breakout_quality_score') ?? 0,
    },
    sparkline: [], // 契约 StrengthRow 无 sparkline
  };
}

/** live 仅下发契约白名单参数（sector → sector_id），其余 UI 参数客户端套用 */
function liveScan(params: ScanParams): Promise<ScreenerRow[]> {
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
  return get(`/strength/scan${qs ? `?${qs}` : ''}`).then((d) => applyParams(unwrap(d, 'rows').map(mapScanRow), params));
}

/** 契约 /strength/market（market_regime 六维分）→ UI MarketStrength（直读 snake/camel，缺失不编造） */
function mapMarket(d: unknown): MarketStrength {
  const r = asRec(d);
  const regime = asRec(r.market_regime);
  return {
    avgScore: pickN(r, 'avgScore', 'avg_score') ?? pickN(regime, 'index_trend_score') ?? 0,
    ge85Count: pickN(r, 'ge85Count', 'ge85_count') ?? 0,
    histogram: Array.isArray(r.histogram) ? (r.histogram as number[]) : [],
  };
}

/** 契约 /strength/profiles → UI StrengthProfile[]（解包 + 权重容错） */
function mapProfiles(d: unknown): StrengthProfile[] {
  return unwrap(d, 'profiles').map((p) => {
    const w = asRec(p.weights);
    return {
      id: pickS(p, 'id') ?? '',
      name: pickS(p, 'name') ?? '',
      description: pickS(p, 'description') ?? '',
      weights: {
        trend: pickN(w, 'trend') ?? 0,
        momentum: pickN(w, 'momentum') ?? 0,
        volume: pickN(w, 'volume') ?? 0,
        volatility: pickN(w, 'volatility') ?? 0,
      },
    };
  });
}

export const strengthApi = {
  market: (): Promise<MarketStrength> => mockOr(() => fx.getMarketStrength(), () => get('/strength/market').then(mapMarket)),
  profiles: (): Promise<StrengthProfile[]> =>
    mockOr(() => fx.getStrengthProfiles(), () => get('/strength/profiles').then(mapProfiles)),
  scan: (params: ScanParams = {}): Promise<ScreenerRow[]> =>
    mockOr(() => applyParams(fx.runStrengthScan(), params), () => liveScan(params)),
};
