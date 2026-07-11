import { api, invalidateCache } from '../api.js';
import { renderMarketStatus } from '../components/marketStatus.js';
import {
  getCustomTickers, initCustomFromBackend, applyCustomOrder,
  addTicker, removeTicker, moveTicker, resetCustom
} from '../components/customWatchlist.js';
import { renderIcon } from '../icons.js';

const WATCHLIST_REFRESH_MS = 5 * 60 * 1000;
const WATCHLIST_VISIBILITY_REFRESH_MIN_MS = 60 * 1000;
const WATCHLIST_PAGE_SIZE = 24;
const WATCHLIST_SNAPSHOT_KEY = 'optix.watchlist.snapshot.v2';
const WATCHLIST_SNAPSHOT_VERSION = 2;
const WATCHLIST_SNAPSHOT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const WATCHLIST_SNAPSHOT_MAX_BYTES = 900_000;
const WATCHLIST_TICKER_PATTERN = /^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$/;
let activeMountGeneration = 0;
let renderedMountGeneration = 0;
let visibleCardLimit = WATCHLIST_PAGE_SIZE;
let lastWatchlistNetworkAt = 0;

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

function removeStoredSnapshot() {
  try { localStorage.removeItem(WATCHLIST_SNAPSHOT_KEY); } catch (_) { /* storage unavailable */ }
}

function snapshotTime(value) {
  const time = Date.parse(String(value || ''));
  return Number.isFinite(time) ? time : null;
}

function watchlistScope(tickers) {
  if (!Array.isArray(tickers)) return 'all';
  return [...new Set(tickers)].sort().join(',');
}

function readStoredSnapshot() {
  try {
    const raw = localStorage.getItem(WATCHLIST_SNAPSHOT_KEY);
    if (!raw) return null;
    if (raw.length > WATCHLIST_SNAPSHOT_MAX_BYTES) {
      removeStoredSnapshot();
      return null;
    }
    const snapshot = JSON.parse(raw);
    const savedAt = Number(snapshot?.savedAt);
    const asOfMs = snapshotTime(snapshot?.asOf);
    const age = Date.now() - savedAt;
    const dataAge = asOfMs === null ? Infinity : Date.now() - asOfMs;
    const stocks = normalizeWatchlistPayload(snapshot?.payload);
    const hasValidTickers = stocks.length > 0
      && stocks.length <= 500
      && stocks.every((stock) => WATCHLIST_TICKER_PATTERN.test(stock.ticker));
    if (
      snapshot?.version !== WATCHLIST_SNAPSHOT_VERSION
      || !Number.isFinite(savedAt)
      || age < -5 * 60 * 1000
      || age > WATCHLIST_SNAPSHOT_MAX_AGE_MS
      || asOfMs === null
      || dataAge < -15 * 60 * 1000
      || dataAge > WATCHLIST_SNAPSHOT_MAX_AGE_MS
      || !hasValidTickers
    ) {
      removeStoredSnapshot();
      return null;
    }
    const attempted = Number(snapshot?.payload?.attempted);
    const succeeded = Number(snapshot?.payload?.succeeded);
    const inferredComplete = Number.isFinite(attempted) && attempted > 0 && Number.isFinite(succeeded)
      ? succeeded >= attempted
      : true;
    return {
      ...snapshot,
      stocks,
      asOfMs,
      complete: typeof snapshot?.complete === 'boolean' ? snapshot.complete : inferredComplete,
      scope: typeof snapshot?.scope === 'string' && snapshot.scope
        ? snapshot.scope
        : watchlistScope(stocks.map((stock) => stock.ticker)),
    };
  } catch (_) {
    removeStoredSnapshot();
    return null;
  }
}

function snapshotPayload(payload, stocks, asOf, { containsPreviousPrices = false } = {}) {
  const seen = new Set();
  const serializedStocks = [];
  for (const stock of stocks) {
    if (!stock?.ticker || seen.has(stock.ticker)) continue;
    seen.add(stock.ticker);
    serializedStocks.push({
      ticker: stock.ticker,
      name: stock.companyName,
      sector: stock.sector,
      price: stock.price,
      change_percent: stock.changePercent,
      spark: stock.spark,
      signal_summary: stock.signalSummary,
    });
  }
  return {
    groups: [{ id: 'snapshot', name: '自选快照', stocks: serializedStocks }],
    as_of: asOf,
    attempted: payload?.attempted,
    succeeded: payload?.succeeded,
    source_status: payload?.source_status,
    _stale: Boolean(payload?._stale),
    stale: Boolean(payload?.stale),
    contains_previous_prices: containsPreviousPrices,
  };
}

