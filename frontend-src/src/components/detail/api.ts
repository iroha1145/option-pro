/**
 * 股票详情域数据助手（components/detail 局部）
 * 契约对齐但 api/modules 尚未覆盖的端点在此经 mockOr 接入：
 * - GET /api/stocks/{t}/chart（ChartBar quote_only / as_of / _stale）
 * - GET /api/signals/stock/{t}（trend_bias_* 完整形状）
 * - POST /api/signals/stock/{t}/ai-analysis（signal_analysis 任务，202）
 * - GET /api/strength/stocks/{t}（概览 503 时的基础行情回退：扫描行快照，匿名可用）
 * mock 模式下代码不存在抛 404（整页形态 404 空态）。
 *
 * 大写后的代码在本文件里一律叫 `symbol`，**不要改回 `t`**。翻译函数按惯例叫 `t`，
 * 从前这里每个函数都以 `const t = ticker.toUpperCase()` 开头，两者一撞，函数体内的
 * `t('…')` 就是 “t is not a function” —— 而且是运行时才炸，类型检查看不出来
 * （`t` 确实存在，只是变成了 string）。双保险：局部改名 `symbol`，翻译函数按
 * codemod 惯例以 `__t` 别名引入（见 i18n-coverage 的 classify）。
 */
import { ApiError, mockOr } from '@/api/client';
import { quoteSymbol } from '@/lib/quoteSymbol';
import { marketGet } from '@/api/marketRead';
import { asRec, pickLabel, pickN, pickS, unwrap, type Rec } from '@/api/live';
import { ma20Of, mapBar } from '@/api/modules/stocks';
import { stocksApi } from '@/api/modules/stocks';
import { mapMacroFitDrivers } from '@/api/macroFields';
import { postAiJob } from '@/api/modules/ai-jobs';
import * as fx from '@/mocks/fixtures';
import * as fx2 from '@/mocks/fixtures2';
import type { AiJob, StockChart, StockDetail, TechBaseStatus, TechnicalStructure, TechSwingPoint } from '@/api/types';
import type {
  ChartBarEx,
  StockChartEx,
  StockTrendBias,
  TrendBiasFactor,
} from '@/mocks/fixtures';
import { localeTag, t as __t } from '../../i18n/core.ts';

export type ChartRange = StockChart['range'];
/**
 * K 线打开时的默认周期。
 *
 * 导出是为了预取能命中**同一个 URL**：写死两份，预取一个周期、图表要另一个，
 * 就白发一次请求 —— 这个优化会变成它想修的那个问题。
 */
export const DEFAULT_CHART_RANGE: ChartRange = '1d';
export const CHART_RANGES: { value: ChartRange; label: string }[] = [
  { value: '5m', label: __t('5分') },
  { value: '15m', label: __t('15分') },
  { value: '1h', label: __t('1小时') },
  { value: '1d', label: __t('日线') },
  { value: '1w', label: __t('周线') },
];

/** 契约 {bars:[{t,o,h,l,c,v,quote_only}], as_of, _stale?} → StockChartEx（字段名 1:1） */
function mapChartEx(body: unknown, ticker: string, range: ChartRange): StockChartEx {
  const r = asRec(body);
  const bars = unwrap(body, 'bars').map((b: Rec) => mapBar<ChartBarEx>(b));
  const analysis = r.chart_analysis ?? r.chartAnalysis;
  return {
    ticker,
    range,
    bars,
    ma20: ma20Of(bars),
    as_of: pickS(r, 'as_of', 'asOf') ?? '',
    last_bar_at: pickS(r, 'last_bar_at'),
    ...(r._stale === true ? { _stale: true } : {}),
    ...(analysis && typeof analysis === 'object' && !Array.isArray(analysis)
      ? { chart_analysis: analysis as Record<string, unknown> }
      : {}),
  };
}

/**
 * /strength/stocks/{t} 信封 {as_of, row:{…扫描行…}} → 基础行情 StockDetail。
 * 仅填契约真实字段：价/涨跌（change 由 change_pct 反推真实算术）/板块/强度分/
 * 市值+PE（finnhub_metrics，市值单位为百万美元）/20 日均量；其余如实留空（UI 显「—」）。
 */
