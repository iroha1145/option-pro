/**
 * 股票详情域数据助手（components/detail 局部）
 * 契约对齐但 api/modules 尚未覆盖的端点在此经 mockOr 接入：
 * - GET /api/stocks/{t}/chart（ChartBar quote_only / as_of / _stale）
 * - GET /api/signals/stock/{t}（trend_bias_* 完整形状）
 * - POST /api/signals/stock/{t}/ai-analysis（signal_analysis 任务，202）
 * - GET /api/strength/stocks/{t}（概览 503 时的基础行情回退：扫描行快照，匿名可用）
 * mock 模式下代码不存在抛 404（整页形态 404 空态）。
 */
import { ApiError, get, mockOr } from '@/api/client';
import { asRec, pickN, pickS, unwrap, type Rec } from '@/api/live';
import { CHART_RANGE_MAP, ma20Of, mapBar } from '@/api/modules/stocks';
import { stocksApi } from '@/api/modules/stocks';
import { postAiJob } from '@/api/modules/ai-jobs';
import * as fx from '@/mocks/fixtures';
import * as fx2 from '@/mocks/fixtures2';
import type { AiJob, StockChart, StockDetail } from '@/api/types';
import type { ChartBarEx, StockChartEx, StockTrendBias } from '@/mocks/fixtures';

export type ChartRange = StockChart['range'];
export const CHART_RANGES: ChartRange[] = ['1D', '5D', '1M', '6M', '1Y', 'ALL'];

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
    snapshotScope: 'strength-row',
  };
}

export function getDetail(ticker: string): Promise<StockDetail> {
  const t = ticker.toUpperCase();
  return mockOr(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      return fx.getStockDetail(t);
    },
    async () => {
      try {
        // 概览契约（snake→camel 归一在 stocks 模块完成）
        return { ...(await stocksApi.detail(t)), snapshotScope: 'full' as const };
      } catch (e) {
        // 焦点池外（匿名 503 public_snapshot_unavailable）：回退强度扫描行基础行情
        if (!(e instanceof ApiError) || e.code !== 503) throw e;
        const body = await get(`/strength/stocks/${encodeURIComponent(t)}`).catch(() => null);
        const fallback = body !== null ? strengthRowToDetail(asRec(body)) : null;
        if (fallback === null) throw e;
        return fallback;
      }
    },
  );
}

export function getDetailChart(ticker: string, range: ChartRange): Promise<StockChartEx> {
  const t = ticker.toUpperCase();
  return mockOr(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      return fx.getStockChartEx(t, range);
    },
    // 契约 range ∈ 5m|15m|1h|1d|1w：UI 挡位经 CHART_RANGE_MAP 显式映射（有损见 stocks 模块注释）
    () =>
      get(`/stocks/${encodeURIComponent(t)}/chart?range=${CHART_RANGE_MAP[range]}&adjustment=raw`).then((d) =>
        mapChartEx(d, t, range),
      ),
  );
}

export function getTrendBias(ticker: string): Promise<StockTrendBias> {
  const t = ticker.toUpperCase();
  return mockOr(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      return fx.getStockTrendBias(t);
    },
    () => get(`/signals/stock/${encodeURIComponent(t)}`),
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
