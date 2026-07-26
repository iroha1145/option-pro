/**
 * Mock fixtures · Optix 宏观环境（仅 VITE_API_MODE=mock 使用）
 *
 * 全部数值由本文件的确定性种子合成，**不是真实市场数据**，也不复制任何第三方
 * 报告的品牌、版式或示例分数。每个响应都带 `reason: 'mock_fixture'` 明确标记。
 * live 模式永不加载本文件的数据。
 */
import { Rng, round2, round4 } from './rng';
/* 值引用只走叶子模块；下面对 api/modules/macro 的引用全部是 type-only，
   编译后会被完全擦除，因此不会与该模块形成运行期循环。 */
import { MACRO_MODULE_ORDER, type MacroModuleId } from '@/lib/macroModules';
import type {
  MacroConditionsResponse,
  MacroDriver,
  MacroFactor,
  MacroFactorHistoryPoint,
  MacroHistoryPoint,
  MacroHistoryResponse,
  MacroModule,
  MacroModuleDetail,
  MacroRefreshResult,
} from '@/api/modules/macro';

/** 明确的 mock 标记：UI 与测试都可据此判断数据来源。 */
export const MOCK_REASON = 'mock_fixture';
const SCORING_VERSION = 'optix-macro-score-v1';

const rng = new Rng(20260724);

const MODULE_META: Record<MacroModuleId, { zh: string; en: string; total: number; floor: number }> = {
  liquidity: { zh: '流动性', en: 'LIQUIDITY', total: 5, floor: 3 },
  funding: { zh: '融资', en: 'FUNDING', total: 6, floor: 4 },
  treasury: { zh: '国债', en: 'TREASURY', total: 3, floor: 2 },
  rates: { zh: '利率', en: 'RATES', total: 3, floor: 2 },
  credit: { zh: '信用', en: 'CREDIT', total: 4, floor: 3 },
  risk: { zh: '风险', en: 'RISK', total: 4, floor: 3 },
  external: { zh: '外部冲击', en: 'EXTERNAL', total: 5, floor: 3 },
};

const FACTOR_META: { id: string; module: MacroModuleId; zh: string; unit: string; symbol: string; decimals: number }[] = [
  { id: 'fed_net_liquidity', module: 'liquidity', zh: '联储净流动性', unit: 'usd_billions', symbol: '十亿美元', decimals: 1 },
  { id: 'bank_reserves', module: 'liquidity', zh: '银行准备金', unit: 'usd_billions', symbol: '十亿美元', decimals: 1 },
  { id: 'net_liquidity_momentum_13w', module: 'liquidity', zh: '净流动性 13 周动量', unit: 'usd_billions', symbol: '十亿美元', decimals: 1 },
  { id: 'tga_deviation_52w', module: 'liquidity', zh: 'TGA 偏离一年中位数', unit: 'usd_billions', symbol: '十亿美元', decimals: 1 },
  { id: 'on_rrp_buffer_risk', module: 'liquidity', zh: '隔夜逆回购缓冲风险', unit: 'ratio', symbol: '', decimals: 4 },
  { id: 'collateral_repo_friction', module: 'funding', zh: '抵押品回购摩擦', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'corridor_friction_1', module: 'funding', zh: '利率走廊摩擦（SOFR−IORB）', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'corridor_friction_2', module: 'funding', zh: '利率走廊摩擦（SOFR−ON RRP）', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'effr_iorb_spread', module: 'funding', zh: 'EFFR−IORB 价差', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'cp_tbill_spread', module: 'funding', zh: '商业票据−国库券价差', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'funding_fragmentation_21d', module: 'funding', zh: '融资分化度（21 日）', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'term_premium_30y_10y', module: 'treasury', zh: '30 年−10 年期限斜率', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'rate_volatility_10y_21d', module: 'treasury', zh: '10 年期利率波动（21 日）', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'curve_curvature_abs', module: 'treasury', zh: '曲线曲率绝对值', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'real_rate_level', module: 'rates', zh: '实际利率水平', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'real_curve_10y_5y', module: 'rates', zh: '实际利率曲线（10 年−5 年）', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'breakeven_10y', module: 'rates', zh: '10 年期通胀预期', unit: 'percentage_points', symbol: '个百分点', decimals: 3 },
  { id: 'nfci', module: 'credit', zh: '全国金融条件指数', unit: 'index_points', symbol: '', decimals: 2 },
  { id: 'hy_credit', module: 'credit', zh: '高收益债相对强度', unit: 'percent', symbol: '%', decimals: 2 },
  { id: 'ig_credit', module: 'credit', zh: '投资级债相对强度', unit: 'percent', symbol: '%', decimals: 2 },
  { id: 'regional_banks_vs_spy', module: 'credit', zh: '区域银行相对大盘', unit: 'percent', symbol: '%', decimals: 2 },
  { id: 'vix', module: 'risk', zh: 'VIX 波动率', unit: 'index_points', symbol: '', decimals: 2 },
  { id: 'vix_term_structure', module: 'risk', zh: 'VIX 期限结构', unit: 'ratio', symbol: '', decimals: 4 },
  { id: 'risk_vs_safe', module: 'risk', zh: '风险资产相对避险资产', unit: 'percent', symbol: '%', decimals: 2 },
  { id: 'high_beta_preference', module: 'risk', zh: '高贝塔偏好', unit: 'percent', symbol: '%', decimals: 2 },
  { id: 'broad_dollar_index', module: 'external', zh: '美元广义指数', unit: 'index_points', symbol: '', decimals: 2 },
  { id: 'fx_realized_volatility_63d', module: 'external', zh: '美元已实现波动（63 日）', unit: 'ratio', symbol: '', decimals: 4 },
  { id: 'wti_oil', module: 'external', zh: 'WTI 原油价格', unit: 'usd_per_barrel', symbol: '美元/桶', decimals: 2 },
  { id: 'oil_volatility_deviation', module: 'external', zh: '原油波动率偏离', unit: 'index_points', symbol: '', decimals: 2 },
  { id: 'natural_gas', module: 'external', zh: '天然气价格', unit: 'usd_per_mmbtu', symbol: '美元/百万英热', decimals: 2 },
];

