import { api, invalidateCache } from '../api.js';
import { renderHeatmap } from '../components/heatmap.js';
import { renderMarketStatus } from '../components/marketStatus.js';
import {
  getCustomTickers, initCustomFromBackend, applyCustomOrder,
  addTicker, removeTicker, moveTicker, saveCustomTickers
} from '../components/customWatchlist.js';

const WATCHLIST_REFRESH_MS = 60 * 1000;
const WATCHLIST_PAGE_SIZE = 48;
let activeMountGeneration = 0;
let visibleCardLimit = WATCHLIST_PAGE_SIZE;

const SECTOR_BY_TICKER = {
  AAPL: 'TECH', MSFT: 'TECH', GOOGL: 'TECH', GOOG: 'TECH', META: 'TECH', AMZN: 'TECH', NFLX: 'TECH', CRM: 'TECH', ORCL: 'TECH', ADBE: 'TECH',
  NVDA: 'SEMIS', AMD: 'SEMIS', INTC: 'SEMIS', AVGO: 'SEMIS', TSM: 'SEMIS', ASML: 'SEMIS', QCOM: 'SEMIS', MU: 'SEMIS', ARM: 'SEMIS',
  TSLA: 'AUTO', F: 'AUTO', GM: 'AUTO', RIVN: 'AUTO', NIO: 'AUTO', LI: 'AUTO', XPEV: 'AUTO',
  JPM: 'BANKS', BAC: 'BANKS', WFC: 'BANKS', GS: 'BANKS', MS: 'BANKS', C: 'BANKS', V: 'FINTECH', MA: 'FINTECH', PYPL: 'FINTECH',
  XOM: 'ENERGY', CVX: 'ENERGY', COP: 'ENERGY', SLB: 'ENERGY',
  JNJ: 'HEALTH', UNH: 'HEALTH', PFE: 'HEALTH', MRK: 'HEALTH', LLY: 'HEALTH', ABBV: 'HEALTH',
  WMT: 'RETAIL', COST: 'RETAIL', HD: 'RETAIL', NKE: 'RETAIL', SBUX: 'RETAIL',
  SPY: 'ETF', QQQ: 'ETF', IWM: 'ETF', DIA: 'ETF'
};

const COMPANY_BY_TICKER = {
  AAPL: 'Apple Inc.', MSFT: 'Microsoft Corp.', GOOGL: 'Alphabet Inc.', GOOG: 'Alphabet Inc.', AMZN: 'Amazon.com Inc.', META: 'Meta Platforms',
  NVDA: 'NVIDIA Corp.', AMD: 'Advanced Micro Devices', TSLA: 'Tesla Inc.', NFLX: 'Netflix Inc.', JPM: 'JPMorgan Chase', SPY: 'SPDR S&P 500 ETF', QQQ: 'Invesco QQQ Trust'
};

// Note: Real prices only. No fallback fake data — showing wrong prices in a
// financial tool is worse than showing nothing.

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;'
  })[character]);
}

