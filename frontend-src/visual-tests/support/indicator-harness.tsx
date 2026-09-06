/** Test-only entry: no backend access, persistent writes, or live market data. */
import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { AccessProvider } from '../../src/hooks/useAccess';
import { ToastProvider } from '../../src/components/Toast';
import KlineChart from '../../src/components/detail/KlineChart';
import { barFingerprint } from '../../src/components/detail/chart-drawings/analysis/mapBundle';
import { quoteStore } from '../../src/lib/liveQuotes';
import { useQuoteSymbols } from '../../src/hooks/useLiveQuote';
import { echarts, type EChartsInstance } from '../../src/lib/chart';
import type { TechnicalStructure } from '../../src/api/types';
import '../../src/index.css';
import '../../src/styles/transitions-root.css';
import '../../src/styles/transitions-catalog.css';

const memory = new Map<string, string>();
Object.defineProperty(window, 'localStorage', { configurable: true, value: {
  get length() { return memory.size; }, key: (index: number) => [...memory.keys()][index] ?? null,
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => { memory.set(key, String(value)); },
  removeItem: (key: string) => { memory.delete(key); }, clear: () => memory.clear(),
} });
const query = new URLSearchParams(location.search);
const tiny = query.get('scenario') === 'tiny';
const bars = Array.from({ length: 160 }, (_, index) => {
  const c = 190 + index * 0.22 + 12 * Math.sin(index / 9);
  return { t: new Date(Date.UTC(2026, 0, 1 + index, 21)).toISOString(),
    o: c - 1, c, h: c + 3, l: c - 3, v: 2_000_000 + index * 5_000, closed: true };
});
const dates = bars.map(bar => bar.t.slice(0, 10));
const through = dates.at(-1)!;
const values = (fn: (index: number) => number) => bars.map((_, index) => index < 30 ? null : fn(index));
const scale = tiny ? 0.000002 : 2;
const analysis = {
  ticker: 'AAPL', range: '1d', adjustment: 'raw', dataThrough: through,
  barFingerprint: barFingerprint(bars), barCount: bars.length, lastClose: bars.at(-1)!.c, dates,
  overlays: [{ id: 'review-resistance', sourceId: 'fixture', algorithmVersion: 'fixture-v1', group: 'price', kind: 'resistance_trend',
    status: 'forming', direction: 'up', shapeQuality: 0.95, displayPriority: 95, evidence: { touches: 5 },
    formationStart: dates[40], formationEnd: through, dataThrough: through, label: 'fixture',
    geometry: { subtype: 'rising', anchors: [40, 159].map((i, n) => ({ time: bars[i].t, barKey: dates[i], price: n ? 236 : 217 })) } }],
  indicatorPanes: query.get('scenario') === 'empty' ? [] : [
    { id: 'macd', label: 'MACD', kind: 'macd', values: { macd: values(i => scale * Math.sin(i / 9)), signal: values(i => scale * 0.8 * Math.sin((i - 2) / 9)), histogram: values(i => scale * 0.4 * Math.cos(i / 9)) } },
    { id: 'rsi', label: 'RSI', kind: 'rsi', values: { rsi: values(i => 50 + 22 * Math.sin(i / 9)) } },
    { id: 'obv', label: 'OBV', kind: 'obv', values: { obv: values(i => -3_000_000_000 + i * 100_000_000) } },
    { id: 'clv', label: 'CLV', kind: 'clv', values: { clv: values(i => Math.sin(i / 9)) } },
    { id: 'spy_rs', label: 'SPY Relative Strength', kind: 'rs', values: { rs: values(i => 100 + 5 * Math.sin(i / 9)) } },
    { id: 'range_persistence', label: '60日区间位置', kind: 'range', values: { position: values(i => 0.5 + 0.3 * Math.sin(i / 9)) } },
  ],
};
const technical = { data_through: through, last_bar: { closed: true, trade_date: through }, chart_analysis: analysis,
  chart_overlays: { resistance_high: 236, base_status: 'active' } } as unknown as TechnicalStructure;