const REGIME_BANDS: [number, string][] = [
  [70, '明显宽松'],
  [55, '偏松'],
  [45, '中性'],
  [30, '偏紧'],
  [-Infinity, '明显收紧'],
];

function regimeFor(score: number | null): string | null {
  if (score === null) return null;
  return REGIME_BANDS.find(([lower]) => score >= lower)?.[1] ?? null;
}

function isoDate(offsetDays: number): string {
  const now = new Date();
  now.setUTCDate(now.getUTCDate() - offsetDays);
  return now.toISOString().slice(0, 10);
}

function formatValue(value: number | null, symbol: string, decimals: number): string | null {
  if (value === null) return null;
  const body = value.toFixed(decimals);
  if (!symbol) return body;
  return symbol === '%' ? `${body}%` : `${body} ${symbol}`;
}

function formatChange(value: number | null, symbol: string, decimals: number): string | null {
  if (value === null) return null;
  const body = formatValue(Math.abs(value), symbol, decimals);
  return body === null ? null : `${value >= 0 ? '+' : '−'}${body}`;
}

/* 固定的合成分数表：同一构建内保持一致，便于视觉与快照测试比对。 */
const FACTOR_SCORES = new Map<string, number>(
  FACTOR_META.map((factor) => [factor.id, round2(rng.normal(50, 22, 2, 98))]),
);
const FACTOR_CHANGES = new Map<string, number>(
  FACTOR_META.map((factor) => [factor.id, round2(rng.normal(0, 9, -34, 34))]),
);

function factorRaw(factor: (typeof FACTOR_META)[number]): number {
  switch (factor.unit) {
    case 'usd_billions':
      return round2(rng.normal(2800, 900, -400, 6800));
    case 'percentage_points':
      return round4(rng.normal(0.1, 0.4, -1.2, 1.6));
    case 'percent':
      return round2(rng.normal(0, 6, -18, 18));
    case 'usd_per_barrel':
      return round2(rng.normal(78, 10, 45, 118));
    case 'usd_per_mmbtu':
      return round2(rng.normal(3.2, 0.9, 1.4, 7.2));
    case 'ratio':
      return round4(rng.normal(0.6, 0.3, 0, 1.4));
    default:
      return round2(rng.normal(22, 8, 9, 62));
  }
}