function strengthRowToDetail(env: Rec): StockDetail | null {
  const row = asRec(env.row);
  const ticker = pickS(row, 'ticker');
  const price = pickN(row, 'price');
  if (!ticker || price === null) return null; // 连基础行情都没有 → 保持原 503 空态
  const changePct = pickN(row, 'change_pct');
  const prevClose = changePct !== null && changePct > -100 ? price / (1 + changePct / 100) : null;
  const fin = asRec(row.finnhub_metrics);
  const marketCapM = pickN(fin, 'market_cap'); // Finnhub 市值单位：百万美元
  const nullNum = null as unknown as number;
  return {
    ticker,
    // 与 strength.ts 同源行保持一致：公司名/板块名过 pickLabel，EN/JA 下不再
    // 出现「列表页 Semiconductors、详情抽屉 半导体」的分裂
    name: pickLabel(row, 'name') ?? ticker,
    sector: pickLabel(row, 'sector_name', 'primary_sector_name') ?? '',
    price,
    change: prevClose !== null ? price - prevClose : nullNum,
    changePct: changePct ?? nullNum,
    sparkline: [],
    strengthScore: pickN(row, 'final_score', 'strength_score', 'score') ?? nullNum,
    signals: [],
    updatedAt: pickS(env, 'as_of') ?? '',
    open: nullNum,
    high: nullNum,
    low: nullNum,
    prevClose: prevClose ?? nullNum,
    volume: nullNum, // 契约仅有 20 日均量，无当日成交量——不冒充
    avgVolume: pickN(row, 'avg_volume_20d') ?? nullNum,
    marketCap: marketCapM !== null ? marketCapM * 1e6 : nullNum,
    pe: pickN(fin, 'pe_ttm'),
    ivPercentile: nullNum,
    range52w: null as unknown as [number, number],
    priceProvider: pickS(row, 'price_provider'),
    profileProvider: null,
    snapshotScope: 'strength-row',
    // 宏观适配影子字段：与选股表同源（同一份扫描行），缺失保持 null。
    macroFit: pickN(row, 'macro_fit_shadow'),
    macroTailwind: pickS(row, 'macro_tailwind'),
    macroFitConfidence: pickN(row, 'macro_fit_confidence'),
    macroSupporting: mapMacroFitDrivers(row.macro_supporting_factors),
    macroOpposing: mapMacroFitDrivers(row.macro_opposing_factors),
    macroTechnicalGap: pickN(row, 'macro_technical_gap'),
    // 这一行的影子字段是对着哪一期宏观快照算的（信封顶层 macro_linkage）。
    macroSnapshotDate: pickS(asRec(env.macro_linkage), 'macro_snapshot_date'),
  };
}

/** 强度补充的独立截止时间；超过就先给核心行情，评分随下一次读取补齐。 */
const STRENGTH_SUPPLEMENT_DEADLINE_MS = 4_000;

/**
 * 强度补充这一路的结果，**带上它为什么是这个结果**。
 *
 * 原来失败和超时都被折成 `null`，于是 503 回退分支只能重发一次请求来区分两者 ——
 * 而明确 404 的端点重发一次还是 404，纯属白跑。超时才值得再要一次：那次请求可能
 * 还在飞，marketGet 会让它们共用同一个 in-flight promise。
 */
type StrengthSupplement =
  | { kind: 'ok'; body: unknown }
  | { kind: 'failed' }
  | { kind: 'timeout' };

/** 抽屉里那组宏观影子字段。 */
type MacroFields = Pick<
  StockDetail,
  | 'macroFit'
  | 'macroTailwind'
  | 'macroFitConfidence'
  | 'macroSupporting'
  | 'macroOpposing'
  | 'macroTechnicalGap'
  | 'macroShadowStatus'
  | 'macroSnapshotDate'
>;

/**
 * 合并概览与扫描行的宏观影子字段。
 *
 * 两条规则，都是「不要把两份读数拼成一份」：
 *
 * 1. **概览一旦回答了，它就是权威**，不再逐字段回填扫描行。逐字段 `??` 会犯两个
 *    方向相反的错：概览说「本次没有读数」（macro_snapshot_unavailable /
 *    exposure_coverage_low）时，上一次扫描的旧分被复活；概览说「本期没有负面
 *    因子」时，空数组长度为 0，旧的负面因子被补回来。两者都是拿一份已经不成立的
 *    解释冒充当前读数 —— 后端专门为此返回 null 而不是 50，前端不能在这里抵消掉。
 *    概览必然带 macro_shadow_status（四种取值都写），所以它非空就等于「答过了」；
 *    只有对接不带这个字段的旧后端时才整组回退扫描行。
 *
 * 2. **差值只在同期时才显示。** macroFit 来自实时概览，macroTechnicalGap 只有落库的
 *    扫描行算得出（它要该股的 market_fit_score）。扫描可能比最新一期宏观旧几个
 *    小时；两个数字各自都对，凑在一起却不是同一个测量时点。
 *
 * 导出是为了能真的跑它 —— 这条规则值得用行为断言钉住，而不是用正则看一眼源码。
 */