function formatPrice(value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `$${number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatChange(value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const sign = number > 0 ? '+' : '';
  return `${sign}${number.toFixed(2)}%`;
}

function parseEarningsDate(value) {
  const raw = String(value || '').slice(0, 10);
  const parts = raw.split('-').map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return null;
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function startOfToday() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function daysUntilDate(date) {
  if (!date) return null;
  return Math.round((date - startOfToday()) / 86400000);
}

function formatEarningsDelta(item) {
  const explicit = Number(item.days_until);
  const days = Number.isFinite(explicit) ? explicit : daysUntilDate(item.dateObj);
  if (!Number.isFinite(days)) return '—';
  if (days < 0) return '已过';
  if (days === 0) return '今天';
  if (days === 1) return '明天';
  return `T+${days}`;
}

function normalizeUpcomingEarnings(payload) {
  const items = Array.isArray(payload) ? payload : (payload?.earnings ?? []);
  const today = startOfToday();
  return items.map((item) => {
    const ticker = String(item.ticker || '').toUpperCase();
    const dateObj = parseEarningsDate(item.earnings_date || item.date);
    return {
      ticker,
      name: item.name || item.company || ticker,
      date: dateObj ? item.earnings_date || item.date : '',
      dateObj,
      days_until: item.days_until,
    };
  }).filter((item) => item.ticker && item.dateObj && item.dateObj >= today)
    .sort((a, b) => a.dateObj - b.dateObj || a.ticker.localeCompare(b.ticker));
}

function normalizeSpark(stock) {
  const spark = stock.spark ?? stock.sparkline ?? stock.spark7d ?? stock.spark_7d ?? stock.sparkData ?? stock.prices7d ?? stock.sevenDaySpark;
  if (Array.isArray(spark)) {
    return spark.map((point) => Number(typeof point === 'object' ? (point.price ?? point.close ?? point.value ?? point.y) : point)).filter(Number.isFinite);
  }
  return [];
}

function getChangePercent(stock, spark) {
  const raw = stock.changePercent ?? stock.change_percentage ?? stock.changePct ?? stock.change_pct ?? stock.percentChange ?? stock.change_percent ?? stock.change;
  const number = raw === null || raw === undefined || raw === '' ? NaN : Number(raw);
  // API change_percent fields are percentage points. Never guess the unit from
  // magnitude: +60% is a valid move and must remain +60%, not +0.60%.
  if (Number.isFinite(number)) return number;
  if (spark.length >= 2 && spark[0] !== 0) return ((spark[spark.length - 1] - spark[0]) / spark[0]) * 100;
  return null;
}

function normalizeStock(stock) {
  const ticker = String(stock.ticker ?? stock.symbol ?? stock.code ?? '').toUpperCase();
  const spark = normalizeSpark(stock);
  const latestSpark = spark.length ? spark[spark.length - 1] : undefined;
  const rawPrice = stock.price ?? stock.last ?? stock.lastPrice ?? stock.close ?? latestSpark;
  const price = rawPrice === null || rawPrice === undefined || rawPrice === '' ? null : Number(rawPrice);
  const changePercent = getChangePercent(stock, spark);
  return {
    ticker,
    companyName: stock.companyName ?? stock.company_name ?? stock.name_cn ?? stock.name ?? stock.company ?? COMPANY_BY_TICKER[ticker] ?? '上市公司',
    sector: String(stock.sector ?? stock.industry ?? stock._groupName ?? SECTOR_BY_TICKER[ticker] ?? 'WATCH').toUpperCase(),
    price,
    changePercent,
    // Real backend data only. Empty array means "no sparkline available" —
    // the card renderer should handle that gracefully (don't fake a fake curve).
    spark: spark.length ? spark : [],
    signalSummary: stock.signalSummary ?? stock.signal_summary ?? stock.signal ?? stock.summary ?? ''
  };
}

function normalizeWatchlistPayload(payload) {
  // API returns {groups: [{id, name, stocks: [{ticker, name, price, change_percent}]}]}
  if (payload?.groups && Array.isArray(payload.groups)) {
    const all = [];
    for (const group of payload.groups) {
      const sectorName = group.name || '';
      for (const s of (group.stocks || [])) {
        s._groupName = sectorName;
        all.push(s);
      }
    }
    return all.map(normalizeStock).filter((stock) => stock.ticker);
  }
  const items = Array.isArray(payload) ? payload : (payload?.watchlist ?? payload?.items ?? payload?.data ?? payload?.stocks ?? []);
  return items.map(normalizeStock).filter((stock) => stock.ticker);
}

function sampleSparkline(points, targetCount = 8) {
  if (points.length <= targetCount) return points;
  return Array.from({ length: targetCount }, (_, index) => {
    if (index === 0) return points[0];
    if (index === targetCount - 1) return points[points.length - 1];
    const start = Math.floor((index / targetCount) * points.length);
    const end = Math.max(start + 1, Math.floor(((index + 1) / targetCount) * points.length));
    const slice = points.slice(start, end);
    return slice.reduce((sum, value) => sum + value, 0) / slice.length;
  });
}

function softenSparkline(points) {
  if (points.length < 3) return points;
  return points.map((value, index) => {
    if (index === 0 || index === points.length - 1) return value;
    return points[index - 1] * 0.24 + value * 0.52 + points[index + 1] * 0.24;
  });
}

function smoothPath(coords) {
  if (coords.length === 1) return `M ${coords[0].x.toFixed(2)} ${coords[0].y.toFixed(2)}`;
  return coords.slice(1).reduce((path, point, index) => {
    const prev = coords[index];
    const dx = point.x - prev.x;
    const c1x = prev.x + dx * 0.46;
    const c2x = point.x - dx * 0.46;
    return `${path} C ${c1x.toFixed(2)} ${prev.y.toFixed(2)}, ${c2x.toFixed(2)} ${point.y.toFixed(2)}, ${point.x.toFixed(2)} ${point.y.toFixed(2)}`;
  }, `M ${coords[0].x.toFixed(2)} ${coords[0].y.toFixed(2)}`);
}

function renderSparkline(points, isPositive) {
  if (!points.length) return '<svg class="sparkline" viewBox="0 0 160 56" role="img" aria-label="No sparkline data"></svg>';
  const width = 160;
  const height = 56;
  const paddingX = 8;
  const paddingY = 8;
  const sampled = softenSparkline(sampleSparkline(points));
  const min = Math.min(...sampled);
  const max = Math.max(...sampled);
  const range = max - min || 1;
  const step = sampled.length > 1 ? (width - paddingX * 2) / (sampled.length - 1) : width - paddingX * 2;
  const coords = sampled.map((value, index) => ({
    x: paddingX + index * step,
    y: height - paddingY - ((value - min) / range) * (height - paddingY * 2),
  }));
  const d = smoothPath(coords);
  const baseline = height - 2;
  const color = '#008c72';
  const trend = isPositive ? 'positive' : 'negative';
  const first = coords[0];
  const last = coords[coords.length - 1];
  return `
    <svg class="sparkline sparkline--${trend}" viewBox="0 0 ${width} ${height}" role="img" aria-label="7-day price sparkline" data-spark="7-day">
      <path class="sparkline-fill" d="${d} L ${last.x.toFixed(2)} ${baseline} L ${first.x.toFixed(2)} ${baseline} Z"></path>
      <path class="sparkline-line" d="${d}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>
    </svg>
  `;
}

function renderStockCard(stock, editMode = false) {
  const hasChange = Number.isFinite(stock.changePercent);
  const isPositive = hasChange && stock.changePercent >= 0;
  const toneClass = !hasChange ? 'neutral' : isPositive ? 'positive' : 'negative';
  const editControls = editMode ? `
      <div class="stock-card__edit-controls">
        <button type="button" class="card-edit-btn" data-edit-action="left" data-ticker="${escapeHtml(stock.ticker)}" aria-label="向前移动" title="向前">
          <span class="material-symbols-outlined">chevron_left</span>
        </button>
        <button type="button" class="card-edit-btn card-edit-btn--remove" data-edit-action="remove" data-ticker="${escapeHtml(stock.ticker)}" aria-label="移除" title="移除">
          <span class="material-symbols-outlined">close</span>
        </button>
        <button type="button" class="card-edit-btn" data-edit-action="right" data-ticker="${escapeHtml(stock.ticker)}" aria-label="向后移动" title="向后">
          <span class="material-symbols-outlined">chevron_right</span>
        </button>
      </div>` : '';
  const cardTag = editMode ? 'div' : 'button';
  const cardAttrs = editMode
    ? `class="stock-card stock-card--editing" data-ticker="${escapeHtml(stock.ticker)}"`
    : `class="stock-card" type="button" data-ticker="${escapeHtml(stock.ticker)}" aria-label="打开 ${escapeHtml(stock.ticker)} 详情"`;
  return `
    <${cardTag} ${cardAttrs}>
      ${editControls}
      <div class="stock-card__topline">
        <span class="sector-tag label-caps">${escapeHtml(stock.sector)}</span>
        <span class="signal-dot ${toneClass}" aria-hidden="true"></span>
      </div>
      <div class="stock-card__identity">
        <strong class="stock-ticker">${escapeHtml(stock.ticker)}</strong>
        <span class="company-name">${escapeHtml(stock.companyName)}</span>
      </div>
      <div class="stock-card__market">
        <span class="stock-price mono font-data-mono" data-numeric>${formatPrice(stock.price)}</span>
        <span class="stock-change mono font-data-mono ${toneClass}" data-numeric>${formatChange(stock.changePercent)}</span>
      </div>
      ${renderSparkline(stock.spark, isPositive)}
      ${stock.signalSummary ? `<p class="signal-summary">${escapeHtml(stock.signalSummary)}</p>` : ''}
    </${cardTag}>
  `;
}

function bindHeatmapNavigation() {
  const heatmap = document.getElementById('terminal-heatmap');
  if (!heatmap || heatmap.dataset.eventsBound === 'true') return;
  heatmap.dataset.eventsBound = 'true';
  heatmap.addEventListener('click', (event) => {
    const tile = event.target.closest('.heatmap-tile[data-ticker]');
    const ticker = tile?.dataset.ticker;
    if (ticker) window.location.hash = `#detail/${encodeURIComponent(ticker)}`;
  });
}