function storeSuccessfulSnapshot(
  payload,
  stocks,
  asOf,
  { complete = true, scope = 'all', containsPreviousPrices = false } = {},
) {
  if (!stocks.length || snapshotTime(asOf) === null) return;
  try {
    const previous = readStoredSnapshot();
    const nextAsOfMs = snapshotTime(asOf);
    if (previous && previous.asOfMs > nextAsOfMs) return;
    const snapshot = {
      version: WATCHLIST_SNAPSHOT_VERSION,
      savedAt: Date.now(),
      asOf,
      complete,
      scope,
      payload: snapshotPayload(payload, stocks, asOf, { containsPreviousPrices }),
    };
    const serialized = JSON.stringify(snapshot);
    if (serialized.length > WATCHLIST_SNAPSHOT_MAX_BYTES) return;
    localStorage.setItem(WATCHLIST_SNAPSHOT_KEY, serialized);
  } catch (_) {
    // A full or disabled localStorage must never block live market data.
  }
}

function formatSnapshotTime(value) {
  const parsed = value ? new Date(value) : null;
  if (!parsed || Number.isNaN(parsed.getTime())) return '时间未知';
  return parsed.toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function setFreshnessStatus(mode, asOf, detail = '') {
  const updated = document.getElementById('watchlist-updated');
  if (!updated) return;
  const labels = {
    loading: '正在获取行情',
    snapshot: `上次快照 · ${formatSnapshotTime(asOf)} · 正在更新`,
    'snapshot-ready': `上次快照 · ${formatSnapshotTime(asOf)}`,
    fresh: `已更新 · ${formatSnapshotTime(asOf)}`,
    stale: `服务端旧快照 · ${formatSnapshotTime(asOf)}`,
    error: `上次快照 · ${formatSnapshotTime(asOf)} · 更新失败`,
    unavailable: '行情暂不可用',
    empty: '自选列表为空',
  };
  updated.dataset.state = mode;
  updated.textContent = detail ? `${labels[mode] || labels.loading} · ${detail}` : (labels[mode] || labels.loading);
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
  if (!points.length) return '<svg class="sparkline sparkline--empty" viewBox="0 0 160 56" role="img" aria-label="暂无走势数据"></svg>';
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
  const trend = isPositive ? 'positive' : 'negative';
  const first = coords[0];
  const last = coords[coords.length - 1];
  return `
    <svg class="sparkline sparkline--${trend}" viewBox="0 0 ${width} ${height}" role="img" aria-label="七日价格走势" data-spark="7-day">
      <line class="sparkline-baseline" x1="${paddingX}" y1="${first.y.toFixed(2)}" x2="${width - paddingX}" y2="${first.y.toFixed(2)}"></line>
      <path class="sparkline-fill" d="${d} L ${last.x.toFixed(2)} ${baseline} L ${first.x.toFixed(2)} ${baseline} Z"></path>
      <path class="sparkline-line" d="${d}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"></path>
      <circle class="sparkline-endpoint" cx="${last.x.toFixed(2)}" cy="${last.y.toFixed(2)}" r="3"></circle>
      <text class="sparkline-period" x="${paddingX}" y="9">7日</text>
    </svg>
  `;
}

function renderStockCard(stock, editMode = false, index = 0) {
  const hasChange = Number.isFinite(stock.changePercent);
  const isPositive = hasChange && stock.changePercent >= 0;
  const toneClass = !hasChange ? 'neutral' : isPositive ? 'positive' : 'negative';
  const editControls = editMode ? `
      <div class="stock-card__edit-controls">
        <button type="button" class="card-edit-btn" data-edit-action="left" data-ticker="${escapeHtml(stock.ticker)}" aria-label="向前移动" title="向前">
          ${renderIcon('chevron_left', { size: 16 })}
        </button>
        <button type="button" class="card-edit-btn card-edit-btn--remove" data-edit-action="remove" data-ticker="${escapeHtml(stock.ticker)}" aria-label="移除" title="移除">
          ${renderIcon('close', { size: 16 })}
        </button>
        <button type="button" class="card-edit-btn" data-edit-action="right" data-ticker="${escapeHtml(stock.ticker)}" aria-label="向后移动" title="向后">
          ${renderIcon('chevron_right', { size: 16 })}
        </button>
      </div>` : '';
  const cardBody = `
    <span class="stock-card__body">
      <span class="stock-card__identity">
        <span><strong class="stock-ticker">${escapeHtml(stock.ticker)}</strong><small class="sector-tag">${escapeHtml(stock.sector)}</small></span>
        <small class="company-name">${escapeHtml(stock.companyName)}</small>
      </span>
      <span class="stock-card__quote">
        <strong class="stock-price" data-numeric data-motion-key>${formatPrice(stock.price)}</strong>
        <small class="stock-change ${toneClass}" data-numeric data-motion-key>${formatChange(stock.changePercent)}</small>
      </span>
      <span class="stock-card__spark">${renderSparkline(stock.spark, isPositive)}</span>
      <span class="quote-row__signal">${stock.signalSummary ? escapeHtml(stock.signalSummary) : (isPositive ? '动能向上' : hasChange ? '承压整理' : '等待行情')}</span>
    </span>`;
  return `
    <article class="stock-card quote-row ${editMode ? 'stock-card--editing' : ''}" data-ticker="${escapeHtml(stock.ticker)}" data-motion-card data-motion-key="${escapeHtml(stock.ticker)}" role="listitem" style="--card-index:${Math.min(index, 8)}">
      ${editMode
        ? cardBody
        : `<a class="stock-card__link" href="#detail/${encodeURIComponent(stock.ticker)}" data-card-link="${escapeHtml(stock.ticker)}" data-motion-spotlight aria-label="打开 ${escapeHtml(stock.ticker)} 详情">${cardBody}</a>`}
      ${editControls}
    </article>
  `;
}

function getWatchlistSummary(stocks) {
  const withChange = stocks.filter((stock) => Number.isFinite(stock.changePercent));
  const advancing = withChange.filter((stock) => stock.changePercent > 0).length;
  const declining = withChange.filter((stock) => stock.changePercent < 0).length;
  const leader = withChange.reduce((best, stock) => !best || stock.changePercent > best.changePercent ? stock : best, null);
  const laggard = withChange.reduce((worst, stock) => !worst || stock.changePercent < worst.changePercent ? stock : worst, null);
  const average = withChange.length
    ? withChange.reduce((sum, stock) => sum + stock.changePercent, 0) / withChange.length
    : null;
  return { total: stocks.length, tracked: withChange.length, advancing, declining, leader, laggard, average };
}

function renderSpotlightStock(stock) {
  const panel = document.getElementById('watchlist-spotlight');
  if (!panel) return;
  if (!stock) {
    panel.innerHTML = '<div class="focus-panel__empty">选择一只股票开始查看</div>';
    return;
  }
  const change = Number.isFinite(stock.changePercent) ? stock.changePercent : null;
  const tone = change == null ? 'neutral' : change >= 0 ? 'positive' : 'negative';
  panel.dataset.ticker = stock.ticker;
  panel.dataset.motionKey = stock.ticker;
  panel.innerHTML = `
    <div class="focus-panel__eyebrow" data-motion-key>
      <span>当前焦点</span>
      <span>${escapeHtml(stock.sector)}</span>
    </div>
    <div class="focus-panel__identity" data-motion-key>
      <div><strong>${escapeHtml(stock.ticker)}</strong><small>${escapeHtml(stock.companyName)}</small></div>
      <span class="focus-panel__change ${tone}" data-numeric>${formatChange(change)}</span>
    </div>
    <div class="focus-panel__price" data-numeric data-motion-key>${formatPrice(stock.price)}</div>
    <div class="focus-panel__chart" data-motion-key>${renderSparkline(stock.spark, change == null || change >= 0)}</div>
    <div class="focus-panel__footer">
      <span>${stock.signalSummary ? escapeHtml(stock.signalSummary) : '七日价格节奏'}</span>
      <button type="button" data-spotlight-open="${escapeHtml(stock.ticker)}">查看详情 ${renderIcon('arrow_up_right', { size: 17 })}</button>
    </div>
  `;
}

function updateWatchlistOverview(stocks) {
  const summary = getWatchlistSummary(stocks);
  const copy = document.getElementById('watchlist-summary');
  if (copy) {
    copy.textContent = summary.tracked
      ? `${summary.advancing} 只上涨，${summary.declining} 只下跌 · 平均 ${formatChange(summary.average)}`
      : '等待第一批行情';
  }
  const pulse = document.getElementById('watchlist-pulse');
  if (pulse) {
    const breadth = summary.tracked ? Math.round((summary.advancing / summary.tracked) * 100) : 0;
    pulse.innerHTML = `
      <div class="pulse-panel__heading"><span>今日摘要</span><small>${summary.total} 个标的</small></div>
      <div class="pulse-metric pulse-metric--wide">
        <span>上涨宽度</span><strong data-numeric>${breadth}%</strong>
        <div class="pulse-meter"><i style="--pulse-width:${breadth}%"></i></div>
      </div>
      <button class="pulse-metric" type="button" data-pulse-ticker="${escapeHtml(summary.leader?.ticker || '')}">
        <span>领涨</span><strong>${escapeHtml(summary.leader?.ticker || '—')}</strong><em class="positive" data-numeric>${formatChange(summary.leader?.changePercent)}</em>
      </button>
      <button class="pulse-metric" type="button" data-pulse-ticker="${escapeHtml(summary.laggard?.ticker || '')}">
        <span>承压</span><strong>${escapeHtml(summary.laggard?.ticker || '—')}</strong><em class="negative" data-numeric>${formatChange(summary.laggard?.changePercent)}</em>
      </button>
      <div class="pulse-panel__note"><span class="pulse-live-dot"></span> 行情在后台持续刷新</div>
    `;
  }
  const selected = stocks.find((stock) => stock.ticker === __spotlightTicker)
    || stocks.reduce((best, stock) => {
      if (!best) return stock;
      return Math.abs(Number(stock.changePercent) || 0) > Math.abs(Number(best.changePercent) || 0) ? stock : best;
    }, null);
  if (selected) __spotlightTicker = selected.ticker;
  renderSpotlightStock(selected);
}

function renderWatchlistShell(isLoading = false, mountGeneration = activeMountGeneration) {
  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = `
    <section class="watchlist-page" data-watchlist-mount="${mountGeneration}" data-motion-page aria-labelledby="watchlist-title">
      <header class="watchlist-header page-intro" data-motion-reveal style="--motion-delay:20ms">
        <div class="watchlist-heading">
          <span class="page-kicker">自选研究</span>
          <h1 id="watchlist-title">自选观察</h1>
          <p id="watchlist-summary">正在整理你的自选行情</p>
          <div class="watchlist-freshness-row">
            <small id="watchlist-updated" class="watchlist-freshness" data-state="loading" aria-live="polite">正在获取行情</small>
            <span class="watchlist-delay-note">延迟行情，仅供研究</span>
          </div>
        </div>
        <div class="watchlist-header-actions">
          <button id="watchlist-edit-btn" type="button" class="watchlist-edit-btn">
            ${renderIcon('edit', { size: 16 })} 编辑
          </button>
        </div>
      </header>
      <div class="watchlist-context-row" data-motion-reveal style="--motion-delay:80ms">
        <div id="market-status-panel" class="market-status-panel market-status-panel--compact"></div>
        <section class="watchlist-earnings-strip" aria-labelledby="watchlist-earnings-title">
          <div class="watchlist-strip-title">
            <span>下一事件</span>
            <h2 id="watchlist-earnings-title">财报</h2>
          </div>
          <ul id="upcoming-earnings-list">
            <li class="terminal-panel-empty">正在加载…</li>
          </ul>
        </section>
      </div>
      <div id="watchlist-add-bar" class="watchlist-add-bar" hidden>
        <label class="sr-only" for="watchlist-add-input">添加股票代码</label>
        <input type="text" id="watchlist-add-input" placeholder="输入代码（如 NVDA），回车添加" autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="32" aria-describedby="watchlist-add-feedback" />
        <button type="button" id="watchlist-add-btn">添加</button>
        <button type="button" id="watchlist-reset-btn" class="watchlist-reset-btn" title="重置为默认自选">重置默认</button>
        <span id="watchlist-add-feedback" class="watchlist-add-feedback" role="status" aria-live="polite"></span>
      </div>

      <section class="watchlist-stage" data-motion-reveal style="--motion-delay:140ms" aria-label="自选摘要">
        <article id="watchlist-spotlight" class="focus-panel" data-spotlight-surface data-motion-spotlight data-motion-lens>
          ${isLoading ? '<div class="focus-panel__skeleton"><span></span><span></span><span></span></div>' : ''}
        </article>
        <aside id="watchlist-pulse" class="pulse-panel" data-motion-reveal>
          ${isLoading ? '<div class="pulse-panel__skeleton"><span></span><span></span><span></span></div>' : ''}
        </aside>
      </section>

      <section class="watchlist-list-section" data-motion-reveal style="--motion-delay:200ms" aria-labelledby="watchlist-list-title">
        <div class="watchlist-section-heading">
          <div class="watchlist-list-title">
            <h2 id="watchlist-list-title">观察清单</h2>
            <span id="watchlist-card-count" class="watchlist-card-count">—</span>
          </div>
          <div class="watchlist-controls">
            <label class="watchlist-local-search">
              <span class="sr-only">筛选当前自选</span>
              <input id="watchlist-filter-input" type="search" placeholder="筛选自选" autocomplete="off" />
            </label>
            <div class="watchlist-filter-group" role="group" aria-label="涨跌筛选">
              <button type="button" data-watchlist-filter="all" aria-pressed="true">全部</button>
              <button type="button" data-watchlist-filter="up" aria-pressed="false">上涨</button>
              <button type="button" data-watchlist-filter="down" aria-pressed="false">下跌</button>
            </div>
            <details class="watchlist-more-controls">
              <summary>${renderIcon('sliders', { size: 17 })}<span>整理</span></summary>
              <div class="watchlist-more-menu">
                <label class="watchlist-sort-label"><span>排序</span>
                  <select id="watchlist-sort">
                    <option value="custom">自定义顺序</option>
                    <option value="change-desc">涨幅优先</option>
                    <option value="change-asc">跌幅优先</option>
                    <option value="ticker">代码顺序</option>
                  </select>
                </label>
              </div>
            </details>
          </div>
        </div>
        <div id="watchlist-grid" class="watchlist-grid ${isLoading ? 'is-loading' : ''}" data-view="cards" data-motion-reveal role="list">
          ${isLoading ? Array.from({ length: 8 }, () => '<div class="watchlist-skeleton" aria-hidden="true"><span></span><span></span><span></span><span></span></div>').join('') : ''}
        </div>
      </section>
    </section>
  `;
}

// Cache the latest backend data so we can re-render quickly when entering edit mode
let __watchlistState = {
  backendStocks: [],
  payloadVersion: '',
  asOf: '',
  source: '',
  fetchError: '',
  fetchErrorRendered: false,
};
let __editMode = false;
let __watchlistFilter = 'all';
let __watchlistSort = 'custom';
let __watchlistQuery = '';
let __spotlightTicker = '';
let watchlistRequestSequence = 0;
let activeWatchlistController = null;

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
  list.innerHTML = items.slice(0, 3).map((item) => {
    const date = item.dateObj;
    const fullDate = String(item.date).slice(0, 10);
    const shortDate = date
      ? `${date.getMonth() + 1}月${date.getDate()}日`
      : fullDate;
    return `
    <li class="terminal-earnings-item">
      <a class="terminal-earnings-card" href="#detail/${encodeURIComponent(item.ticker)}" data-motion-card data-motion-key="${escapeHtml(item.ticker)}-earnings" title="${escapeHtml(item.name)} · ${escapeHtml(fullDate)}" aria-label="打开 ${escapeHtml(item.ticker)} 详情，财报 ${escapeHtml(fullDate)}">
        <strong class="terminal-earnings-ticker">${escapeHtml(item.ticker)}</strong>
        <span class="terminal-earnings-date">
          <span class="mono font-data-mono" data-numeric>${escapeHtml(formatEarningsDelta(item))}</span>
          <time datetime="${escapeHtml(fullDate)}">${escapeHtml(shortDate)}</time>
        </span>
      </a>
    </li>
  `;
  }).join('');
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
  const requestId = ++watchlistRequestSequence;
  activeWatchlistController?.abort();
  const controller = new AbortController();
  activeWatchlistController = controller;
  if (__watchlistState.backendStocks.length) {
    setFreshnessStatus('snapshot', __watchlistState.asOf);
  } else {
    setFreshnessStatus('loading', '');
  }
  try {
    const requestedTickers = getCustomTickers();
    if (requestedTickers !== null && requestedTickers.length === 0) {
      setFreshnessStatus('empty', '');
      return { stocks: [], changed: false };
    }
    if (force) invalidateCache('wl:', { prefix: true });
    const payload = await api.watchlist({
      signal: controller.signal,
      tickers: requestedTickers === null ? undefined : requestedTickers,
    });
    if (!isWatchlistMounted(mountGeneration) || requestId !== watchlistRequestSequence) return null;
    lastWatchlistNetworkAt = Date.now();
    const groups = payload?.groups || [];
    let stocks = normalizeWatchlistPayload(payload);
    const asOf = payload?.as_of || payload?.fetched_at || '';
    const incomingAsOf = snapshotTime(asOf);
    const currentAsOf = snapshotTime(__watchlistState.asOf);
    if (stocks.length && incomingAsOf !== null && currentAsOf !== null && incomingAsOf < currentAsOf) {
      setFreshnessStatus(__watchlistState.source === 'snapshot' ? 'snapshot-ready' : 'fresh', __watchlistState.asOf);
      return { stocks: __watchlistState.backendStocks, changed: false };
    }
    const succeeded = Number(payload?.succeeded);
    const attempted = Number(payload?.attempted);
    const isPartial = Number.isFinite(succeeded) && Number.isFinite(attempted) && succeeded < attempted;
    let usedPreviousPrices = false;
    if (isPartial && requestedTickers?.length && __watchlistState.backendStocks.length) {
      const incomingByTicker = new Map(stocks.map((stock) => [stock.ticker, stock]));
      const previousByTicker = new Map(__watchlistState.backendStocks.map((stock) => [stock.ticker, stock]));
      stocks = requestedTickers
        .map((ticker) => {
          if (incomingByTicker.has(ticker)) return incomingByTicker.get(ticker);
          const previous = previousByTicker.get(ticker);
          if (previous) usedPreviousPrices = true;
          return previous;
        })
        .filter(Boolean);
    }
    const fallbackVersion = stocks.map((stock) => `${stock.ticker}:${stock.price ?? ''}:${stock.changePercent ?? ''}`).join('|');
    const payloadVersion = String(asOf || fallbackVersion);
    const changed = payloadVersion !== __watchlistState.payloadVersion;
    const recoveredFromError = Boolean(__watchlistState.fetchError);
    // Initialize custom from backend on first visit or when the backend universe changes.
    if (changed) initCustomFromBackend(groups);
    if (changed) {
      __watchlistState.backendStocks = stocks;
      __watchlistState.payloadVersion = payloadVersion;
    }
    __watchlistState.asOf = asOf;
    __watchlistState.source = 'network';
    __watchlistState.fetchError = '';
    __watchlistState.fetchErrorRendered = false;
    const stale = Boolean(payload?._stale || payload?.stale);
    const partial = isPartial
      ? `部分行情 ${succeeded}/${attempted}${usedPreviousPrices ? ' · 含上次价格' : ''}`
      : '';
    setFreshnessStatus(stale ? 'stale' : 'fresh', asOf, partial);
    // A partial provider response may temporarily omit symbols. Keep an
    // existing complete snapshot; on a first visit, even a partial snapshot is
    // still more useful than an empty screen on the next opening.
    const storedSnapshot = readStoredSnapshot();
    const requestScope = watchlistScope(requestedTickers);
    const sameStoredScope = storedSnapshot?.scope === requestScope;
    if (stocks.length && (!isPartial || !storedSnapshot?.complete || !sameStoredScope)) {
      storeSuccessfulSnapshot(payload, stocks, asOf, {
        complete: !isPartial,
        scope: requestScope,
        containsPreviousPrices: usedPreviousPrices,
      });
    }
    return { stocks, changed: changed || recoveredFromError };
  } catch (e) {
    if (e?.name === 'AbortError' || controller.signal.aborted) return null;
    if (!isWatchlistMounted(mountGeneration) || requestId !== watchlistRequestSequence) return null;
    console.warn('api.watchlist() failed; preserving the last available state.', e);
    const hadData = __watchlistState.backendStocks.length > 0;
    __watchlistState.fetchError = e.message || 'API unavailable';
    if (hadData) {
      setFreshnessStatus('error', __watchlistState.asOf);
      return { stocks: __watchlistState.backendStocks, changed: false };
    }
    __watchlistState.backendStocks = [];
    setFreshnessStatus('unavailable', '');
    const changed = !__watchlistState.fetchErrorRendered;
    __watchlistState.fetchErrorRendered = true;
    return { stocks: [], changed };
  } finally {
    if (activeWatchlistController === controller) activeWatchlistController = null;
  }
}

