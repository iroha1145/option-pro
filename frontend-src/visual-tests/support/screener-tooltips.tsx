import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import ResultTable from '../../src/components/screener/ResultTable';
import ResultCards from '../../src/components/screener/ResultCards';
import { CatalystBadge, SubscoreTicks } from '../../src/components/screener/cells';
import type { ScreenerRow } from '../../src/api/types';
import type { CatalystSummary } from '../../src/components/screener/types';
import '../../src/index.css';
import '../../src/styles/transitions-root.css';
import '../../src/styles/transitions-catalog.css';

const rows: ScreenerRow[] = Array.from({ length: 16 }, (_, i) => ({
  ticker: ['AAA', 'BBB', 'CCC'][i] ?? `T${i}`, name: `测试股票 ${i + 1}`, sector: '半导体',
  price: 123.45 + i, changePct: 1.5, strengthScore: 84.5, band: 'strong',
  subscores: { trend: 90, momentum: 82, volume: 75, volatility: null }, sparkline: [2, 3, 5],
  subscoreDims: [
    { key: 'short', label: '短期', value: 81 }, { key: 'mid', label: '中期', value: 73 },
    { key: 'long', label: '长期', value: null }, { key: 'breakout', label: '突破质量', value: 92 },
  ],
}));
const summary: CatalystSummary = {
  loaded: true, count: 7, pos: 4, neg: 1, neu: 1, pending: 1, hasMore: true,
  latestTitle: '发布新的产品与订单进展，预计在接下来的季度进一步提升交付能力',
  latestAt: '2026-09-05T03:00:00Z',
};
const catalysts = Object.fromEntries(rows.map((row) => [row.ticker, summary]));
const mode = new URLSearchParams(location.search).get('mode');
const noop = () => {};

export function ScreenerTooltipHarness() {
  const [visible, setVisible] = useState(true);
  const [toggles, setToggles] = useState(0);
  useEffect(() => { Object.assign(window, { tooltipHarness: { setVisible } }); }, []);
  const shared = {
    rows: visible ? rows : [], expanded: null, onToggle: () => setToggles((value) => value + 1),
    catalysts, details: {}, weights: null, signals: {}, onOpenDetail: noop, animKey: 'static', page: 2,
  };
  return <main className="p-5" style={{ minHeight: '180vh' }}>
    <h1 className="mb-4 text-h2">选股扫描提示回归</h1>
    <button id="outside" className="mb-4 rounded border px-3 py-2">表格外部</button>
    <output id="row-toggle-count" className="ml-4">{toggles}</output>
    {mode === 'edges' ? visible && <>
      {['top-left', 'top-right', 'bottom-left', 'bottom-right'].map((corner) => (
        <div key={corner} data-corner={corner} className="fixed flex gap-2"
          style={{ top: corner.startsWith('top') ? 8 : undefined, bottom: corner.startsWith('bottom') ? 8 : undefined,
            left: corner.endsWith('left') ? 8 : undefined, right: corner.endsWith('right') ? 8 : undefined }}>
          <SubscoreTicks row={rows[0]} tipSide={corner.startsWith('top') ? 'top' : 'bottom'} />
          <CatalystBadge summary={summary} tipSide={corner.startsWith('top') ? 'top' : 'bottom'} />
        </div>
      ))}
    </> : mode === 'cards' ? <div id="cards"><ResultCards {...shared} /></div>
      : <div id="table-scroll" style={{ height: 320, overflow: 'auto' }}><ResultTable {...shared} startIndex={0} totalPages={1} onPageChange={noop} flashes={{}} /></div>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<ScreenerTooltipHarness />);