export function mergeMacroFields(
  detail: Partial<MacroFields>,
  strength: Partial<MacroFields> | null,
): MacroFields {
  const overviewAnswered =
    detail.macroShadowStatus !== null && detail.macroShadowStatus !== undefined;
  const source = overviewAnswered ? detail : strength;
  const gapIsSameSnapshot =
    // 概览没答 → 分数和差值都出自同一行，同期是构造保证的。
    !overviewAnswered
    || (strength?.macroSnapshotDate != null
      && detail.macroSnapshotDate === strength.macroSnapshotDate);
  return {
    macroFit: source?.macroFit ?? null,
    macroTailwind: source?.macroTailwind ?? null,
    macroFitConfidence: source?.macroFitConfidence ?? null,
    macroSupporting: source?.macroSupporting ?? [],
    macroOpposing: source?.macroOpposing ?? [],
    macroSnapshotDate: source?.macroSnapshotDate ?? null,
    macroTechnicalGap: gapIsSameSnapshot ? strength?.macroTechnicalGap ?? null : null,
    macroShadowStatus: detail.macroShadowStatus ?? strength?.macroShadowStatus ?? null,
  };
}

export function getDetail(ticker: string, force = false): Promise<StockDetail> {
  const symbol = quoteSymbol(ticker);
  return mockOr(
    () => {
      if (!fx.hasTicker(symbol)) throw new ApiError(404, __t('代码 {ticker} 不存在', { ticker: symbol }));
      return fx.getStockDetail(symbol);
    },
    async () => {
      // 两个请求互不依赖，同时发出。
      //
      // 之前是串行：先 await 核心行情，再去要强度补充 —— 两段往返首尾相接，
      // 而强度这一路根本不需要前一路的结果。并行之后总时长是两者的较大值，
      // 不再是两者之和。
      //
      // 可选补充不得拖住核心详情（审计 P2-32）：失败已经被捕获，但请求缓慢或
      // 悬挂时整个详情对象仍会一直等它。截止时间从**发起时刻**起算，
      // 这也是并行之后才成立的口径。
      const detailPromise = stocksApi.detail(symbol, force);
      const supplementUrl = `/strength/stocks/${encodeURIComponent(symbol)}`;
      const supplementPromise: Promise<StrengthSupplement> = Promise.race([
        marketGet(supplementUrl, { ttlMs: 60_000, staleMs: 30 * 60_000, force })
          .then((body): StrengthSupplement => ({ kind: 'ok', body }))
          .catch((): StrengthSupplement => ({ kind: 'failed' })),
        new Promise<StrengthSupplement>((resolve) => {
          setTimeout(() => resolve({ kind: 'timeout' }), STRENGTH_SUPPLEMENT_DEADLINE_MS);
        }),
      ]);

      try {
        // 行情价格仍以 stocks 概览（Massive 主源）为准；强度快照只补其真实评分与缺失基本面。
        const detail = await detailPromise;
        const supplement = await supplementPromise;
        const strength =
          supplement.kind === 'ok' ? strengthRowToDetail(asRec(supplement.body)) : null;
        const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
        return {
          ...detail,
          sector: detail.sector || strength?.sector || '',
          strengthScore: finite(strength?.strengthScore) ? strength.strengthScore : detail.strengthScore,
          avgVolume: finite(detail.avgVolume) ? detail.avgVolume : strength?.avgVolume ?? detail.avgVolume,
          marketCap: finite(detail.marketCap) ? detail.marketCap : strength?.marketCap ?? detail.marketCap,
          pe: detail.pe ?? strength?.pe ?? null,
          snapshotScope: 'full' as const,
          // 宏观适配以**概览**为主源：概览对每只在主题表里的票都算得出，而
          // /strength/stocks/{t} 只回答公开快照 top 切片里的代码，其余 404 ——
          // 之前拿它当主源，AMD、SLB 等约 190 只票就都显示「暂无宏观读数」。
          // 整组字段同源同期，规则见 mergeMacroFields。
          ...mergeMacroFields(detail, strength),
        };
      } catch (e) {
        // 只有明确的公开快照边界才允许回退扫描行；供应方错误不能被伪装成基础行情成功。
        if (!(e instanceof ApiError) || e.code !== 503 || e.bizCode !== 'public_snapshot_unavailable') throw e;
        // 上面已经发出去的那次强度请求可以直接复用，不必再发一次。
        // **只有超时**才值得重发：那次请求可能还在飞，marketGet 会让两者共用同一个
        // in-flight promise。明确失败（404 / 503）重发一次结果一样，纯属白跑。
        const supplement = await supplementPromise;
        const retried =
          supplement.kind === 'timeout'
            ? await marketGet(supplementUrl, { ttlMs: 60_000, staleMs: 30 * 60_000, force })
                .then((body): StrengthSupplement => ({ kind: 'ok', body }))
                .catch((): StrengthSupplement => ({ kind: 'failed' }))
            : supplement;
        const fallback = retried.kind === 'ok' ? strengthRowToDetail(asRec(retried.body)) : null;
        if (fallback === null) throw e;
        return fallback;
      }
    },
  );
}

