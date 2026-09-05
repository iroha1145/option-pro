import { quoteSymbol } from './quoteSymbol.ts';

/** Shared snapshot readiness. Missing rows and malformed fields remain unknown. */
export type StockPreparationStatus = 'ready' | 'partial' | 'pending' | 'running' | 'failed';
export interface StockResourceStatus {
  available: boolean;
  fresh: boolean;
  asOf: string | null;
}
export interface StockDataStatus {
  ticker: string;
  status: StockPreparationStatus;
  refreshStatus: StockPreparationStatus | null;
  asOf: string | null;
  retryAfterSeconds: number | null;
  resources: { overview: StockResourceStatus; dailyChart: StockResourceStatus; signals: StockResourceStatus };
}

export function normalizeStatusTickers(tickers: readonly string[]): string[] {
  return [...new Set(tickers.map(quoteSymbol).filter(Boolean))].sort();
}

const isStatus = (value: unknown): value is StockPreparationStatus =>
  typeof value === 'string' && ['ready', 'partial', 'pending', 'running', 'failed'].includes(value);
const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const date = (value: unknown): string | null =>
  typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : null;

function resource(value: unknown): StockResourceStatus {
  const row = record(value);
  return { available: row.available === true, fresh: row.available === true && row.fresh === true, asOf: date(row.as_of) };
}

export function mapStockDataStatus(body: unknown, requested: readonly string[]): StockDataStatus[] {
  const items = record(body).items;
  if (!Array.isArray(items)) throw new Error('Invalid stock data status response');
  const allowed = new Set(normalizeStatusTickers(requested));
  const rows = new Map<string, StockDataStatus>();
  for (const value of items) {
    const item = record(value);
    const ticker = typeof item.ticker === 'string' ? quoteSymbol(item.ticker) : '';
    if (!allowed.has(ticker) || !isStatus(item.status) || rows.has(ticker)) continue;
    const resources = record(item.resources);
    rows.set(ticker, {
      ticker, status: item.status, refreshStatus: isStatus(item.refresh_status) ? item.refresh_status : null,
      asOf: date(item.as_of),
      retryAfterSeconds: typeof item.retry_after_seconds === 'number' && Number.isFinite(item.retry_after_seconds)
        ? Math.max(0, item.retry_after_seconds) : null,
      resources: { overview: resource(resources.overview), dailyChart: resource(resources.daily_chart), signals: resource(resources.signals) },
    });
  }
  return [...rows.values()];
}

export function summarizeStockData(tickers: readonly string[], items: readonly StockDataStatus[]) {
  const requested = normalizeStatusTickers(tickers);
  const byTicker = new Map(items.map((item) => [item.ticker, item]));
  const summary = { total: requested.length, overview: 0, dailyChart: 0, signals: 0, preparing: 0, partial: 0, failed: 0, stale: 0, unknown: 0, ready: 0 };
  for (const ticker of requested) {
    const row = byTicker.get(ticker);
    if (!row) { summary.unknown += 1; continue; }
    const resources = Object.values(row.resources);
    for (const key of ['overview', 'dailyChart', 'signals'] as const) {
      if (row.resources[key].available) summary[key] += 1;
    }
    const failed = row.status === 'failed' || row.refreshStatus === 'failed';
    const preparing = !failed && [row.status, row.refreshStatus].some((status) => status === 'pending' || status === 'running');
    if (preparing) summary.preparing += 1;
    if (failed) summary.failed += 1;
    if (resources.some((item) => !item.available)) summary.partial += 1;
    if (resources.some((item) => item.available && !item.fresh)) summary.stale += 1;
    if (row.status === 'ready' && resources.every((item) => item.fresh) && !preparing && !failed) summary.ready += 1;
  }
  return summary;
}

export function dailyDataVersion(item: StockDataStatus | undefined): string {
  return item?.resources.dailyChart.available ? item.resources.dailyChart.asOf ?? 'available' : '';
}
