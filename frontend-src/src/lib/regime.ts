import type { MarketRegime } from '@/components/market/api';

export function regimeMean(r: MarketRegime): number {
  return (r.index_trend_score + r.market_momentum_score + r.market_breadth_score
    + r.market_volume_score + r.risk_appetite_score + r.risk_on_spread_score) / 6;
}