export function getDetailChart(ticker: string, range: ChartRange, force = false): Promise<StockChartEx> {
  const symbol = quoteSymbol(ticker);
  return mockOr(
    () => {
      if (!fx.hasTicker(symbol)) throw new ApiError(404, __t('代码 {ticker} 不存在', { ticker: symbol }));
      return fx.getStockChartEx(symbol, range);
    },
    // 契约 range ∈ 5m|15m|1h|1d|1w：界面与后端周期一一对应。
    () =>
      marketGet(
        `/stocks/${encodeURIComponent(symbol)}/chart?range=${range}&adjustment=raw`,
        { ttlMs: 60_000, staleMs: 60 * 60_000, force },
      ).then((d) =>
        mapChartEx(d, symbol, range),
      ),
  );
}

type TrendBiasScoreKey = keyof StockTrendBias['scores'];
type TrendBiasLabel = StockTrendBias['trend_bias_label'] | '数据不足';

export interface StockTrendBiasView {
  ticker: string;
  trend_bias_score: number | null;
  trend_bias_label: TrendBiasLabel;
  trend_bias_status: StockTrendBias['trend_bias_status'];
  trend_bias_coverage: number | null;
  trend_bias_missing_components: string[];
  scores: Record<TrendBiasScoreKey, number | null>;
  factors: TrendBiasFactor[];
  as_of: string;
}

const SCORE_KEYS: TrendBiasScoreKey[] = ['trend', 'momentum', 'volume', 'volatility'];

const FACTOR_SIGNALS: {
  key: TrendBiasScoreKey;
  label: string;
  candidates: string[];
}[] = [
  {
    key: 'trend',
    label: __t('趋势'),
    candidates: ['relative_strength_spy', 'sma50_dist', 'sma20_dist'],
  },
  {
    key: 'momentum',
    label: __t('动量'),
    candidates: ['rsi14', 'macd_hist', 'return_20d'],
  },
  {
    key: 'volume',
    label: __t('量能'),
    candidates: ['obv_divergence', 'volume_zscore', '_volume_ratio'],
  },
  {
    key: 'volatility',
    label: __t('波动'),
    candidates: ['atr_percentile', 'atm_iv_percent'],
  },
];

const PERCENT_SIGNALS = new Set([
  'relative_strength_spy',
  'sma20_dist',
  'sma50_dist',
  'sma200_dist',
  'return_20d',
  'atr_percentile',
  'atm_iv_percent',
]);

function boundedScore(value: number | null): number | null {
  return value === null ? null : Math.max(0, Math.min(100, value));
}

function meanAvailableScores(values: (number | null)[]): number | null {
  const available = values.filter((value): value is number => value !== null);
  if (available.length === 0) return null;
  return Math.round((available.reduce((sum, value) => sum + value, 0) / available.length) * 10) / 10;
}

/**
 * Backend signal scorers expose two bounded directional sides. For the
 * trend-oriented indicators below, top_score rises with positive direction
 * and bottom_score rises with negative direction, so their midpoint
 * difference maps deterministically onto the panel's 0–100 scale.
 */
function directionalSignalScore(signals: Rec, key: string): number | null {
  const signal = asRec(signals[key]);
  const top = pickN(signal, 'top_score');
  const bottom = pickN(signal, 'bottom_score');
  if (top === null || bottom === null) return null;
  return boundedScore(50 + (top - bottom) / 2);
}

function scoresFromSignals(body: Rec): Record<TrendBiasScoreKey, number | null> {
  const signals = asRec(body.signals);
  return {
    trend: meanAvailableScores(
      ['sma20_dist', 'sma50_dist', 'sma200_dist', 'relative_strength_spy']
        .map((key) => directionalSignalScore(signals, key)),
    ),
    momentum: meanAvailableScores(
      ['rsi14', 'return_20d']
        .map((key) => directionalSignalScore(signals, key)),
    ),
    // OBV 背离自身带有价格与成交量的方向；单纯放量不等于利多或利空。
    volume: directionalSignalScore(signals, 'obv_divergence'),
    // 后端直接返回 1 年真实分位，无需二次推断方向。
    volatility: boundedScore(pickN(asRec(signals.atr_percentile), 'value')),
  };
}