function renderCardsFromCustom() {
  const grid = document.getElementById('watchlist-grid');
  const count = document.getElementById('watchlist-card-count');
  if (!grid) return;
  const customTickers = getCustomTickers() || __watchlistState.backendStocks.map(s => s.ticker);
  const ordered = applyCustomOrder(__watchlistState.backendStocks, customTickers).map(s => {
    if (s._placeholder) {
      return normalizeStock({ ticker: s.ticker, price: null, change_percent: null, name: s.ticker, sector: 'CUSTOM' });
    }
    return s;
  });
  updateWatchlistOverview(ordered);
  grid.dataset.view = 'cards';
  if (!ordered.length) {
    if (count) count.textContent = '0 个标的';
    const msg = __watchlistState.fetchError
      ? '<div class="watchlist-empty"><strong>行情暂时不可用</strong><span>已停止替换现有数据</span><button type="button" data-watchlist-retry>重新连接</button></div>'
      : '<div class="watchlist-empty"><strong>建立你的第一条观察线</strong><span>点击右上角「编辑」添加股票代码</span></div>';
    grid.innerHTML = msg;
    return;
  }
  let filtered = ordered.filter((stock) => {
    if (__watchlistFilter === 'up' && !(stock.changePercent > 0)) return false;
    if (__watchlistFilter === 'down' && !(stock.changePercent < 0)) return false;
    if (!__watchlistQuery) return true;
    const haystack = `${stock.ticker} ${stock.companyName} ${stock.sector}`.toLowerCase();
    return haystack.includes(__watchlistQuery);
  });
  if (__watchlistSort === 'change-desc') {
    filtered = [...filtered].sort((a, b) => {
      const left = Number.isFinite(a.changePercent) ? a.changePercent : -Infinity;
      const right = Number.isFinite(b.changePercent) ? b.changePercent : -Infinity;
      return right - left;
    });
  } else if (__watchlistSort === 'change-asc') {
    filtered = [...filtered].sort((a, b) => {
      const left = Number.isFinite(a.changePercent) ? a.changePercent : Infinity;
      const right = Number.isFinite(b.changePercent) ? b.changePercent : Infinity;
      return left - right;
    });
  } else if (__watchlistSort === 'ticker') {
    filtered = [...filtered].sort((a, b) => a.ticker.localeCompare(b.ticker));
  }
  const visible = filtered.slice(0, visibleCardLimit);
  const remaining = filtered.length - visible.length;
  if (count) count.textContent = filtered.length === ordered.length
    ? `${ordered.length} 个标的`
    : `${filtered.length} / ${ordered.length}`;
  if (!visible.length) {
    grid.innerHTML = '<div class="watchlist-empty"><strong>没有符合条件的标的</strong><span>换一个筛选条件或搜索词</span></div>';
    return;
  }
  grid.innerHTML = `${visible.map((s, index) => renderStockCard(s, __editMode, index)).join('')}
    ${remaining > 0 ? `<button type="button" class="watchlist-load-more" data-watchlist-load-more>继续显示 ${remaining} 只</button>` : ''}`;
  grid.querySelector(`[data-ticker="${CSS.escape(__spotlightTicker)}"]`)?.classList.add('is-focused');
  bindCardEvents();
}

