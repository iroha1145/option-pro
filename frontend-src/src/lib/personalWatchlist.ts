import type { WatchlistItem } from '@/api/types';

export const DEFAULT_WATCHLIST_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'SPY'];
const TICKER_PATTERN = /^(?:\^[A-Z0-9][A-Z0-9._-]{0,10}|[A-Z0-9][A-Z0-9._-]{0,11})$/;

export function parseWatchlistInput(input: string): { tickers: string[]; invalid: string[] } {
  const tokens = [...new Set(input.normalize('NFKC').toUpperCase().split(/[\s,;、，；]+/).filter(Boolean))];
  return {
    tickers: tokens.filter((value) => TICKER_PATTERN.test(value)),
    invalid: tokens.filter((value) => !TICKER_PATTERN.test(value)),
  };
}

export function watchlistDelta(original: string[], draft: string[]) {
  return {
    add: draft.filter((symbol) => !original.includes(symbol)),
    remove: original.filter((symbol) => !draft.includes(symbol)),
  };
}

/** Membership survives missing quotes; unknown numbers must never look like zero. */
export function personalWatchlistRows(tickers: string[], quotes: WatchlistItem[]): WatchlistItem[] {
  const bySymbol = new Map(quotes.map((row) => [row.ticker, row]));
  return tickers.map((ticker) => bySymbol.get(ticker) ?? {
    ticker, name: ticker, sector: '', price: NaN, change: NaN, changePct: NaN,
    sparkline: [], strengthScore: NaN, signals: [], updatedAt: '',
  });
}