function signalTone(key: string, value: number): TrendBiasFactor['tone'] {
  if (key === 'rsi14') {
    return value >= 55 ? 'bullish' : value <= 45 ? 'bearish' : 'neutral';
  }
  if (
    key === 'relative_strength_spy'
    || key === 'sma20_dist'
    || key === 'sma50_dist'
    || key === 'sma200_dist'
    || key === 'return_20d'
    || key === 'macd_hist'
    || key === 'obv_divergence'
  ) {
    return value > 0 ? 'bullish' : value < 0 ? 'bearish' : 'neutral';
  }
  return 'neutral';
}

function signalReading(label: string, key: string, value: number): string {
  const formatted = value.toLocaleString(localeTag(), {
    maximumFractionDigits: 2,
  });
  return `${label}：${formatted}${PERCENT_SIGNALS.has(key) ? '%' : ''}`;
}

function mapProvidedFactors(body: Rec): TrendBiasFactor[] {
  const allowedKeys = new Set<TrendBiasScoreKey>(SCORE_KEYS);
  const allowedTones = new Set<TrendBiasFactor['tone']>(['bullish', 'neutral', 'bearish']);
  return unwrap(body, 'factors').flatMap((item) => {
    const key = pickS(item, 'key') as TrendBiasScoreKey | null;
    const tone = pickS(item, 'tone') as TrendBiasFactor['tone'] | null;
    const label = pickLabel(item, 'label');
    const reading = pickLabel(item, 'reading');
    if (
      key === null
      || !allowedKeys.has(key)
      || tone === null
      || !allowedTones.has(tone)
      || label === null
      || reading === null
    ) {
      return [];
    }
    return [{ key, tone, label, reading }];
  });
}

function factorsFromSignals(body: Rec): TrendBiasFactor[] {
  const signals = asRec(body.signals);
  return FACTOR_SIGNALS.flatMap((spec) => {
    for (const signalKey of spec.candidates) {
      const signal = asRec(signals[signalKey]);
      const value = pickN(signal, 'value');
      if (value === null) continue;
      const sourceLabel = pickLabel(signal, 'label') ?? signalKey;
      return [{
        key: spec.key,
        label: spec.label,
        reading: signalReading(sourceLabel, signalKey, value),
        tone: signalTone(signalKey, value),
      }];
    }
    return [];
  });
}

/** Live /signals/stock response → null-safe detail-panel contract. */
export function mapTrendBiasResponse(body: unknown, ticker: string): StockTrendBiasView {
  const raw = asRec(body);
  const rawScores = asRec(raw.scores);
  const trendBiasScore = boundedScore(pickN(raw, 'trend_bias_score'));
  const derivedScores = scoresFromSignals(raw);
  const scores = Object.fromEntries(
    SCORE_KEYS.map((key) => [
      key,
      boundedScore(pickN(rawScores, key)) ?? derivedScores[key],
    ]),
  ) as Record<TrendBiasScoreKey, number | null>;

  const rawLabel = pickS(raw, 'trend_bias_label');
  const trendBiasLabel: TrendBiasLabel =
    rawLabel === '偏多' || rawLabel === '中性' || rawLabel === '偏空' || rawLabel === '数据不足'
      ? rawLabel
      : trendBiasScore === null
        ? '数据不足'
        : trendBiasScore >= 58
          ? '偏多'
          : trendBiasScore <= 42
            ? '偏空'
            : '中性';

  const missingComponents = Array.isArray(raw.trend_bias_missing_components)
    ? raw.trend_bias_missing_components.filter((value): value is string => typeof value === 'string')
    : [];
  const coverage = boundedScore(
    (() => {
      const value = pickN(raw, 'trend_bias_coverage');
      return value === null ? null : value * 100;
    })(),
  );
  const rawStatus = pickS(raw, 'trend_bias_status');
  const hasMissingSubscores = SCORE_KEYS.some((key) => scores[key] === null);
  const trendBiasStatus: StockTrendBias['trend_bias_status'] =
    trendBiasScore === null || rawStatus === 'insufficient_data'
      ? 'insufficient_data'
      : rawStatus === 'degraded'
        || hasMissingSubscores
        || missingComponents.length > 0
        || (coverage !== null && coverage < 100)
        ? 'degraded'
        : 'ok';

  const mergedFactors = new Map<TrendBiasScoreKey, TrendBiasFactor>();
  for (const factor of mapProvidedFactors(raw)) mergedFactors.set(factor.key, factor);
  for (const factor of factorsFromSignals(raw)) {
    if (!mergedFactors.has(factor.key)) mergedFactors.set(factor.key, factor);
  }

  return {
    ticker: pickS(raw, 'ticker') ?? ticker.toUpperCase(),
    trend_bias_score: trendBiasScore,
    trend_bias_label: trendBiasLabel,
    trend_bias_status: trendBiasStatus,
    trend_bias_coverage: coverage,
    trend_bias_missing_components: missingComponents,
    scores,
    factors: SCORE_KEYS.flatMap((key) => {
      const factor = mergedFactors.get(key);
      return factor ? [factor] : [];
    }),
    as_of: pickS(raw, 'as_of') ?? '',
  };
}