localStorage.setItem('option-pro:chart-layers:v1:anonymous', JSON.stringify({
  version: 2, preset: 'custom', enabled: ['ma20', 'auto_patterns', 'support_resistance', 'macd', 'rsi', 'obv', 'clv', 'spy_rs', 'range_persistence'],
  minShapeQuality: 0.45, onlyActive: false, showInvalidated: true, maxPatterns: 3, maxLabels: 6, labelDensity: 1,
}));
const status = { enabled: true, configured: true, allowed: true, public_enabled: true, connected: true, connection_status: 'connected', market_session: 'regular' };
const streams: LocalStream[] = [];
class LocalStream {
  url: string;
  closed = false;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: { data: string }) => void>();
  constructor(url: string | URL) { this.url = String(url); streams.push(this); }
  addEventListener(name: string, listener: (event: { data: string }) => void) { this.listeners.set(name, listener); }
  close() { this.closed = true; }
  emit(name: string, value: unknown) { this.listeners.get(name)?.({ data: JSON.stringify(value) }); }
}
Object.defineProperty(window, 'EventSource', { value: LocalStream, configurable: true });
const requests: string[] = [];
window.fetch = async (input, init) => {
  const url = new URL(input instanceof Request ? input.url : String(input), location.origin);
  requests.push(`${init?.method ?? 'GET'} ${url.pathname}`);
  const body = url.pathname === '/api/access/status'
    ? { access_mode: 'password', logged_in: false, account: null }
    : url.pathname === '/api/quotes' ? { quotes: [], status }
    : url.pathname.endsWith('/chart') ? { ticker: 'AAPL', bars, as_of: '2026-09-04T21:00:00Z' }
    : {};
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
};
let fullUpdates = 0, quoteUpdates = 0;
function getChart(): EChartsInstance | undefined {
  const node = document.querySelector<HTMLElement>('[data-indicator-chart] [_echarts_instance_]');
  return node ? echarts.getInstanceByDom(node) : undefined;
}
const intercepted = new WeakSet<EChartsInstance>();
const monitor = setInterval(() => {
  const chart = getChart();
  if (!chart || intercepted.has(chart)) return;
  intercepted.add(chart);
  const original = chart.setOption.bind(chart);
  chart.setOption = ((...args: Parameters<typeof chart.setOption>) => {
    const opts = args[1];
    if (opts === true || (opts && typeof opts === 'object' && opts.notMerge)) fullUpdates++;
    else quoteUpdates++;
    return original(...args);
  }) as typeof chart.setOption;
}, 20);
window.addEventListener('pagehide', () => clearInterval(monitor));
Object.assign(window, { indicatorTest: { getChart, bars, analysis, requests,
  counts: () => ({ fullUpdates, quoteUpdates }),
  resetCounts: () => { fullUpdates = 0; quoteUpdates = 0; },
  tick: (price: number, seq = 0) => {
    const at = new Date(Date.UTC(2026, 8, 4, 15, 0, seq)).toISOString();
    streams.filter(row => !row.closed).forEach(stream => stream.emit('quotes', { status, quotes: [{
      symbol: 'AAPL', price, previous_close: 220, change: price - 220, change_pct: (price / 220 - 1) * 100,
      trade_at: at, received_at: at, source: 'finnhub_websocket', session: 'regular', freshness: 'live', subscription_status: 'live',
    }] }));
  },
} });
export function Harness() {
  const [live, setLive] = useState(false);
  useQuoteSymbols(['AAPL']);
  useEffect(() => quoteStore.start(false), []);
  useEffect(() => {
    if (!live) return;
    let count = 0;
    const timer = setInterval(() => {
      const at = new Date(Date.UTC(2026, 8, 4, 15, 0, count++)).toISOString();
      streams.filter(row => !row.closed).forEach(stream => stream.emit('quotes', { status, quotes: [{
        symbol: 'AAPL', price: 224 + count * 0.01, previous_close: 220, change: 4 + count * 0.01,
        change_pct: 2, trade_at: at, received_at: at, source: 'finnhub_websocket', session: 'regular', freshness: 'live', subscription_status: 'live',
      }] }));
    }, 250);
    return () => clearInterval(timer);
  }, [live]);
  return <main className="mx-auto max-w-[1320px] p-2 md:p-5">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 text-caption text-ink-500">
      <span>副图隔离测试 · 全部为模拟数据 · 不连接后端</span>
      <button className="btn-secondary" aria-pressed={live} onClick={() => setLive(v => !v)}>模拟实时价格</button>
    </div>
    <KlineChart ticker="AAPL" technical={technical} height={420} className="rounded-lg border border-line bg-card p-2 md:p-5" />
  </main>;
}
createRoot(document.getElementById('root')!).render(<AccessProvider><ToastProvider><Harness /></ToastProvider></AccessProvider>);
