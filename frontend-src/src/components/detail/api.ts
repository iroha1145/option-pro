/**
 * 股票详情域数据助手（components/detail 局部）
 * 契约对齐但 api/modules 尚未覆盖的端点在此经 mockOr 接入：
 * - GET /api/stocks/{t}/chart（ChartBar quote_only / as_of / _stale）
 * - GET /api/signals/stock/{t}（trend_bias_* 完整形状）
 * - POST /api/signals/stock/{t}/ai-analysis（signal_analysis 任务，202）
 * - GET /api/strength/stocks/{t}（概览 503 时的基础行情回退：扫描行快照，匿名可用）
 * mock 模式下代码不存在抛 404（整页形态 404 空态）。
 */
import { ApiError, mockOr } from '@/api/client';
import { marketGet } from '@/api/marketRead';
import { asRec, pickN, pickS, unwrap, type Rec } from '@/api/live';
import { ma20Of, mapBar } from '@/api/modules/stocks';
import { stocksApi } from '@/api/modules/stocks';
import { postAiJob } from '@/api/modules/ai-jobs';
import * as fx from '@/mocks/fixtures';
import * as fx2 from '@/mocks/fixtures2';
import type { AiJob, StockChart, StockDetail } from '@/api/types';
import type {
  ChartBarEx,
  StockChartEx,
  StockTrendBias,
  TrendBiasFactor,
} from '@/mocks/fixtures';

export type ChartRange = StockChart['range'];
export const CHART_RANGES: { value: ChartRange; label: string }[] = [
  { value: '5m', label: '5分' },
  { value: '15m', label: '15分' },
  { value: '1h', label: '1小时' },
  { value: '1d', label: '日线' },
  { value: '1w', label: '周线' },
];

/** 契约 {bars:[{t,o,h,l,c,v,quote_only}], as_of, _stale?} → StockChartEx（字段名 1:1） */
function mapChartEx(body: unknown, ticker: string, range: ChartRange): StockChartEx {
  const r = asRec(body);
  const bars = unwrap(body, 'bars').map((b: Rec) => mapBar<ChartBarEx>(b));
  return {
    ticker,
    range,
    bars,
    ma20: ma20Of(bars),
    as_of: pickS(r, 'as_of', 'asOf') ?? '',
    ...(r._stale === true ? { _stale: true } : {}),
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
    name: pickS(row, 'name') ?? ticker,
    sector: pickS(row, 'sector_name', 'primary_sector_name') ?? '',
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
  };
}

/** 强度补充的独立截止时间；超过就先给核心行情，评分随下一次读取补齐。 */
const STRENGTH_SUPPLEMENT_DEADLINE_MS = 4_000;

export function getDetail(ticker: string, force = false): Promise<StockDetail> {
  const t = ticker.toUpperCase();
  return mockOr(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      return fx.getStockDetail(t);
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
      const detailPromise = stocksApi.detail(t, force);
      const strengthPromise = Promise.race([
        marketGet(
          `/strength/stocks/${encodeURIComponent(t)}`,
          { ttlMs: 60_000, staleMs: 30 * 60_000, force },
        ).catch(() => null),
        new Promise<null>((resolve) => {
          setTimeout(() => resolve(null), STRENGTH_SUPPLEMENT_DEADLINE_MS);
        }),
      ]);

      try {
        // 行情价格仍以 stocks 概览（Massive 主源）为准；强度快照只补其真实评分与缺失基本面。
        const detail = await detailPromise;
        const strengthBody = await strengthPromise;
        const strength = strengthBody !== null ? strengthRowToDetail(asRec(strengthBody)) : null;
        const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
        return {
          ...detail,
          sector: detail.sector || strength?.sector || '',
          strengthScore: finite(strength?.strengthScore) ? strength.strengthScore : detail.strengthScore,
          avgVolume: finite(detail.avgVolume) ? detail.avgVolume : strength?.avgVolume ?? detail.avgVolume,
          marketCap: finite(detail.marketCap) ? detail.marketCap : strength?.marketCap ?? detail.marketCap,
          pe: detail.pe ?? strength?.pe ?? null,
          snapshotScope: 'full' as const,
        };
      } catch (e) {
        // 只有明确的公开快照边界才允许回退扫描行；供应方错误不能被伪装成基础行情成功。
        if (!(e instanceof ApiError) || e.code !== 503 || e.bizCode !== 'public_snapshot_unavailable') throw e;
        // 上面已经发出去的那次强度请求可以直接复用，不必再发一次。
        // 它超时会解析成 null，此时才真的重新要一次。
        const body =
          (await strengthPromise) ??
          (await marketGet(
            `/strength/stocks/${encodeURIComponent(t)}`,
            { ttlMs: 60_000, staleMs: 30 * 60_000, force },
          ).catch(() => null));
        const fallback = body !== null ? strengthRowToDetail(asRec(body)) : null;
        if (fallback === null) throw e;
        return fallback;
      }
    },
  );
}