export function getTrendBias(ticker: string, force = false): Promise<StockTrendBiasView> {
  const symbol = quoteSymbol(ticker);
  return mockOr<StockTrendBiasView>(
    () => {
      if (!fx.hasTicker(symbol)) throw new ApiError(404, __t('代码 {ticker} 不存在', { ticker: symbol }));
      return mapTrendBiasResponse(fx.getStockTrendBias(symbol), symbol);
    },
    () =>
      marketGet(`/signals/stock/${encodeURIComponent(symbol)}`, {
        ttlMs: 60_000,
        staleMs: 30 * 60_000,
        force,
      }).then((body) => mapTrendBiasResponse(body, symbol)),
  );
}

/** signal_analysis 任务（owner）；轮询/取消复用 aiJobsApi.get / cancel */
export function createSignalAnalysisJob(ticker: string): Promise<AiJob> {
  const symbol = quoteSymbol(ticker);
  return mockOr(
    () => {
      if (!fx.hasTicker(symbol)) throw new ApiError(404, __t('代码 {ticker} 不存在', { ticker: symbol }));
      const d = fx.getStockDetail(symbol);
      const b = fx.getStockTrendBias(symbol);
      // mock 模式下模拟「模型写的分析正文」，与真实 AI 输出同样不做界面翻译（如实保留原文）。
      const ivTone = d.ivPercentile >= 60 ? '偏贵' : d.ivPercentile <= 40 ? '相对便宜' : '中性';
      const text =
        `${symbol} 模型分析完成：趋势偏向分 ${b.trend_bias_score}（${b.trend_bias_label}），` +
        `分项读数 趋势 ${b.scores.trend} / 动量 ${b.scores.momentum} / 量能 ${b.scores.volume} / 波动 ${b.scores.volatility}。` +
        `现价 ${d.price.toFixed(2)} 美元，IV 百分位 ${d.ivPercentile}%，期权定价${ivTone}。` +
        `近端观察 MA20 附近的量能配合与突破延续性；若量价背离放大，偏向读数将快速回落。` +
        `以上为方向性研究结论，非收益预测。`;
      return fx2.createAiJob('signal-analysis' as AiJob['kind'], text);
    },
    // 契约：owner+SO → 202 + Location（job_id 可能仅在 Location 头）
    () => postAiJob(`/signals/stock/${encodeURIComponent(symbol)}/ai-analysis`, { force: false }),
  );
}

/**
 * 预取详情面板必然会要的两个只读快照。
 *
 * K 线自己取自己的 bars，详情对象里一个字段都不用；但它挂在 `loading` 分支之后，
 * 详情请求回来之前根本没被挂上 —— 两段互不依赖的往返被排成了串行。信号面板同理，
 * 而且 TrendBiasPanel 和 SignalList 读的是同一个 `/signals/stock/{t}`。
 *
 * 这里只是把请求**提前发出**，不是新增请求：图表和信号挂载时命中的是 marketGet 的
 * in-flight promise 或它 60 秒的缓存，所以请求总数不变，只是早了一个往返。
 *
 * 刻意不 force：强制读会消耗 owner 手动拉取后那一次性的退避豁免，把它花在预取上，
 * 真正要用它的那次读取就没得用了。
 *
 * 失败在这里吞掉。预取不是数据来源 —— 各面板自己那次调用共享的是同一个 promise，
 * 该失败照样失败，错误态仍由面板自己呈现。
 */
/* ---------------- 技术结构（/stocks/{t}/technical） ---------------- */

/** 摆动点 t（epoch 秒/毫秒）→ 与 mapBar 同一 ISO 口径，图表按同源字符串对齐 */
function mapSwingPoint(raw: Rec): TechSwingPoint | null {
  const price = pickN(raw, 'price');
  const tradeDate = pickS(raw, 'trade_date') ?? '';
  const rawTime = raw.t;
  let iso = pickS(raw, 't');
  if (iso === null && typeof rawTime === 'number' && Number.isFinite(rawTime)) {
    const epoch = rawTime >= 100_000_000_000 ? rawTime : rawTime * 1_000;
    const parsed = new Date(epoch);
    iso = Number.isFinite(parsed.getTime()) ? parsed.toISOString() : null;
  }
  if (iso === null && tradeDate === '') return null;
  return { t: iso ?? tradeDate, trade_date: tradeDate || (iso ?? '').slice(0, 10), price };
}

