import { createRoot } from 'react-dom/client';
import type { OptionChain } from '../../src/api/types';
import ChainBrowser from '../../src/components/detail/options/ChainBrowser';
import SummaryTiles from '../../src/components/detail/options/SummaryTiles';
import '../../src/index.css';
import '../../src/styles/transitions-root.css';
import '../../src/styles/transitions-catalog.css';

// Deliberately synthetic contract data, isolated from every market endpoint.
// Six existing legs: four calls and two puts; the 105 call has unknown volume.
const chain: OptionChain = {
  ticker: 'TEST',
  expiration: '2030-08-16',
  spot: 103,
  provider: 'isolated-test-fixture',
  rows: [
    {
      strike: 100,
      callVol: 6_000, callOi: 10_000, callIv: 0.28, callBid: 0.2, callAsk: 0.4,
      putVol: null, putOi: null, putIv: null, putBid: null, putAsk: null,
    },
    {
      strike: 102.5,
      callVol: 300, callOi: 100, callIv: 0.32, callBid: 1.2, callAsk: 1.4,
      putVol: 200, putOi: 0, putIv: 0.35, putBid: 0.9, putAsk: 1.1,
    },
    {
      strike: 105,
      callVol: null, callOi: 80, callIv: null, callBid: null, callAsk: 2,
      putVol: 50, putOi: 200, putIv: 0.36, putBid: 1.5, putAsk: 1.7,
    },
    {
      strike: 110,
      callVol: 100, callOi: 200, callIv: 0.41, callBid: 0.1, callAsk: 0.2,
      putVol: null, putOi: null, putIv: null, putBid: null, putAsk: null,
    },
  ],
};

export function OptionsHarness() {
  return <main className="mx-auto w-full min-w-0 max-w-[1100px] px-4 py-6 sm:px-6">
    <h1 className="text-title-s text-ink-900">期权面板隔离测试</h1>
    <p className="mt-2 text-caption text-ink-500">专用合约样例，非真实行情</p>
    <section aria-label="期权成交摘要">
      <SummaryTiles chain={chain} />
    </section>
    <ChainBrowser chain={chain} />
  </main>;
}

createRoot(document.getElementById('root')!).render(<OptionsHarness />);
