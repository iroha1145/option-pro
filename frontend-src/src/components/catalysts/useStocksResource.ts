import { catalystsContract, type TickerImpactSummary } from './api';
import type { CatalystFilters } from './filters';
import { toFeedQuery } from './filters';
import type { ResourcePolicy } from './resourceCache';
import { useCatalystResource } from './useCatalystResource';
const POLICY: ResourcePolicy<TickerImpactSummary[]> = {
  freshMs: 300_000, retainMs: 2 * 24 * 60 * 60_000,
  validate: (value): value is TickerImpactSummary[] => Array.isArray(value) && value.every((row) =>
    row && typeof row.ticker === 'string' && Number.isFinite(row.netImpact) && Number.isFinite(row.count)),
};
export function useStocksResource(filters: CatalystFilters) {
  const query = toFeedQuery(filters);
  return useCatalystResource(`stocks:${JSON.stringify(query)}`, POLICY, () => catalystsContract.tickerSummaries(query));
}