const FACTOR_RAWS = new Map<string, number>(
  FACTOR_META.map((factor) => [factor.id, factorRaw(factor)]),
);

function buildFactor(meta: (typeof FACTOR_META)[number]): MacroFactor {
  const raw = FACTOR_RAWS.get(meta.id) ?? 0;
  const change = round4(raw * 0.02);
  const signed = meta.unit === 'percentage_points' ? raw : null;
  return {
    factorId: meta.id,
    moduleId: meta.module,
    nameZh: meta.zh,
    descriptionZh: `${MOCK_REASON}：合成示例值，不是真实数据。`,
    formulaVersion: 'optix-macro-factor-v1',
    rawValue: raw,
    formattedValue: formatValue(raw, meta.symbol, meta.decimals),
    signedValue: signed,
    formattedSignedValue: formatValue(signed, meta.symbol, meta.decimals),
    unit: { unit: meta.unit, symbolZh: meta.symbol, decimals: meta.decimals },
    score: FACTOR_SCORES.get(meta.id) ?? null,
    scoreMethod: 'supportive_high_percentile',
    direction: 'high',
    rawChange7d: change,
    formattedRawChange7d: formatChange(change, meta.symbol, meta.decimals),
    scoreChange7d: FACTOR_CHANGES.get(meta.id) ?? null,
    confidence: 1,
    validObservations: 1258,
    minimumHistory: 252,
    status: 'ok',
    dataThrough: isoDate(1),
    historyBasis: 'latest_revised_backfill',
    missingInputs: [],
    staleInputs: [],
    sources: ['FRED（合成）'],
  };
}

function buildModule(moduleId: MacroModuleId): MacroModule {
  const meta = MODULE_META[moduleId];
  const members = FACTOR_META.filter((factor) => factor.module === moduleId);
  const scores = members.map((factor) => FACTOR_SCORES.get(factor.id) ?? 0);
  const score = round2(scores.reduce((sum, value) => sum + value, 0) / scores.length);
  const changes = members.map((factor) => FACTOR_CHANGES.get(factor.id) ?? 0);
  return {
    moduleId,
    nameZh: meta.zh,
    nameEn: meta.en,
    score,
    scoreChange7d: round2(changes.reduce((sum, value) => sum + value, 0) / changes.length),
    confidence: 1,
    validFactorCount: meta.total,
    totalFactorCount: meta.total,
    minimumValidFactors: meta.floor,
    dataThrough: isoDate(1),
    status: 'ok',
  };
}

const MOCK_MODULES: MacroModule[] = MACRO_MODULE_ORDER.map(buildModule);
const MOCK_COMPOSITE_SCORE = round2(
  MOCK_MODULES.reduce((sum, module) => sum + (module.score ?? 0), 0) / MOCK_MODULES.length,
);

function buildDrivers(): { improving: MacroDriver[]; deteriorating: MacroDriver[] } {
  const ranked = FACTOR_META.map((meta) => ({
    factorId: meta.id,
    moduleId: meta.module,
    nameZh: meta.zh,
    score: FACTOR_SCORES.get(meta.id) ?? null,
    scoreChange7d: FACTOR_CHANGES.get(meta.id) ?? null,
  }));
  const sorted = [...ranked].sort(
    (a, b) => (b.scoreChange7d ?? 0) - (a.scoreChange7d ?? 0),
  );
  return {
    improving: sorted.filter((item) => (item.scoreChange7d ?? 0) > 0).slice(0, 3),
    deteriorating: sorted
      .filter((item) => (item.scoreChange7d ?? 0) < 0)
      .slice(-3)
      .reverse(),
  };
}

const STRUCTURAL_MOCK_MODULES = ['liquidity', 'funding', 'treasury', 'rates'];