export function getDetailChart(ticker: string, range: ChartRange, force = false): Promise<StockChartEx> {
  const t = ticker.toUpperCase();
  return mockOr(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      return fx.getStockChartEx(t, range);
    },
    // 契约 range ∈ 5m|15m|1h|1d|1w：界面与后端周期一一对应。
    () =>
      marketGet(
        `/stocks/${encodeURIComponent(t)}/chart?range=${range}&adjustment=raw`,
        { ttlMs: 60_000, staleMs: 60 * 60_000, force },
      ).then((d) =>
        mapChartEx(d, t, range),
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
    label: '趋势',
    candidates: ['relative_strength_spy', 'sma50_dist', 'sma20_dist'],
  },
  {
    key: 'momentum',
    label: '动量',
    candidates: ['rsi14', 'macd_hist', 'return_20d'],
  },
  {
    key: 'volume',
    label: '量能',
    candidates: ['obv_divergence', 'volume_zscore', '_volume_ratio'],
  },
  {
    key: 'volatility',
    label: '波动',
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
  const formatted = value.toLocaleString('zh-CN', {
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
    const label = pickS(item, 'label');
    const reading = pickS(item, 'reading');
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
      const sourceLabel = pickS(signal, 'label') ?? signalKey;
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
  const t = ticker.toUpperCase();
  return mockOr<StockTrendBiasView>(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      return mapTrendBiasResponse(fx.getStockTrendBias(t), t);
    },
    () =>
      marketGet(`/signals/stock/${encodeURIComponent(t)}`, {
        ttlMs: 60_000,
        staleMs: 30 * 60_000,
        force,
      }).then((body) => mapTrendBiasResponse(body, t)),
  );
}

/** signal_analysis 任务（owner）；轮询/取消复用 aiJobsApi.get / cancel */
export function createSignalAnalysisJob(ticker: string): Promise<AiJob> {
  const t = ticker.toUpperCase();
  return mockOr(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      const d = fx.getStockDetail(t);
      const b = fx.getStockTrendBias(t);
      const ivTone = d.ivPercentile >= 60 ? '偏贵' : d.ivPercentile <= 40 ? '相对便宜' : '中性';
      const text =
        `${t} 模型分析完成：趋势偏向分 ${b.trend_bias_score}（${b.trend_bias_label}），` +
        `分项读数 趋势 ${b.scores.trend} / 动量 ${b.scores.momentum} / 量能 ${b.scores.volume} / 波动 ${b.scores.volatility}。` +
        `现价 ${d.price.toFixed(2)} 美元，IV 百分位 ${d.ivPercentile}%，期权定价${ivTone}。` +
        `近端观察 MA20 附近的量能配合与突破延续性；若量价背离放大，偏向读数将快速回落。` +
        `以上为方向性研究结论，非收益预测。`;
      return fx2.createAiJob('signal-analysis' as AiJob['kind'], text);
    },
    // 契约：owner+SO → 202 + Location（job_id 可能仅在 Location 头）
    () => postAiJob(`/signals/stock/${encodeURIComponent(t)}/ai-analysis`, { force: false }),
  );
}
