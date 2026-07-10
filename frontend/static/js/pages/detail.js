import { api, invalidateCache, safe } from '../api.js';
import { renderChart } from '../components/chart.js';
import { getMarketStatusForTicker } from '../components/marketStatus.js';
import { renderTopBottomSignals } from '../components/topBottomSignals.js';
import { renderOptionChain, renderAlerts } from '../components/optionChain.js';
import { renderAlertAnalysisButton } from '../components/aiAnalysis.js';

const TIMEFRAMES = [ ['5m','5分'], ['15m','15分'], ['1h','1时'], ['1d','日K'], ['1w','周K'] ];
const CHART_REFRESH_MS = 5 * 60 * 1000;
const QUOTE_REFRESH_MS = 60 * 1000;
const CHART_MODE_KEY = 'option-pro:detail-chart-mode';

const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const money = (n) => n == null || Number.isNaN(Number(n)) ? '—' : `$${Number(n).toFixed(2)}`;
const large = (n) => n == null ? '—' : n >= 1e12 ? `$${(n/1e12).toFixed(2)}T` : n >= 1e9 ? `$${(n/1e9).toFixed(2)}B` : n >= 1e6 ? `$${(n/1e6).toFixed(1)}M` : `$${Number(n).toLocaleString()}`;
const num = (n) => n == null ? '—' : Number(n).toLocaleString();
const safeUrl = (v) => {
  try {
    const url = new URL(String(v || ''), window.location.origin);
    return url.origin === window.location.origin || url.protocol === 'https:' ? url.href : '';
  } catch (_) {
    return '';
  }
};
const logoCandidates = (stock) => {
  const ticker = String(stock?.ticker || '').trim().toUpperCase();
  const proxiedLogo = ticker ? `/api/stocks/${encodeURIComponent(ticker)}/logo` : '';
  const apiUrls = Array.isArray(stock?.logo_urls) ? stock.logo_urls : [];
  const tickerUrls = [
    ticker ? `https://financialmodelingprep.com/image-stock/${encodeURIComponent(ticker)}.png` : '',
    ticker ? `https://static2.finnhub.io/file/publicdatany/finnhubimage/stock_logo/${encodeURIComponent(ticker)}.png` : '',
    ticker ? `https://eodhd.com/img/logos/US/${encodeURIComponent(ticker)}.png` : '',
  ];
  const urls = [
    proxiedLogo,
    ...(apiUrls.length ? apiUrls : tickerUrls),
    stock?.logo_url,
    ...(apiUrls.length ? tickerUrls : []),
  ].map(safeUrl).filter(Boolean);
  return [...new Set(urls)];
};

const readChartMode = () => {
  try {
    return localStorage.getItem(CHART_MODE_KEY) === 'line' ? 'line' : 'candles';
  } catch (_) {
    return 'candles';
  }
};

const writeChartMode = (mode) => {
  try {
    localStorage.setItem(CHART_MODE_KEY, mode);
  } catch (_) {
    // Local storage may be unavailable in private or restricted contexts.
  }
};

const chartModeMeta = (mode) => mode === 'line'
  ? { icon: 'candlestick_chart', title: '切换为K线' }
  : { icon: 'show_chart', title: '切换为线图' };

const marketStatusIcon = (status) => {
  if (status?.tone === 'live') return 'wb_sunny';
  if (status?.tone === 'pre') return 'schedule';
  return 'dark_mode';
};

// Track the "active" mount so older mountDetail calls can detect they've been
// superseded and bail out of late cleanups. Each mount captures its own
// local handle + timer in closure — no shared mutable state.
let __activeMountId = 0;

