import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { BrowserRouter } from 'react-router';
import { ToastProvider } from '../../src/components/Toast';
import { AccessProvider } from '../../src/hooks/useAccess';
import { ShellContext } from '../../src/hooks/useShell';
import { useStockDataStatus } from '../../src/hooks/useStockDataStatus';
import StockDataCoverage from '../../src/components/shared/StockDataCoverage';
import Home from '../../src/pages/Home';
import Breakouts from '../../src/pages/Breakouts';
import { accessApi } from '../../src/api/modules/access';
import { marketApi } from '../../src/api/modules/market';
import { marketPulseApi } from '../../src/components/market/api';
import { signalsApi } from '../../src/api/modules/signals';
import { strengthApi } from '../../src/api/modules/strength';
import { breakoutsApi } from '../../src/api/modules/breakouts';
import { earningsApi } from '../../src/api/modules/earnings';
import * as fx from '../../src/mocks/fixtures';
import * as fx2 from '../../src/mocks/fixtures2';
import * as pulse from '../../src/mocks/marketPulse';
import '../../src/index.css';

const noop = () => {};
const mode = new URLSearchParams(location.search).get('mode') ?? 'status';
// Only unrelated domains are stubbed. Status, watchlist and chart requests use
// the production API client and cache; Playwright supplies isolated HTTP responses.
Object.assign(accessApi, { status: async () => ({ role: 'visitor', aiEnabled: false, aiAvailable: false }) });
Object.assign(marketApi, { indices: async () => fx.getIndices(), ctaTrend: async () => fx2.getCtaTrend() });
Object.assign(marketPulseApi, { statusDetail: async () => pulse.getMarketStatusDetail(), regime: async () => pulse.getMarketRegime() });
Object.assign(signalsApi, { market: async () => null });
Object.assign(strengthApi, { market: async () => fx.getMarketStrength() });
const seed = fx2.getBreakoutsCurrent()[0];
const historySeed = fx2.getBreakoutEvents().items[0];
const currents = mode === 'home'
  ? Array.from({ length: 12 }, (_, i) => ({ ...seed, ticker: `R${i}`, event_id: `R${i}` }))
  : [{ ...seed, ticker: 'CUR1', event_id: 'current-1' }, { ...seed, ticker: 'CUR2', event_id: 'current-2' }];
Object.assign(breakoutsApi, {
  current: async () => currents,
  currentEnvelope: async () => ({ events: currents, asOf: '2026-09-04T20:00:00Z' }),
  status: async () => fx2.getBreakoutsStatus(),
  eventDetail: async () => { throw new Error('No additional detail in this fixture'); },
  events: async (filters: { cursor?: string }) => filters.cursor
    ? { items: [{ ...historySeed, ticker: 'HIS2', event_id: 'history-2' }], nextCursor: null, hasMore: false, total: null, page: 2 }
    : { items: [{ ...historySeed, ticker: 'HIS1', event_id: 'history-1' }, { ...historySeed, ticker: 'CUR1', event_id: 'history-duplicate' }], nextCursor: 'next', hasMore: true, total: null, page: 1 },
});
Object.assign(earningsApi, { upcoming: async () => ({ items: Array.from({ length: 8 }, (_, i) => ({
  ticker: `E${i}`, name: `Earnings ${i}`, date: `2099-01-${String(i + 1).padStart(2, '0')}`, timing: 'bmo',
})) }) });

export function StatusHarness() {
  const [tickers, setTickers] = useState(['AAPL', 'MSFT']);
  const state = useStockDataStatus(tickers);
  useEffect(() => { Object.assign(window, { statusHarness: { setTickers, refresh: state.refresh } }); }, [state.refresh]);
  return <><StockDataCoverage state={state} /><output id="daily-version">{state.dailyVersion}</output></>;
}

createRoot(document.getElementById('root')!).render(
  <BrowserRouter><AccessProvider><ToastProvider><ShellContext.Provider value={{ openTicker: noop, openPalette: noop }}>
    <main className="p-4 md:p-8">{mode === 'home' ? <Home /> : mode === 'breakouts' ? <Breakouts /> : <StatusHarness />}</main>
  </ShellContext.Provider></ToastProvider></AccessProvider></BrowserRouter>,
);