function focusWatchlistTicker(ticker, { navigateOnMobile = false } = {}) {
  const normalized = String(ticker || '').toUpperCase();
  if (!normalized) return;
  if (navigateOnMobile && window.matchMedia('(max-width: 767px)').matches) {
    window.location.hash = `#detail/${encodeURIComponent(normalized)}`;
    return;
  }
  const stock = __watchlistState.backendStocks.find((item) => item.ticker === normalized)
    || normalizeStock({ ticker: normalized, name: normalized, sector: 'CUSTOM' });
  __spotlightTicker = normalized;
  renderSpotlightStock(stock);
  document.querySelectorAll('.stock-card.is-focused').forEach((card) => card.classList.remove('is-focused'));
  document.querySelector(`.stock-card[data-ticker="${CSS.escape(normalized)}"]`)?.classList.add('is-focused');
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
  });
  grid.addEventListener('pointerover', (event) => {
    if (__editMode || window.matchMedia('(max-width: 767px)').matches) return;
    const row = event.target.closest('.stock-card[data-ticker]');
    if (row?.dataset.ticker && row.dataset.ticker !== __spotlightTicker) {
      focusWatchlistTicker(row.dataset.ticker);
    }
  });
  grid.addEventListener('focusin', (event) => {
    if (__editMode) return;
    const link = event.target.closest('[data-card-link]');
    if (link?.dataset.cardLink && link.dataset.cardLink !== __spotlightTicker) {
      focusWatchlistTicker(link.dataset.cardLink);
    }
  });
}