function renderWatchlistShell(isLoading = false, mountGeneration = activeMountGeneration) {
  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = `
    <section class="terminal-page" data-watchlist-mount="${mountGeneration}" aria-labelledby="terminal-title">
      <header class="terminal-header">
        <div>
          <span class="label-caps">自选</span>
          <h1 id="terminal-title">Signal Deck</h1>
          <p>信号分析与波动率监控 · 自定义自选已存储在本机</p>
          <small id="watchlist-updated" class="detail-muted">等待行情更新</small>
        </div>
        <div class="terminal-header-right">
          <div id="market-status-panel" class="market-status-panel"></div>
          <button id="watchlist-edit-btn" type="button" class="watchlist-edit-btn">
            <span class="material-symbols-outlined">edit</span> 编辑
          </button>
        </div>
      </header>
      <div id="watchlist-add-bar" class="watchlist-add-bar" hidden>
        <input type="text" id="watchlist-add-input" placeholder="输入代码（如 NVDA），回车添加" autocomplete="off" />
        <button type="button" id="watchlist-add-btn">添加</button>
        <button type="button" id="watchlist-reset-btn" class="watchlist-reset-btn" title="重置为默认自选">重置默认</button>
      </div>
      <div class="terminal-layout">
        <div>
          <div id="watchlist-grid" class="watchlist-grid ${isLoading ? 'is-loading' : ''}">
            ${isLoading ? '<div class="panel loading-card">正在加载智能卡片…</div>' : ''}
          </div>
        </div>
        <aside class="terminal-sidebar" aria-label="市场智能侧栏">
          <section class="terminal-panel panel">
            <span class="label-caps">即将财报</span>
            <ul id="upcoming-earnings-list">
              <li class="terminal-panel-empty">正在加载…</li>
            </ul>
          </section>
          <!-- 板块 IV 概览 removed: it showed hardcoded fake numbers (42.1/28.7/51.4),
               which violates this file's own "real data only" rule. Real sector IV
               lives on the 板块 page. -->
        </aside>
      </div>
      <section class="terminal-heatmap panel" aria-labelledby="terminal-heatmap-title">
        <div class="section-card-heading">
          <span class="label-caps">波动率热力图</span>
          <h2 id="terminal-heatmap-title">波动率热力图</h2>
        </div>
        <div id="terminal-heatmap" class="terminal-heatmap__body">
          ${isLoading ? '<div class="detail-muted">正在加载波动率热力图…</div>' : ''}
        </div>
      </section>
    </section>
  `;
}

