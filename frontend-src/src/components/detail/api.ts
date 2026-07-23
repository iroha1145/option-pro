/**
 * 股票详情域数据助手（components/detail 局部）
 * 契约对齐但 api/modules 尚未覆盖的端点在此经 mockOr 接入：
 * - GET /api/stocks/{t}/chart（ChartBar quote_only / as_of / _stale）
 * - GET /api/signals/stock/{t}（trend_bias_* 完整形状）
 * - POST /api/signals/stock/{t}/ai-analysis（signal_analysis 任务，202）
 * mock 模式下代码不存在抛 404（整页形态 404 空态）。
 */
import { ApiError, get, mockOr } from '@/api/client';
import { asRec, pickS, unwrap, type Rec } from '@/api/live';
import { CHART_RANGE_MAP, ma20Of, mapBar } from '@/api/modules/stocks';
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

export function getDetail(ticker: string): Promise<StockDetail> {
  const t = ticker.toUpperCase();
  return mockOr(
    () => {
      if (!fx.hasTicker(t)) throw new ApiError(404, `代码 ${t} 不存在`);
      return fx.getStockDetail(t);
    },
    () => get(`/stocks/${encodeURIComponent(t)}`),
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