function bindWatchlistControls() {
  const page = document.querySelector('.watchlist-page');
  if (!page || page.dataset.controlsBound === 'true') return;
  page.dataset.controlsBound = 'true';
  const grid = document.getElementById('watchlist-grid');
  if (grid) grid.dataset.view = 'cards';
  const queryInput = document.getElementById('watchlist-filter-input');
  queryInput?.addEventListener('input', () => {
    __watchlistQuery = queryInput.value.trim().toLowerCase();
    visibleCardLimit = WATCHLIST_PAGE_SIZE;
    renderCardsFromCustom();
  });
  document.getElementById('watchlist-sort')?.addEventListener('change', (event) => {
    __watchlistSort = event.target.value || 'custom';
    renderCardsFromCustom();
  });
  page.addEventListener('click', (event) => {
    const filter = event.target.closest('[data-watchlist-filter]');
    if (filter) {
      __watchlistFilter = filter.dataset.watchlistFilter || 'all';
      page.querySelectorAll('[data-watchlist-filter]').forEach((button) => {
        button.setAttribute('aria-pressed', String(button === filter));
      });
      visibleCardLimit = WATCHLIST_PAGE_SIZE;
      renderCardsFromCustom();
      return;
    }
    const pulseTicker = event.target.closest('[data-pulse-ticker]')?.dataset.pulseTicker;
    if (pulseTicker) focusWatchlistTicker(pulseTicker);
    const spotlightTicker = event.target.closest('[data-spotlight-open]')?.dataset.spotlightOpen;
    if (spotlightTicker) window.location.hash = `#detail/${encodeURIComponent(spotlightTicker)}`;
  });
}