function mapSwingList(body: Rec, key: string): TechSwingPoint[] {
  return unwrap(body, key).flatMap((item) => {
    const point = mapSwingPoint(item);
    return point ? [point] : [];
  });
}

/**
 * live 归一：数值原样、结构/形态/量价的中文标签**保持原文透传**——渲染处统一
 * 过 t()（与雷达 structure_label 同纪律，勿在此 pickLabel，双侧翻译会让
 * ja 落空）。
 */
function mapTechnicalStructure(body: unknown): TechnicalStructure {
  const r = asRec(body);
  const pa = asRec(r.price_action);
  const vp = asRec(r.vol_price);
  const tech = asRec(r.technicals);
  const macd = asRec(tech.macd);
  const overlays = asRec(r.chart_overlays);
  const baseRaw = r.base === null || r.base === undefined ? null : asRec(r.base);
  const strList = (rec: Rec, key: string): string[] =>
    unwrap(rec, key).flatMap((item) => (typeof item === 'string' ? [item] : []));
  const base = baseRaw === null
    ? null
    : {
        pivot_id: pickS(baseRaw, 'pivot_id'),
        pivot_price: pickN(baseRaw, 'pivot_price'),
        resistance_low: pickN(baseRaw, 'resistance_low'),
        resistance_high: pickN(baseRaw, 'resistance_high'),
        support_low: pickN(baseRaw, 'support_low'),
        support_high: pickN(baseRaw, 'support_high'),
        invalidation_price: pickN(baseRaw, 'invalidation_price'),
        base_start: pickS(baseRaw, 'base_start'),
        base_end: pickS(baseRaw, 'base_end'),
        resistance_touches: pickN(baseRaw, 'resistance_touches'),
        quality: pickN(baseRaw, 'quality'),
        base_duration_days: pickN(baseRaw, 'base_duration_days'),
        quality_coverage: (() => {
          const cov = asRec(baseRaw.quality_coverage);
          const observed = pickN(cov, 'observed');
          const total = pickN(cov, 'total');
          if (observed === null || total === null) return null;
          return {
            observed,
            total,
            missing: unwrap(cov, 'missing').flatMap((v) => (typeof v === 'string' ? [v] : [])),
          };
        })(),
        window_agreement: pickN(baseRaw, 'window_agreement'),
        windows_scanned: pickN(baseRaw, 'windows_scanned'),
      };
  const stateRaw = r.base_state === null || r.base_state === undefined ? null : asRec(r.base_state);
  const BASE_STATUSES = new Set(['in_base', 'at_resistance', 'breakout', 'below_support', 'failed']);
  const stateStatus = stateRaw === null ? null : pickS(stateRaw, 'status');
  const base_state = stateRaw !== null && stateStatus !== null && BASE_STATUSES.has(stateStatus)
    ? {
        status: stateStatus as TechBaseStatus,
        reference_close: pickN(stateRaw, 'reference_close'),
        reference_date: pickS(stateRaw, 'reference_date'),
        provisional: stateRaw.provisional === true,
      }
    : null;
  const mapPatternEvents = (rec: Rec): TechnicalStructure['price_action']['pattern_events'] =>
    unwrap(rec, 'pattern_events').flatMap((item) => {
      const pattern = pickS(item, 'pattern');
      const label = pickS(item, 'label');
      if (pattern === null || label === null) return [];
      return [{
        pattern,
        label,
        bars_ago: pickN(item, 'bars_ago'),
        trade_date: pickS(item, 'trade_date'),
      }];
    });
  const lastBarRaw = asRec(r.last_bar);
  const lastBarPoint = mapSwingPoint(lastBarRaw);
  // 形态列表只从 chart_analysis.overlays 走一条路；负载里的 auto_patterns 顶层
  // 副本没有任何组件读，后端已停发。带 option/graphic 的负载一律当不可信丢弃。
  const analysisRaw = asRec(r.chart_analysis ?? r.chartAnalysis);
  const chartAnalysis = Object.keys(analysisRaw).length && analysisRaw.option == null && analysisRaw.graphic == null
    ? analysisRaw
    : null;
  return {
    base,
    base_state,
    price_action: {
      status: pickS(pa, 'status') ?? 'missing_data',
      score: pickN(pa, 'score'),
      structure: pickS(pa, 'structure') ?? 'range',
      structure_label: pickS(pa, 'structure_label') ?? '',
      swing_highs: mapSwingList(pa, 'swing_highs'),
      swing_lows: mapSwingList(pa, 'swing_lows'),
      resistance: pickN(pa, 'resistance'),
      support: pickN(pa, 'support'),
      resistance_dist_pct: pickN(pa, 'resistance_dist_pct'),
      support_dist_pct: pickN(pa, 'support_dist_pct'),
      patterns: strList(pa, 'patterns'),
      pattern_labels: strList(pa, 'pattern_labels'),
      pattern_events: mapPatternEvents(pa),
      spring: pa.spring === true,
      upthrust: pa.upthrust === true,
      spring_bars_ago: pickN(pa, 'spring_bars_ago'),
      spring_trade_date: pickS(pa, 'spring_trade_date'),
      upthrust_bars_ago: pickN(pa, 'upthrust_bars_ago'),
      upthrust_trade_date: pickS(pa, 'upthrust_trade_date'),
      tags: strList(pa, 'tags'),
    },
    vol_price: {
      status: pickS(vp, 'status') ?? 'missing_data',
      setup_type: pickS(vp, 'setup_type') ?? '',
      setup_label: pickS(vp, 'setup_label') ?? '',
      effort: pickN(vp, 'effort'),
      result: pickN(vp, 'result'),
      // null = 数据不足未测：不许折成 0 冒充「风险为零」
      breakout_quality_adjustment: pickN(vp, 'breakout_quality_adjustment'),
      false_breakout_risk: pickN(vp, 'false_breakout_risk'),
      tags: strList(vp, 'tags'),
    },
    technicals: {
      rsi14: pickN(tech, 'rsi14'),
      rsi_score: pickN(tech, 'rsi_score'),
      macd: {
        histogram: pickN(macd, 'histogram'),
        histogram_pct: pickN(macd, 'histogram_pct'),
        direction_pct: pickN(macd, 'direction_pct'),
      },
      trend_efficiency_63d: pickN(tech, 'trend_efficiency_63d'),
      ma50_slope_pct_21d: pickN(tech, 'ma50_slope_pct_21d'),
      return_stability_20d: pickN(tech, 'return_stability_20d'),
      range_position_60d: pickN(tech, 'range_position_60d'),
      range_persistence_fast: pickN(tech, 'range_persistence_fast'),
      range_persistence_slow: pickN(tech, 'range_persistence_slow'),
    },
    chart_overlays: {
      swing_highs: mapSwingList(overlays, 'swing_highs'),
      swing_lows: mapSwingList(overlays, 'swing_lows'),
      resistance_high: pickN(overlays, 'resistance_high'),
      resistance_low: pickN(overlays, 'resistance_low'),
      support_low: pickN(overlays, 'support_low'),
      invalidation_price: pickN(overlays, 'invalidation_price'),
      pivot_price: pickN(overlays, 'pivot_price'),
      base_start: pickS(overlays, 'base_start'),
      base_end: pickS(overlays, 'base_end'),
      base_status: (() => {
        const status = pickS(overlays, 'base_status');
        return status !== null && BASE_STATUSES.has(status) ? (status as TechBaseStatus) : null;
      })(),
    },
    basis: pickS(r, 'basis') ?? 'raw_daily',
    data_through: pickS(r, 'data_through'),
    last_bar: lastBarPoint === null
      ? null
      : {
          t: lastBarPoint.t,
          trade_date: lastBarPoint.trade_date,
          closed: lastBarRaw.closed === true,
        },
    series_break_at: pickS(r, 'series_break_at'),
    as_of: pickS(r, 'as_of', 'asOf'),
    chart_analysis: chartAnalysis,
  };
}

