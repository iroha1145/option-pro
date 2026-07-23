/** 信号域：GET /api/signals/market · GET /api/signals/stock/{t} */
import { get, mockOr } from '../client';
import { pickN, pickS, unwrap } from '../live';
import * as fx from '@/mocks/fixtures';
import type { MarketSignalsSummary, Signal, SignalType } from '../types';

const SIGNAL_TYPES: SignalType[] = ['breakout', 'volume', 'pullback', 'ma-touch', 'gap', 'iv-spike'];

/** 契约 {trend_bias_*, scores, signals:[...]} → UI Signal[]（信封解包 + 字段容错） */
function mapStockSignals(d: unknown): Signal[] {
  return unwrap(d, 'signals').map((r) => {
    const t = pickS(r, 'type') ?? 'breakout';
    return {
      type: (SIGNAL_TYPES as string[]).includes(t) ? (t as SignalType) : 'breakout',
      label: pickS(r, 'label') ?? t,
      at: pickS(r, 'at', 'triggered_at') ?? '',
    };
  });
}

/** 契约 /signals/market → UI MarketSignalsSummary（snake/camel 容错，缺失不编造） */
function mapMarketSignals(d: unknown): MarketSignalsSummary {
  const byType = unwrap(d, 'byType', 'by_type').map((r) => {
    const t = pickS(r, 'type') ?? 'breakout';
    return {
      type: (SIGNAL_TYPES as string[]).includes(t) ? (t as SignalType) : 'breakout',
      label: pickS(r, 'label') ?? t,
      today: pickN(r, 'today') ?? 0,
      avg7d: pickN(r, 'avg7d', 'avg_7d') ?? 0,
    };
  });
  return {
    totalToday: pickN(d as Record<string, unknown>, 'totalToday', 'total_today') ?? byType.reduce((s, x) => s + x.today, 0),
    deltaVsYesterday: pickN(d as Record<string, unknown>, 'deltaVsYesterday', 'delta_vs_yesterday') ?? 0,
    byType,
  };
}

export const signalsApi = {
  market: (): Promise<MarketSignalsSummary> =>
    mockOr(() => fx.getMarketSignals(), () => get('/signals/market').then(mapMarketSignals)),
  stock: (ticker: string): Promise<Signal[]> =>
    mockOr(() => fx.getStockSignals(ticker), () => get(`/signals/stock/${encodeURIComponent(ticker)}`).then(mapStockSignals)),
};
