import { api, safe } from '../api.js';
import { refreshRemainingMs } from '../utils/frontendState.js';

const INDICES = [
  { symbol: '^GSPC',     label: 'S&P 500' },
  { symbol: '^IXIC',     label: 'NASDAQ' },
  { symbol: '^DJI',      label: 'DOW' },
  { symbol: '^N225',     label: '日经 225' },
  { symbol: '000001.SS', label: '上证综指' }
];
const INDEX_DETAIL_SYMBOLS = Object.freeze({
  'S&P 500': '^GSPC',
  NASDAQ: '^IXIC',
  DOW: '^DJI',
  '日经 225': '^N225',
  '上证综指': '000001.SS',
});
const REFRESH_MS = 60 * 1000;
let mountGeneration = 0;
let refreshTimer = null;
let visibilityHandler = null;
let lastData = null;
let lastDataAt = null;
let lastRefreshStartedAt = 0;

export function createIndicesVisibilityHandler({
  isHidden,
  now,
  lastStartedAt,
  refresh,
  intervalMs = REFRESH_MS,
}) {
  return () => {
    if (isHidden()) return false;
    if (refreshRemainingMs(lastStartedAt(), now(), intervalMs) > 0) return false;
    void refresh(true);
    return true;
  };
}

const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const fmtPrice = (n) => n !== null && n !== undefined && n !== '' && Number.isFinite(Number(n)) ? Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
const fmtPct = (n) => {
  if (n === null || n === undefined || n === '') return '—';
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
};

function detailSymbol(idx) {
  const payloadSymbol = typeof idx?.symbol === 'string' ? idx.symbol.trim() : '';
  return payloadSymbol || INDEX_DETAIL_SYMBOLS[idx?.label] || '^GSPC';
}

function renderItem(idx) {
  const pct = idx.changePercent == null ? null : Number(idx.changePercent);
  const toneClass = !Number.isFinite(pct) ? '' : pct > 0 ? 'up' : pct < 0 ? 'down' : '';
  const symbol = detailSymbol(idx);
  return `<a class="ticker-item" href="#detail/${encodeURIComponent(symbol)}" data-index-symbol="${esc(symbol)}" title="打开 ${esc(idx.label)} 详情" aria-label="打开 ${esc(idx.label)} 详情">
    <strong>${esc(idx.label)}</strong>
    <span class="mono" data-numeric>${fmtPrice(idx.price)}</span>
    <em class="${toneClass} mono" data-numeric>${fmtPct(pct)}</em>
  </a>`;
}

function normalizeStockQuote(idx, quote) {
  if (!quote || quote.__error) return idx;
  const price = Number(quote.price ?? quote.regularMarketPrice ?? quote.last);
  if (!Number.isFinite(price)) return idx;
  return {
    ...idx,
    price,
    changePercent: Number(quote.change_percent ?? quote.changePercent ?? quote.regularMarketChangePercent)
  };
}

export async function mountIndexTicker() {
  const track = document.getElementById('index-ticker-track');
  const updated = document.getElementById('index-ticker-updated');
  if (!track || !updated) return;
  const generation = ++mountGeneration;

  if (lastData) {
    track.innerHTML = lastData.map(renderItem).join('');
    const timeLabel = lastDataAt?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) || '最近';
    updated.textContent = `最近更新 ${timeLabel}`;
    updated.dataset.compactLabel = timeLabel;
    if (lastDataAt) updated.dateTime = lastDataAt.toISOString();
  } else {
    // Initial skeleton with labels only
    track.innerHTML = INDICES.map(i => {
      const symbol = detailSymbol(i);
      return `<a class="ticker-item" href="#detail/${encodeURIComponent(symbol)}" data-index-symbol="${esc(symbol)}" title="${esc(i.label)} 行情加载中" aria-label="打开 ${esc(i.label)} 详情"><strong>${esc(i.label)}</strong><span class="mono">—</span><em class="mono">—</em></a>`;
    }).join('');
    updated.textContent = '行情加载中';
    updated.dataset.compactLabel = '载入中';
    updated.removeAttribute('datetime');
  }

  let refreshInFlight = false;
  const refresh = async (force = false) => {
    if (refreshInFlight) return;
    const now = Date.now();
    if (refreshRemainingMs(lastRefreshStartedAt, now, REFRESH_MS) > 0) return;
    lastRefreshStartedAt = now;
    refreshInFlight = true;
    const batch = await safe(api.marketIndices({ refresh: force }));
    refreshInFlight = false;
    if (generation !== mountGeneration || !track.isConnected) return;

  // ONE batch request (backend fast_info) instead of 5 per-ticker overview
  // calls that each ran yfinance's slow full `.info` scrape.
  let data = INDICES;
  if (!batch?.__error && Array.isArray(batch?.indices)) {
    const bySymbol = new Map(batch.indices.map(q => [q.symbol, q]));
    data = INDICES.map(idx => {
      const q = bySymbol.get(idx.symbol);
      if (!q || q.price == null) return idx;
      return {
        ...idx,
        symbol: typeof q.symbol === 'string' && q.symbol.trim() ? q.symbol.trim() : idx.symbol,
        price: Number(q.price),
        changePercent: q.change_percent == null ? null : Number(q.change_percent),
      };
    });
    const missing = data.filter(idx => !Number.isFinite(Number(idx.price)));
    if (missing.length) {
      const fallbackResults = await Promise.all(missing.map(i => safe(api.stock(i.symbol))));
      if (generation !== mountGeneration || !track.isConnected) return;
      const fallbackBySymbol = new Map(missing.map((idx, i) => [idx.symbol, fallbackResults[i]]));
      data = data.map(idx => normalizeStockQuote(idx, fallbackBySymbol.get(idx.symbol)));
    }
  } else {
    // Fallback to per-ticker overview endpoint (older backend)
    const results = await Promise.all(INDICES.map(i => safe(api.stock(i.symbol))));
    if (generation !== mountGeneration || !track.isConnected) return;
    data = INDICES.map((idx, i) => normalizeStockQuote(idx, results[i]));
  }

  const hasAnyPrice = data.some((item) => Number.isFinite(Number(item.price)));
  if (hasAnyPrice) {
    lastData = data;
    lastDataAt = new Date();
  }
  const displayData = hasAnyPrice ? data : (lastData || data);
  const refreshedAt = hasAnyPrice ? lastDataAt : new Date();
  const timeLabel = refreshedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  track.innerHTML = displayData.map(renderItem).join('');
  updated.textContent = `${hasAnyPrice ? '更新' : '刷新失败'} ${timeLabel}`;
  updated.dataset.compactLabel = hasAnyPrice ? timeLabel : '失败';
  updated.dateTime = refreshedAt.toISOString();
  };

  await refresh();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    if (!document.hidden) refresh(true);
  }, REFRESH_MS);
  if (visibilityHandler) document.removeEventListener('visibilitychange', visibilityHandler);
  visibilityHandler = createIndicesVisibilityHandler({
    isHidden: () => document.hidden,
    now: () => Date.now(),
    lastStartedAt: () => lastRefreshStartedAt,
    refresh,
  });
  document.addEventListener('visibilitychange', visibilityHandler);
}