export function getTechnicalStructure(ticker: string, force = false): Promise<TechnicalStructure> {
  const symbol = quoteSymbol(ticker);
  return mockOr(
    () => {
      if (!fx.hasTicker(symbol)) throw new ApiError(404, __t('代码 {ticker} 不存在', { ticker: symbol }));
      const row = fx2.getTechnicalStructure(symbol);
      // 日线分析包线上挂在 /technical 上（分钟包挂 /chart）。data_through 跟着
      // 分析包的纽约交易日走：闸门要求两者逐字相等，用 UTC 切片会差一天。
      const analysis = fx.buildChartAnalysis(symbol, fx.getStockChartEx(symbol, '1d').bars, '1d');
      return analysis
        ? { ...row, data_through: String(analysis.dataThrough), chart_analysis: analysis }
        : row;
    },
    () =>
      marketGet(`/stocks/${encodeURIComponent(symbol)}/technical`, {
        ttlMs: 5 * 60_000,
        staleMs: 60 * 60_000,
        force,
      }).then((d) => mapTechnicalStructure(d)),
  );
}

export function prefetchStockDetailPanels(ticker: string): void {
  const symbol = quoteSymbol(ticker);
  void getDetailChart(symbol, DEFAULT_CHART_RANGE).catch(() => {});
  void getTrendBias(symbol).catch(() => {});
  void getTechnicalStructure(symbol).catch(() => {});
}