function currentDetailTicker() {
  const [route, encoded = ''] = window.location.hash.replace(/^#/, '').split('/');
  if (route !== 'detail') return '';
  try {
    return decodeURIComponent(encoded).trim().toUpperCase();
  } catch (_) {
    return '';
  }
}

function isDetailActive(state) {
  const root = document.querySelector(`[data-detail-mount="${state.mountId}"]`);
  return !state.cancelled
    && state.mountId === __activeMountId
    && currentDetailTicker() === state.ticker
    && Boolean(root?.isConnected);
}

function queryInMount(state, selector) {
  if (!isDetailActive(state)) return null;
  return document.querySelector(`[data-detail-mount="${state.mountId}"]`)?.querySelector(selector) || null;
}

function renderShell(ticker, backRoute, mountId) {
  return `<section class="detail-page" data-detail-mount="${mountId}" data-detail-ticker="${esc(ticker)}">
    <nav class="detail-breadcrumb">
      <a href="${esc(backRoute)}" data-back-breadcrumb>← 返回列表</a>
      <span class="mono">/ ${esc(ticker)}</span>
    </nav>

    <div id="modal-header" class="panel" style="padding:24px"></div>
    <div id="modal-stats" style="display:grid;gap:16px"></div>

    <div class="detail-chart-wrap panel" style="padding:20px">
      <div class="detail-chart-control-row">
        <div id="tf-buttons" class="ethos-timeframe-row"></div>
        <button id="chart-mode-toggle" class="chart-mode-toggle" type="button" aria-pressed="false" title="切换为线图">
          <span class="material-symbols-outlined" aria-hidden="true">show_chart</span>
          <span class="sr-only">切换图表类型</span>
        </button>
      </div>
      <div id="modal-chart" style="height:420px;min-height:420px"></div>
    </div>

    <div id="top-bottom-signals"></div>

    <div id="option-alerts-section"></div>

    <div id="option-chain-container"></div>
  </section>`;
}

function renderHeaderAndStats(stock, backendMarketStatus = null, state = null) {
  const header = state ? queryInMount(state, '#modal-header') : document.getElementById('modal-header');
  const stats = state ? queryInMount(state, '#modal-stats') : document.getElementById('modal-stats');
  if (!header || !stats) return;
  const hasPrice = stock.price != null && Number.isFinite(Number(stock.price));
  const hasChange = stock.change_percent != null && Number.isFinite(Number(stock.change_percent));
  const pct = hasChange ? Number(stock.change_percent) : 0;
  const hasAbsoluteChange = stock.change != null && Number.isFinite(Number(stock.change));
  const ch = hasAbsoluteChange ? Number(stock.change) : null;
  const pos = hasChange && pct > 0, neg = hasChange && pct < 0;
  const toneClass = pos ? 'up' : neg ? 'down' : '';
  const initial = (stock.ticker || '?')[0];
  const logos = logoCandidates(stock);
  const logoUrl = logos[0] || '';
  const priceText = hasPrice ? Number(stock.price).toLocaleString(undefined,{maximumFractionDigits:2}) : '—';
  const absoluteChangeText = hasAbsoluteChange
    ? ` (${ch > 0 ? '+' : ch < 0 ? '-' : ''}$${Math.abs(ch).toFixed(2)})`
    : '';
  const changeText = hasChange ? `${pos?'+':''}${pct.toFixed(2)}%${absoluteChangeText}` : '行情暂不可用';
  const titleText = stock.name || stock.ticker;
  const showTicker = String(titleText || '').trim().toUpperCase() !== String(stock.ticker || '').trim().toUpperCase();
  const marketStatus = getMarketStatusForTicker(stock.ticker, backendMarketStatus, stock.exchange || stock.market || '');
  const sessionIcon = marketStatusIcon(marketStatus);
  const sessionTitle = `${marketStatus.phase} · ${marketStatus.nextEventLabel}: ${marketStatus.nextEventMarketText} · 本地 ${marketStatus.nextEventLocalText}`;

  header.innerHTML = `
    <div class="detail-stock-header">
      <div style="display:flex;align-items:flex-start;gap:16px;flex:1;min-width:0">
        <div class="detail-logo" data-logo-shell>
          ${logoUrl ? `<img src="${esc(logoUrl)}" alt="${esc(stock.name_en || stock.ticker)} logo" loading="eager" decoding="async" referrerpolicy="no-referrer" data-company-logo data-logo-index="0" data-logo-urls="${esc(logos.join('|'))}">` : ''}
          <span data-logo-fallback>${esc(initial)}</span>
        </div>
        <div style="min-width:0">
          <h1 style="margin:0;font-size:24px;font-weight:800;letter-spacing:-.04em">
            ${esc(titleText)}
            ${showTicker ? `<span style="font-family:'JetBrains Mono';font-size:14px;color:var(--color-muted);font-weight:700;margin-left:8px">${esc(stock.ticker)}</span>` : ''}
          </h1>
          <p style="margin:6px 0 0;color:var(--color-muted);font-size:13px;line-height:1.5">${esc(stock.description || `${stock.name || stock.ticker} · 行情、技术信号与期权链分析`)}</p>
        </div>
      </div>
      <div class="detail-market-price">
        <div class="detail-price-line">
          <strong class="mono">${priceText}</strong>
          <span class="material-symbols-outlined detail-price-session detail-price-session--${esc(marketStatus.tone)}" title="${esc(sessionTitle)}" aria-label="${esc(sessionTitle)}">${sessionIcon}</span>
        </div>
        <span class="mono ${toneClass}">${changeText}</span>
        <small class="detail-market-next">${esc(marketStatus.market.label)} · ${esc(marketStatus.nextEventLabel)} ${esc(marketStatus.nextEventMarketText)} · 本地 ${esc(marketStatus.nextEventLocalText)}</small>
        <small class="detail-market-next" data-detail-updated>${esc(stock._updatedLabel || '等待行情更新')}</small>
      </div>
    </div>`;
  header.querySelectorAll('[data-company-logo]').forEach((img) => {
    const shell = img.closest('[data-logo-shell]');
    let logoTimer = null;
    const clearLogoTimer = () => {
      if (logoTimer) {
        clearTimeout(logoTimer);
        logoTimer = null;
      }
    };
    const armLogoTimeout = () => {
      clearLogoTimer();
      logoTimer = setTimeout(() => {
        if (!img.complete || img.naturalWidth <= 2 || img.naturalHeight <= 2) {
          tryNextLogo();
        }
      }, 3200);
    };
    const tryNextLogo = () => {
      clearLogoTimer();
      const urls = (img.dataset.logoUrls || '').split('|').filter(Boolean);
      const nextIndex = Number(img.dataset.logoIndex || 0) + 1;
      if (nextIndex < urls.length) {
        img.dataset.logoIndex = String(nextIndex);
        shell?.classList.remove('logo-failed', 'has-logo');
        img.src = urls[nextIndex];
        armLogoTimeout();
        return;
      }
      shell?.classList.remove('has-logo');
      shell?.classList.add('logo-failed');
    };
    const markLogoLoaded = () => {
      clearLogoTimer();
      if (img.naturalWidth <= 2 || img.naturalHeight <= 2) {
        tryNextLogo();
        return;
      }
      shell?.classList.remove('logo-failed');
      shell?.classList.add('has-logo');
    };
    img.addEventListener('load', markLogoLoaded);
    img.addEventListener('error', tryNextLogo);
    if (img.complete) {
      if (img.naturalWidth > 0 && img.naturalHeight > 0) markLogoLoaded();
      else tryNextLogo();
    } else {
      armLogoTimeout();
    }
  });

  const quickStats = [
    ['Open', money(stock.open ?? stock.o)],
    ['High', money(stock.day_high ?? stock.high)],
    ['Low', money(stock.day_low ?? stock.low)],
    ['Volume', num(stock.volume)]
  ];
  const marketStats = [
    ['市值', large(stock.market_cap)],
    ['P/E', stock.pe_ratio ?? stock.pe ?? '—'],
    ['52周最高', money(stock.year_high ?? stock.week52_high)],
    ['52周最低', money(stock.year_low ?? stock.week52_low)]
  ];

  stats.innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px">
      ${quickStats.map(([k,v]) => `<div class="panel" style="padding:14px"><div class="label-caps" style="margin-bottom:6px">${esc(k)}</div><div class="mono" style="font-size:18px;font-weight:800">${esc(v)}</div></div>`).join('')}
    </div>
    <div class="panel" style="padding:20px">
      <div class="label-caps" style="margin-bottom:14px">Market Statistics</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 24px">
        ${marketStats.map(([k,v]) => `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--color-border);font-size:13px"><span style="color:var(--color-muted)">${esc(k)}</span><span class="mono" style="font-weight:700">${esc(v)}</span></div>`).join('')}
      </div>
    </div>`;
}

async function loadChart(ticker, range, state, force = false) {
  const requestId = ++state.chartRequestId;
  const el = queryInMount(state, '#modal-chart');
  if (!el) return;
  state.chartHandle?.destroy?.();
  state.chartHandle = null;
  el.innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center"><div style="width:24px;height:24px;border:2px solid #000;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite"></div></div>';
  if (force) invalidateCache(`c:${ticker.toUpperCase()}:${range}`);
  const data = await safe(api.chart(ticker, range));
  if (!isDetailActive(state) || requestId !== state.chartRequestId || !el.isConnected) return;
  state.chartHandle?.destroy?.();
  state.chartHandle = null;
  if (data.__error) {
    el.innerHTML = '<div style="height:100%;display:flex;gap:12px;align-items:center;justify-content:center;color:var(--color-crimson);font-size:14px">图表加载失败 <button type="button" data-chart-retry>重试</button></div>';
    el.querySelector('[data-chart-retry]')?.addEventListener('click', () => loadChart(ticker, range, state));
    return;
  }
  const handle = renderChart(el, data, data.visible || 0, { mode: state.chartMode });
  if (!isDetailActive(state) || requestId !== state.chartRequestId) {
    handle?.destroy?.();
    return;
  }
  state.chartHandle = handle;
}

async function loadOptionAlertsAndChain(ticker, state) {
  const requestId = ++state.optionRequestId;
  const alertsSection = queryInMount(state, '#option-alerts-section');
  const chainSection = queryInMount(state, '#option-chain-container');
  if (!alertsSection || !chainSection) return;
  alertsSection.innerHTML = '';
  chainSection.innerHTML = '<div class="panel detail-muted" style="padding:24px">正在加载期权链…</div>';
  const ex = await safe(api.expirations(ticker));
  if (!isDetailActive(state) || requestId !== state.optionRequestId) return;
  if (ex.__error) {
    chainSection.innerHTML = '<div class="panel detail-muted" style="padding:24px;color:var(--color-crimson)">到期日加载失败 <button type="button" data-options-retry>重试</button></div>';
    chainSection.querySelector('[data-options-retry]')?.addEventListener('click', () => loadOptionAlertsAndChain(ticker, state));
    return;
  }
  const expirations = ex.expirations || [];
  if (!expirations.length) {
    chainSection.innerHTML = '<div class="panel detail-muted" style="padding:24px">暂无可用期权到期日</div>';
    return;
  }
  const selected = expirations[0];
  const chain = await safe(api.optionChain(ticker, selected));
  if (!isDetailActive(state) || requestId !== state.optionRequestId) return;
  if (chain.__error) {
    chainSection.innerHTML = '<div class="panel detail-muted" style="padding:24px;color:var(--color-crimson)">期权链加载失败 <button type="button" data-options-retry>重试</button></div>';
    chainSection.querySelector('[data-options-retry]')?.addEventListener('click', () => loadOptionAlertsAndChain(ticker, state));
    return;
  }
  // Render alerts + AI button at top
  const alerts = chain?.alerts || [];
  if (alerts.length > 0) {
    alertsSection.innerHTML = `<section class="option-alert-section">${renderAlerts(alerts)}<div id="ai-analysis-mount"></div></section>`;
    renderAlertAnalysisButton(alertsSection.querySelector('#ai-analysis-mount'), ticker, alerts, chain.underlying_price || 0, selected);
  } else {
    alertsSection.innerHTML = '';
  }
  // Render option chain table
  chainSection.innerHTML = `<div class="panel" style="padding:20px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:16px">
      <div>
        <span class="label-caps">Options Chain</span>
        <h2 style="margin:6px 0 0;font-size:20px;font-weight:800;letter-spacing:-.03em">期权链 · ATM ±10</h2>
      </div>
      <select id="expiration-select" class="option-expiration-select">
        ${expirations.map(e => `<option value="${esc(e)}" ${e === selected ? 'selected' : ''}>${esc(e)}</option>`).join('')}
      </select>
    </div>
    <div id="option-chain-table">${renderOptionChain(chain)}</div>
  </div>`;
  // Bind expiration change
  chainSection.querySelector('#expiration-select')?.addEventListener('change', async (e) => {
    const exp = e.target.value;
    const selectionRequestId = ++state.expirationRequestId;
    const tbl = chainSection.querySelector('#option-chain-table');
    if (!tbl) return;
    tbl.innerHTML = '<div style="padding:32px;text-align:center;color:var(--color-muted)">加载中...</div>';
    const newChain = await safe(api.optionChain(ticker, exp));
    if (!isDetailActive(state)
      || requestId !== state.optionRequestId
      || selectionRequestId !== state.expirationRequestId
      || !tbl.isConnected) return;
    if (!newChain.__error) {
      tbl.innerHTML = renderOptionChain(newChain);
      // Re-render alerts for new expiration
      const newAlerts = newChain.alerts || [];
      if (newAlerts.length > 0) {
        alertsSection.innerHTML = `<section class="option-alert-section">${renderAlerts(newAlerts)}<div id="ai-analysis-mount"></div></section>`;
        renderAlertAnalysisButton(alertsSection.querySelector('#ai-analysis-mount'), ticker, newAlerts, newChain.underlying_price || 0, exp);
      } else {
        alertsSection.innerHTML = '';
      }
    } else {
      tbl.innerHTML = '<div style="padding:32px;text-align:center;color:var(--color-crimson)">该到期日加载失败，请重试</div>';
    }
  });
}

export function mountDetail(tickerFromRoute, options = {}) {
  const ticker = String(tickerFromRoute || '').trim().toUpperCase();
  if (!ticker) { location.hash = '#watchlist'; return; }
  const backRoute = /^#(watchlist|sectors|earnings|screener)$/.test(options.backRoute || '')
    ? options.backRoute
    : '#watchlist';

  // Per-mount state in closure. Older mounts can still run cleanup on their
  // own state without trampling on this mount.
  const mountId = ++__activeMountId;
  const state = {
    mountId,
    ticker,
    cancelled: false,
    chartHandle: null,
    chartTimer: null,
    quoteTimer: null,
    chartMode: readChartMode(),
    chartRequestId: 0,
    quoteRequestId: 0,
    marketRequestId: 0,
    optionRequestId: 0,
    expirationRequestId: 0,
    latestStock: null,
    marketStatus: null,
  };

  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = renderShell(ticker, backRoute, mountId);

  // Back nav
  queryInMount(state, '[data-back-breadcrumb]')?.addEventListener('click', (event) => {
    event.preventDefault();
    location.hash = backRoute;
  });

  // Render timeframe buttons
  let currentRange = '5m';
  const tf = queryInMount(state, '#tf-buttons');
  const drawTf = () => {
    if (!tf || !isDetailActive(state)) return;
    tf.innerHTML = TIMEFRAMES.map(([r, l]) =>
    `<button type="button" class="ethos-timeframe-button ${r === currentRange ? 'active' : ''}" data-range="${r}">${l}</button>`
    ).join('');
  };
  drawTf();
  tf?.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-range]');
    if (!b || !isDetailActive(state)) return;
    currentRange = b.dataset.range;
    drawTf();
    await loadChart(ticker, currentRange, state);
  });

  const chartModeButton = queryInMount(state, '#chart-mode-toggle');
  const drawChartMode = () => {
    if (!chartModeButton) return;
    const meta = chartModeMeta(state.chartMode);
    chartModeButton.title = meta.title;
    chartModeButton.setAttribute('aria-label', meta.title);
    chartModeButton.setAttribute('aria-pressed', state.chartMode === 'line' ? 'true' : 'false');
    chartModeButton.innerHTML = `
      <span class="material-symbols-outlined" aria-hidden="true">${meta.icon}</span>
      <span class="sr-only">${meta.title}</span>
    `;
  };
  drawChartMode();
  chartModeButton?.addEventListener('click', async () => {
    if (!isDetailActive(state)) return;
    state.chartMode = state.chartMode === 'line' ? 'candles' : 'line';
    writeChartMode(state.chartMode);
    drawChartMode();
    await loadChart(ticker, currentRange, state);
  });

  // Header + stats
  const placeholder = {
    ticker,
    name: ticker,
    name_en: ticker,
    description: '行情、技术信号与期权链分析',
    _updatedLabel: '正在加载行情…',
  };
  state.latestStock = placeholder;
  renderHeaderAndStats(placeholder, state.marketStatus, state);

  const loadQuote = async (force = false) => {
    const requestId = ++state.quoteRequestId;
    if (force) invalidateCache(`s:${ticker}`);
    const data = await safe(api.stock(ticker));
    if (!isDetailActive(state) || requestId !== state.quoteRequestId) return;
    if (data.__error) {
      state.latestStock = { ...state.latestStock, _updatedLabel: '行情刷新失败 · 保留上次显示' };
    } else {
      const fetched = data.fetched_at || data.as_of;
      const parsed = fetched ? new Date(fetched) : new Date();
      const time = Number.isNaN(parsed.getTime()) ? new Date() : parsed;
      state.latestStock = {
        ...data,
        ticker: data.ticker || ticker,
        _updatedLabel: `${data._stale || data.stale ? '陈旧数据' : '更新'} ${time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
      };
    }
    renderHeaderAndStats(state.latestStock, state.marketStatus, state);
  };

  const loadMarketStatus = async (force = false) => {
    const requestId = ++state.marketRequestId;
    if (force) invalidateCache('mkt');
    const data = await safe(api.marketStatus());
    if (!isDetailActive(state) || requestId !== state.marketRequestId) return;
    if (!data.__error) state.marketStatus = data;
    renderHeaderAndStats(state.latestStock, state.marketStatus, state);
  };
  loadQuote();
  loadMarketStatus();

  // Chart with auto-refresh
  state.chartTimer = setInterval(() => {
    if (!document.hidden && isDetailActive(state)) loadChart(ticker, currentRange, state, true);
  }, CHART_REFRESH_MS);
  loadChart(ticker, currentRange, state);

  state.quoteTimer = setInterval(() => {
    if (!document.hidden && isDetailActive(state)) {
      loadQuote(true);
      loadMarketStatus(true);
    }
  }, QUOTE_REFRESH_MS);

  const onVisibility = () => {
    if (!document.hidden && isDetailActive(state)) {
      loadQuote(true);
      loadMarketStatus(true);
    }
  };
  document.addEventListener('visibilitychange', onVisibility);

  // Top/Bottom signals (4 gauges + AI analysis)
  safe(api.topBottomSignals(ticker)).then(d => {
    if (!isDetailActive(state)) return;
    const el = queryInMount(state, '#top-bottom-signals');
    if (el) renderTopBottomSignals(el, ticker, d);
  });

  // Option alerts + chain
  loadOptionAlertsAndChain(ticker, state);

  // Cleanup on hash change — only this mount's state, not whatever's mounted now
  const cleanup = () => {
    state.cancelled = true;
    state.chartRequestId += 1;
    state.quoteRequestId += 1;
    state.marketRequestId += 1;
    state.optionRequestId += 1;
    if (state.chartTimer) { clearInterval(state.chartTimer); state.chartTimer = null; }
    if (state.quoteTimer) { clearInterval(state.quoteTimer); state.quoteTimer = null; }
    document.removeEventListener('visibilitychange', onVisibility);
    state.chartHandle?.destroy?.(); state.chartHandle = null;
    window.removeEventListener('hashchange', cleanup);
  };
  window.addEventListener('hashchange', cleanup, { once: true });
}

export const renderDetail = mountDetail;
