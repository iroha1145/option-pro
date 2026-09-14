/** Local JSON bodies for Round 6 surfaces lab. Browser fulfill only. */

export function homeLabFixtures(now = new Date()) {
  const asOf = now.toISOString();
  return {
    indices: {
      indices: [
        { symbol: '^GSPC', price: 5972.4, change_percent: 0.21 },
        { symbol: '^NDX', price: 21468.2, change_percent: 0.18 },
        { symbol: '^DJI', price: 43828.1, change_percent: -0.12 },
        { symbol: '^N225', price: 38220.1, change_percent: 0.33 },
        { symbol: '000001.SS', price: 3351.8, change_percent: -0.08 },
      ],
    },
    status: { market: 'closed', phase: null, holiday: null, server_time: asOf },
    strength: { as_of: asOf, market_regime: null },
    signals: { signals: {}, scores: {}, as_of: asOf },
    breakoutsCurrent: { events: [] },
    breakoutsStatus: { status: 'idle', as_of: asOf },
    watchlist: { groups: [] },
    cta: { method_version: 'lab', source_status: 'unavailable', instruments: [] },
    quotes: {
      quotes: [],
      status: {
        enabled: false,
        configured: false,
        public_enabled: false,
        connected: false,
        connection_status: 'disabled',
      },
    },
  };
}

export function earningsFixture(now = new Date()) {
  const iso = (offset) => {
    const date = new Date(now);
    date.setDate(date.getDate() + offset);
    return date.toISOString().slice(0, 10);
  };
  const row = (ticker, offset, name) => ({
    ticker,
    name,
    date: iso(offset),
    earnings_date: iso(offset),
    timing: 'amc',
    epsEstimate: 1.25 + offset / 10,
    epsActual: offset < 0 ? 1.3 : null,
    eps_estimate: 1.25 + offset / 10,
    eps_actual: offset < 0 ? 1.3 : null,
    marketCap: 2e12,
    sector: 'Technology',
  });
  return {
    earnings: [
      row('AAPL', 0, 'Apple'),
      row('MSFT', 1, 'Microsoft'),
      row('NVDA', 2, 'NVIDIA'),
      row('AMZN', 3, 'Amazon'),
      row('META', 4, 'Meta'),
      row('GOOGL', 8, 'Alphabet'),
    ],
    items: [
      row('AAPL', 0, 'Apple'),
      row('MSFT', 1, 'Microsoft'),
      row('NVDA', 2, 'NVIDIA'),
      row('AMZN', 3, 'Amazon'),
      row('META', 4, 'Meta'),
      row('GOOGL', 8, 'Alphabet'),
    ],
    attempted: 6,
    succeeded: 6,
    failed_symbols: [],
    data_limited: false,
    source_status: 'ok',
    as_of: now.toISOString(),
  };
}