export function getMacroConditions(): MacroConditionsResponse {
  return {
    status: 'active',
    reason: MOCK_REASON,
    asOf: new Date().toISOString(),
    dataThrough: isoDate(1),
    scoringVersion: SCORING_VERSION,
    historyBasis: 'latest_revised_backfill',
    composite: {
      score: MOCK_COMPOSITE_SCORE,
      scoreChange7d: round2(rng.normal(0, 4, -12, 12)),
      confidence: 1,
      regime: regimeFor(MOCK_COMPOSITE_SCORE),
      validModuleCount: MOCK_MODULES.length,
      totalModuleCount: MOCK_MODULES.length,
      snapshotDate: isoDate(0),
      formattedScore: `${MOCK_COMPOSITE_SCORE.toFixed(1)} 分`,
    },
    modules: MOCK_MODULES,
    /* 结构性宏观 = 流动性/融资/国债/利率。信用与风险读的是技术形态用的同一批工具，
       不计入，否则同一个信号换两个名字计两次权 —— 与后端 STRUCTURAL_MODULES 同义。 */
    structuralScore: (() => {
      const scored = MOCK_MODULES.filter(
        (m) => STRUCTURAL_MOCK_MODULES.includes(m.moduleId) && m.score !== null,
      );
      return scored.length === 0
        ? null
        : round2(scored.reduce((sum, m) => sum + (m.score ?? 0), 0) / scored.length);
    })(),
    structuralModules: [...STRUCTURAL_MOCK_MODULES],
    drivers: buildDrivers(),
    warnings: [MOCK_REASON],
    sources: ['合成 mock 数据 · 不是真实来源'],
  };
}

export function getMacroHistory(days = 365): MacroHistoryResponse {
  const historyRng = new Rng(19940101 + days);
  const points: MacroHistoryPoint[] = [];
  const step = days > 400 ? 5 : 1;
  let level = MOCK_COMPOSITE_SCORE;
  for (let offset = days; offset >= 0; offset -= step) {
    level = Math.min(92, Math.max(8, level + historyRng.normal(0, 1.6, -5, 5)));
    const moduleScores: Partial<Record<MacroModuleId, number | null>> = {};
    for (const moduleId of MACRO_MODULE_ORDER) {
      moduleScores[moduleId] = round2(
        Math.min(96, Math.max(4, level + historyRng.normal(0, 9, -26, 26))),
      );
    }
    points.push({
      date: isoDate(offset),
      score: round2(level),
      confidence: 1,
      regime: regimeFor(level),
      /* 早于约一年的区间标为「按当前修订值回算」，与真实语义一致。 */
      historyBasis: offset > 365 ? 'latest_revised_backfill' : 'local_point_in_time',
      moduleScores,
    });
  }
  return { status: 'active', days, points };
}

export function getMacroModuleDetail(moduleId: MacroModuleId): MacroModuleDetail {
  const meta = MODULE_META[moduleId];
  return {
    status: 'active',
    moduleId,
    nameZh: meta.zh,
    nameEn: meta.en,
    snapshotDate: isoDate(0),
    module: MOCK_MODULES.find((item) => item.moduleId === moduleId) ?? null,
    factors: FACTOR_META.filter((factor) => factor.module === moduleId).map(buildFactor),
  };
}

export function getMacroFactorHistory(factorId: string, days = 365): MacroFactorHistoryPoint[] {
  const meta = FACTOR_META.find((item) => item.id === factorId);
  if (!meta) return [];
  const historyRng = new Rng(20200202 + factorId.length);
  const points: MacroFactorHistoryPoint[] = [];
  for (let offset = days; offset >= 0; offset -= 1) {
    points.push({
      date: isoDate(offset),
      rawValue: round4(historyRng.normal(FACTOR_RAWS.get(factorId) ?? 0, 1, -1e6, 1e6)),
      signedValue: null,
      score: round2(historyRng.normal(FACTOR_SCORES.get(factorId) ?? 50, 8, 2, 98)),
      status: 'ok',
      dataThrough: isoDate(offset),
      historyBasis: 'latest_revised_backfill',
    });
  }
  return points;
}

export function refreshMacroConditions(): MacroRefreshResult {
  return {
    requestId: 'act_mock_macro_refresh',
    status: 'queued',
    reason: 'queued',
    reused: false,
    cooldownUntil: null,
    cooldownSeconds: 300,
    errorCode: null,
  };
}