function bindEditToolbar(mountGeneration = activeMountGeneration) {
  const editBtn = document.getElementById('watchlist-edit-btn');
  const addBar = document.getElementById('watchlist-add-bar');
  const addInput = document.getElementById('watchlist-add-input');
  const addBtn = document.getElementById('watchlist-add-btn');
  const resetBtn = document.getElementById('watchlist-reset-btn');
  const feedback = document.getElementById('watchlist-add-feedback');

  const setAddFeedback = (message = '', tone = '') => {
    if (feedback) {
      feedback.textContent = message;
      feedback.dataset.tone = tone;
    }
    addInput?.setAttribute('aria-invalid', tone === 'error' ? 'true' : 'false');
  };

  const setEdit = (on) => {
    __editMode = on;
    if (on) {
      __watchlistSort = 'custom';
      const sort = document.getElementById('watchlist-sort');
      if (sort) sort.value = 'custom';
    }
    if (editBtn) editBtn.innerHTML = on
      ? `${renderIcon('check', { size: 16 })} 完成`
      : `${renderIcon('edit', { size: 16 })} 编辑`;
    editBtn?.classList.toggle('is-active', on);
    if (addBar) addBar.hidden = !on;
    if (!on) setAddFeedback();
    renderCardsFromCustom();
  };

  editBtn?.addEventListener('click', () => setEdit(!__editMode));

  const handleAdd = () => {
    const v = (addInput?.value || '').trim().toUpperCase();
    if (!v) return;
    const before = getCustomTickers() || [];
    if (!WATCHLIST_TICKER_PATTERN.test(v)) {
      setAddFeedback('代码格式不正确', 'error');
      return;
    }
    if (before.includes(v)) {
      setAddFeedback(`${v} 已在列表中`);
      return;
    }
    if (before.length >= 100) {
      setAddFeedback('自选列表最多 100 只', 'error');
      return;
    }
    const after = addTicker(v);
    if (!after.includes(v)) {
      setAddFeedback('未能添加这个代码', 'error');
      return;
    }
    addInput.value = '';
    setAddFeedback(`已添加 ${v}`);
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
    resetCustom();
    initCustomFromBackend(__watchlistState.backendStocks.map(s => ({ stocks: [s] })));
    setAddFeedback('已恢复默认自选');
    renderCardsFromCustom();
  });
}

