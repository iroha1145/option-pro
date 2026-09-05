import { useMemo } from 'react';
import { stocksApi } from '@/api/modules/stocks';
import { dailyDataVersion, normalizeStatusTickers, summarizeStockData } from '@/lib/stockDataStatus';
import { usePolling } from './usePolling';

/** Only GETs: the background worker owns preparation and retry scheduling. */
export function useStockDataStatus(input: readonly string[]) {
  const scope = normalizeStatusTickers(input).join(',');
  const tickers = useMemo(() => scope ? scope.split(',') : [], [scope]);
  const query = usePolling(() => stocksApi.dataStatus(tickers), tickers.length ? 30_000 : null, [scope]);
  const items = useMemo(() => {
    const requested = new Set(tickers);
    return (query.data ?? []).filter((item) => requested.has(item.ticker));
  }, [query.data, tickers]);
  const byTicker = useMemo(() => new Map(items.map((item) => [item.ticker, item])), [items]);
  const summary = useMemo(() => summarizeStockData(tickers, items), [tickers, items]);
  const dailyVersion = useMemo(() => items
    .filter((item) => dailyDataVersion(item))
    .map((item) => `${item.ticker}:${dailyDataVersion(item)}`).sort().join('|'), [items]);
  return { ...query, items, byTicker, summary, dailyVersion };
}
