/** 信号域：GET /api/signals/market · GET /api/signals/stock/{t} */
import { get, mockOr } from '../client';
import { marketGet } from '../marketRead';
import { asRec, pickN, pickS } from '../live';
import * as fx from '@/mocks/fixtures';
import type { MarketSignalsSnapshot, Signal, SignalType } from '../types';

/** 契约 signals 为指标对象；只把有明确高分方向的指标投影成“活跃信号”。 */
function mapStockSignals(d: unknown): Signal[] {
  const env = asRec(d);
  const signals = asRec(env.signals);
  const at = pickS(env, 'as_of') ?? '';
  return Object.entries(signals).flatMap(([key, raw]) => {
    if (key.startsWith('_')) return [];
    const metric = asRec(raw);
    const value = pickN(metric, 'value');
    if (value === null) return [];
    const topScore = pickN(metric, 'top_score', 'topScore');
    const bottomScore = pickN(metric, 'bottom_score', 'bottomScore');
    const activeScore = Math.max(topScore ?? -1, bottomScore ?? -1);
    if (activeScore < 40) return [];
    const label = pickS(metric, 'label') ?? key;
    let type: SignalType = 'pullback';
    if (key.includes('volume')) type = 'volume';
    else if (key.includes('iv')) type = 'iv-spike';
    else if (key.includes('sma')) type = 'ma-touch';
    else if (key.includes('return') || key.includes('relative_strength') || key.includes('macd')) {
      type = value >= 0 ? 'breakout' : 'pullback';
    }
    return {
      type,
      label: `${label} ${value} · ${topScore !== null && topScore >= (bottomScore ?? -1) ? '顶部风险' : '底部修复'} ${activeScore}`,
      at,
    };
  });
}

/** 契约 /signals/market = {signals:{key:{value,label,top_score,bottom_score}},scores:{…}}。 */
export function mapMarketSignals(d: unknown): MarketSignalsSnapshot {
  const env = asRec(d);
  const rawSignals = asRec(env.signals);
  const scores = asRec(env.scores);
  const source = asRec(rawSignals._source_status);
  const metrics = Object.entries(rawSignals).flatMap(([key, raw]) => {
    if (key.startsWith('_')) return [];
    const metric = asRec(raw);
    const value = pickN(metric, 'value');
    if (value === null) return [];
    return [{
      key,
      label: pickS(metric, 'label') ?? key,
      value,
      topScore: pickN(metric, 'top_score', 'topScore'),
      bottomScore: pickN(metric, 'bottom_score', 'bottomScore'),
    }];
  });
  return {
    metrics,
    topScore: pickN(scores, 'top_score', 'topScore'),
    bottomScore: pickN(scores, 'bottom_score', 'bottomScore'),
    topLabel: pickS(scores, 'top_label', 'topLabel'),
    bottomLabel: pickS(scores, 'bottom_label', 'bottomLabel'),
    dataQuality: pickN(scores, 'data_quality', 'dataQuality'),
    sourceStatus: pickS(source, 'value'),
    asOf: pickS(env, 'as_of', 'asOf'),
    cached: env._cached === true,
  };
}

function mapMockMarketSignals(): MarketSignalsSnapshot {
  const mock = fx.getMarketSignals();
  return {
    metrics: mock.byType.map((item) => ({
      key: item.type,
      label: item.label,
      value: item.today,
      topScore: null,
      bottomScore: null,
    })),
    topScore: null,
    bottomScore: null,
    topLabel: null,
    bottomLabel: null,
    dataQuality: null,
    sourceStatus: 'mock',
    asOf: new Date().toISOString(),
    cached: false,
  };
}

export const signalsApi = {
  market: (): Promise<MarketSignalsSnapshot> =>
    mockOr(mapMockMarketSignals, () => get('/signals/market').then(mapMarketSignals)),
  stock: (ticker: string): Promise<Signal[]> =>
    mockOr(
      () => fx.getStockSignals(ticker),
      () =>
        marketGet(`/signals/stock/${encodeURIComponent(ticker)}`, {
          ttlMs: 60_000,
          staleMs: 30 * 60_000,
        }).then(mapStockSignals),
    ),
};