async function refreshWatchlist(mountGeneration, force = false) {
  const result = await fetchAndCacheBackend(mountGeneration, force);
  if (result === null || !isWatchlistMounted(mountGeneration)) return;
  if (!result.changed && renderedMountGeneration === mountGeneration) return;
  const grid = document.getElementById('watchlist-grid');
  renderCardsFromCustom();
  grid?.classList.remove('is-loading');
  renderedMountGeneration = mountGeneration;
}

export function renderWatchlist() {
  const mountGeneration = ++activeMountGeneration;
  visibleCardLimit = WATCHLIST_PAGE_SIZE;
  __editMode = false;
  __watchlistFilter = 'all';
  __watchlistSort = 'custom';
  __watchlistQuery = '';
  __spotlightTicker = '';
  const snapshot = readStoredSnapshot();
  const hasSnapshot = Boolean(snapshot?.stocks?.length);
  if (!hasSnapshot) {
    __watchlistState = {
      backendStocks: [],
      payloadVersion: '',
      asOf: '',
      source: '',
      fetchError: '',
      fetchErrorRendered: false,
    };
  }
  renderWatchlistShell(!hasSnapshot, mountGeneration);
  bindWatchlistControls();
  if (hasSnapshot) {
    const payload = snapshot.payload;
    const groups = payload?.groups || [];
    const fallbackVersion = snapshot.stocks
      .map((stock) => `${stock.ticker}:${stock.price ?? ''}:${stock.changePercent ?? ''}`)
      .join('|');
    __watchlistState.backendStocks = snapshot.stocks;
    __watchlistState.payloadVersion = String(snapshot.asOf || fallbackVersion);
    __watchlistState.asOf = snapshot.asOf;
    __watchlistState.source = 'snapshot';
    __watchlistState.fetchError = '';
    initCustomFromBackend(groups);
    renderCardsFromCustom();
    const attempted = Number(payload?.attempted);
    const succeeded = Number(payload?.succeeded);
    const previousPriceNote = payload?.contains_previous_prices ? ' · 含上次价格' : '';
    const coverage = !snapshot.complete && Number.isFinite(attempted) && Number.isFinite(succeeded)
      ? `部分行情 ${succeeded}/${attempted}${previousPriceNote}`
      : (!snapshot.complete && previousPriceNote ? previousPriceNote.slice(3) : '');
    setFreshnessStatus('snapshot', snapshot.asOf, coverage);
    renderedMountGeneration = mountGeneration;
  }
  renderMarketStatus(document.getElementById('market-status-panel'), { compact: true });
  loadUpcomingEarnings(mountGeneration);
  bindCardEvents();
  bindEditToolbar(mountGeneration);
  refreshWatchlist(mountGeneration);

  const timer = setInterval(() => {
    if (!document.hidden && isWatchlistMounted(mountGeneration)) refreshWatchlist(mountGeneration, true);
  }, WATCHLIST_REFRESH_MS);
  const onVisibility = () => {
    const oldEnough = Date.now() - lastWatchlistNetworkAt >= WATCHLIST_VISIBILITY_REFRESH_MIN_MS;
    if (!document.hidden && oldEnough && !activeWatchlistController && isWatchlistMounted(mountGeneration)) {
      refreshWatchlist(mountGeneration, true);
    }
  };
  document.addEventListener('visibilitychange', onVisibility);
  const cleanup = () => {
    watchlistRequestSequence += 1;
    activeWatchlistController?.abort();
    activeWatchlistController = null;
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('hashchange', cleanup);
  };
  window.addEventListener('hashchange', cleanup, { once: true });
}