// Cache the latest backend data so we can re-render quickly when entering edit mode
let __watchlistState = { backendStocks: [], heatmapData: [] };
let __editMode = false;

function isWatchlistMounted(generation = activeMountGeneration) {
  return generation === activeMountGeneration
    && window.location.hash.replace(/^#/, '').split('/')[0] === 'watchlist'
    && Boolean(document.querySelector(`[data-watchlist-mount="${generation}"]`));
}

function renderUpcomingEarnings(items) {
  const list = document.getElementById('upcoming-earnings-list');
  if (!list) return;
  if (!items.length) {
    list.innerHTML = '<li class="terminal-panel-empty">暂无未来财报数据</li>';
    return;
  }
  list.innerHTML = items.slice(0, 3).map((item) => `
    <li class="terminal-earnings-item">
      <button type="button" class="terminal-earnings-ticker" data-earnings-ticker="${escapeHtml(item.ticker)}" title="${escapeHtml(item.name)}">
        ${escapeHtml(item.ticker)}
      </button>
      <span class="terminal-earnings-date">
        <span class="mono font-data-mono" data-numeric>${escapeHtml(formatEarningsDelta(item))}</span>
        <small>${escapeHtml(String(item.date).slice(0, 10))}</small>
      </span>
    </li>
  `).join('');
  list.querySelectorAll('[data-earnings-ticker]').forEach((button) => {
    button.addEventListener('click', () => {
      const ticker = button.dataset.earningsTicker;
      if (ticker) window.location.hash = `#detail/${encodeURIComponent(ticker)}`;
    });
  });
}

async function loadUpcomingEarnings(mountGeneration = activeMountGeneration) {
  try {
    const payload = await api.earnings();
    if (!isWatchlistMounted(mountGeneration)) return;
    renderUpcomingEarnings(normalizeUpcomingEarnings(payload));
  } catch (e) {
    if (!isWatchlistMounted(mountGeneration)) return;
    const list = document.getElementById('upcoming-earnings-list');
    if (list) list.innerHTML = '<li class="terminal-panel-empty">财报数据暂不可用</li>';
  }
}

async function fetchAndCacheBackend(mountGeneration = activeMountGeneration, force = false) {
  try {
    if (force) invalidateCache('wl');
    const payload = await api.watchlist();
    if (!isWatchlistMounted(mountGeneration)) return null;
    const groups = payload?.groups || [];
    // Initialize custom from backend on first visit
    initCustomFromBackend(groups);
    const stocks = normalizeWatchlistPayload(payload);
    __watchlistState.backendStocks = stocks;
    __watchlistState.heatmapData = stocks.slice(0, 20).map(s => ({
      ticker: s.ticker, label: s.companyName, changePercent: s.changePercent, weight: 1 + Math.abs(s.changePercent) / 2
    }));
    __watchlistState.fetchError = '';
    const updated = document.getElementById('watchlist-updated');
    if (updated) {
      const stale = payload?._stale || payload?.stale;
      updated.textContent = `${stale ? '陈旧数据' : '更新'} ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
    }
    return stocks;
  } catch (e) {
    if (!isWatchlistMounted(mountGeneration)) return null;
    console.warn('api.watchlist() failed; showing empty state.', e);
    __watchlistState.backendStocks = [];
    __watchlistState.heatmapData = [];
    __watchlistState.fetchError = e.message || 'API unavailable';
    return [];
  }
}

function renderCardsFromCustom() {
  const grid = document.getElementById('watchlist-grid');
  if (!grid) return;
  const customTickers = getCustomTickers() || __watchlistState.backendStocks.map(s => s.ticker);
  const ordered = applyCustomOrder(__watchlistState.backendStocks, customTickers).map(s => {
    if (s._placeholder) {
      return normalizeStock({ ticker: s.ticker, price: null, change_percent: null, name: s.ticker, sector: 'CUSTOM' });
    }
    return s;
  });
  if (!ordered.length) {
    const msg = __watchlistState.fetchError
      ? `<div class="detail-muted" style="padding:32px;text-align:center"><strong style="display:block;margin-bottom:6px;color:var(--color-crimson)">数据暂不可用</strong>API 返回失败 <button type="button" data-watchlist-retry>重试</button></div>`
      : '<div class="detail-muted" style="padding:32px;text-align:center">自选列表为空 · 点击右上角「编辑」添加代码</div>';
    grid.innerHTML = msg;
    return;
  }
  const visible = ordered.slice(0, visibleCardLimit);
  const remaining = ordered.length - visible.length;
  grid.innerHTML = `${visible.map((s) => renderStockCard(s, __editMode)).join('')}
    ${remaining > 0 ? `<button type="button" class="panel detail-muted" data-watchlist-load-more>再显示 ${remaining} 只</button>` : ''}`;
  bindCardEvents();
}

function bindCardEvents() {
  const grid = document.getElementById('watchlist-grid');
  if (!grid || grid.dataset.eventsBound === 'true') return;
  grid.dataset.eventsBound = 'true';
  grid.addEventListener('click', (e) => {
    const retry = e.target.closest('[data-watchlist-retry]');
    if (retry) {
      retry.disabled = true;
      refreshWatchlist(activeMountGeneration, true);
      return;
    }
    if (e.target.closest('[data-watchlist-load-more]')) {
      visibleCardLimit += WATCHLIST_PAGE_SIZE;
      renderCardsFromCustom();
      return;
    }
    const btn = e.target.closest('[data-edit-action]');
    if (btn) {
      e.stopPropagation();
      const action = btn.dataset.editAction;
      const ticker = btn.dataset.ticker;
      if (!ticker) return;
      if (action === 'remove') removeTicker(ticker);
      else if (action === 'left') moveTicker(ticker, 'left');
      else if (action === 'right') moveTicker(ticker, 'right');
      renderCardsFromCustom();
      return;
    }
    const card = e.target.closest('.stock-card[data-ticker]');
    if (!card || __editMode) return;
    const ticker = card.dataset.ticker;
    if (ticker) window.location.hash = `#detail/${encodeURIComponent(ticker)}`;
  });
}

function bindEditToolbar(mountGeneration = activeMountGeneration) {
  const editBtn = document.getElementById('watchlist-edit-btn');
  const addBar = document.getElementById('watchlist-add-bar');
  const addInput = document.getElementById('watchlist-add-input');
  const addBtn = document.getElementById('watchlist-add-btn');
  const resetBtn = document.getElementById('watchlist-reset-btn');

  const setEdit = (on) => {
    __editMode = on;
    if (editBtn) editBtn.innerHTML = on
      ? '<span class="material-symbols-outlined">check</span> 完成'
      : '<span class="material-symbols-outlined">edit</span> 编辑';
    editBtn?.classList.toggle('is-active', on);
    if (addBar) addBar.hidden = !on;
    renderCardsFromCustom();
  };

  editBtn?.addEventListener('click', () => setEdit(!__editMode));

  const handleAdd = () => {
    const v = (addInput?.value || '').trim().toUpperCase();
    if (!v) return;
    addTicker(v);
    addInput.value = '';
    renderCardsFromCustom();
    // Try fetching the new ticker's data and re-render to fill placeholder
    api.stock(v).then(data => {
      if (!isWatchlistMounted(mountGeneration) || !data || data.__error) return;
      const existing = __watchlistState.backendStocks.find(s => s.ticker === v);
      if (!existing && data) {
        __watchlistState.backendStocks.push(normalizeStock({
          ticker: v, name: data.name, price: data.price, change_percent: data.change_percent
        }));
        renderCardsFromCustom();
      }
    }).catch(() => {});
  };
  addBtn?.addEventListener('click', handleAdd);
  addInput?.addEventListener('keydown', (e) => { if (e.key === 'Enter') handleAdd(); });

  resetBtn?.addEventListener('click', () => {
    if (!confirm('确认重置为默认自选？当前自定义将被覆盖。')) return;
    localStorage.removeItem('optix.watchlist.custom.v1');
    initCustomFromBackend(__watchlistState.backendStocks.map(s => ({ stocks: [s] })));
    renderCardsFromCustom();
  });
}

async function refreshWatchlist(mountGeneration, force = false) {
  const result = await fetchAndCacheBackend(mountGeneration, force);
  if (result === null || !isWatchlistMounted(mountGeneration)) return;
  const grid = document.getElementById('watchlist-grid');
  const heatmap = document.getElementById('terminal-heatmap');
  renderCardsFromCustom();
  if (heatmap) heatmap.innerHTML = renderHeatmap(__watchlistState.heatmapData);
  grid?.classList.remove('is-loading');
  bindHeatmapNavigation();
}

export function renderWatchlist() {
  const mountGeneration = ++activeMountGeneration;
  visibleCardLimit = WATCHLIST_PAGE_SIZE;
  __editMode = false;
  renderWatchlistShell(true, mountGeneration);
  renderMarketStatus(document.getElementById('market-status-panel'));
  loadUpcomingEarnings(mountGeneration);
  bindCardEvents();
  bindEditToolbar(mountGeneration);
  refreshWatchlist(mountGeneration);

  const timer = setInterval(() => {
    if (!document.hidden && isWatchlistMounted(mountGeneration)) refreshWatchlist(mountGeneration, true);
  }, WATCHLIST_REFRESH_MS);
  const onVisibility = () => {
    if (!document.hidden && isWatchlistMounted(mountGeneration)) refreshWatchlist(mountGeneration, true);
  };
  document.addEventListener('visibilitychange', onVisibility);
  const cleanup = () => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('hashchange', cleanup);
  };
  window.addEventListener('hashchange', cleanup, { once: true });
}
