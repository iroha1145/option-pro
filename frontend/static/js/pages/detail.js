import { api, invalidateCache, safe } from '../api.js';
import { renderChart } from '../components/chart.js';
import { getMarketStatusForTicker } from '../components/marketStatus.js';
import { renderTopBottomSignals } from '../components/topBottomSignals.js';
import { renderOptionChain, renderAlerts } from '../components/optionChain.js';
import { renderAlertAnalysisButton } from '../components/aiAnalysis.js';
import { renderIcon } from '../icons.js';

const DETAIL_STYLES_ID = 'optix-detail-v3-styles';

function ensureDetailStyles() {
  if (document.getElementById(DETAIL_STYLES_ID)) return;
  const link = document.createElement('link');
  link.id = DETAIL_STYLES_ID;
  link.rel = 'stylesheet';
  link.href = new URL('../../css/optix-detail-v3.css', import.meta.url).href;
  document.head.append(link);
}

const TIMEFRAMES = [
  ['5m', '5分'],
  ['15m', '15分'],
  ['1h', '1时'],
  ['1d', '日K'],
  ['1w', '周K'],
];
const PERIOD_LABELS = {
  '5d': '近5日',
  '1mo': '近1月',
  '3mo': '近3月',
  '2y': '近2年',
  '5y': '近5年',
};
const CHART_REFRESH_MS = 5 * 60 * 1000;
const QUOTE_REFRESH_MS = 60 * 1000;
const CHART_MODE_KEY = 'optix-pro:detail-chart-mode';
const CHART_ADJUSTMENT_KEY = 'optix-pro:detail-chart-adjustment';

const esc = (value) => String(value ?? '').replace(
  /[&<>'"]/g,
  (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character]),
);

const formatPrice = (value, { currency = true } = {}) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const abs = Math.abs(number);
  let maximumFractionDigits = 2;
  let minimumFractionDigits = 2;
  if (abs < 1) {
    maximumFractionDigits = abs < 0.01 ? 8 : 6;
    minimumFractionDigits = 4;
  } else if (abs < 10) {
    maximumFractionDigits = 4;
  }
  const formatted = number.toLocaleString('en-US', { minimumFractionDigits, maximumFractionDigits });
  return currency ? `$${formatted}` : formatted;
};

const large = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  if (number >= 1e12) return `$${(number / 1e12).toFixed(2)}T`;
  if (number >= 1e9) return `$${(number / 1e9).toFixed(2)}B`;
  if (number >= 1e6) return `$${(number / 1e6).toFixed(1)}M`;
  return `$${number.toLocaleString()}`;
};

const num = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : '—';
};

const firstFinite = (...values) => {
  for (const value of values) {
    if (value === null || value === undefined || value === '') continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
};

function readStoredValue(key, allowed, fallback) {
  try {
    const value = localStorage.getItem(key);
    return allowed.includes(value) ? value : fallback;
  } catch (_) {
    return fallback;
  }
}

function writeStoredValue(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_) {
    // Storage may be unavailable in a private or restricted browser context.
  }
}

const chartModeMeta = (mode) => mode === 'line'
  ? { icon: 'candlestick_chart', title: '切换为K线图' }
  : { icon: 'show_chart', title: '切换为价格线图' };

function formatChartTimestamp(value, timeZone, { dateOnly = false } = {}) {
  const parsed = value ? new Date(value) : null;
  if (!parsed || Number.isNaN(parsed.getTime())) return '';
  try {
    return parsed.toLocaleString([], {
      timeZone,
      month: '2-digit',
      day: '2-digit',
      ...(dateOnly ? {} : { hour: '2-digit', minute: '2-digit' }),
    });
  } catch (_) {
    return parsed.toLocaleString([], {
      month: '2-digit',
      day: '2-digit',
      ...(dateOnly ? {} : { hour: '2-digit', minute: '2-digit' }),
    });
  }
}

const marketStatusIcon = (status) => {
  if (status?.tone === 'live') return 'wb_sunny';
  if (status?.tone === 'pre') return 'schedule';
  return 'dark_mode';
};

function blobToDataUrl(blob, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Request aborted', 'AbortError'));
      return;
    }
    const reader = new FileReader();
    const abort = () => reader.abort();
    const cleanup = () => signal?.removeEventListener('abort', abort);
    reader.addEventListener('load', () => {
      cleanup();
      resolve(String(reader.result || ''));
    }, { once: true });
    reader.addEventListener('error', () => {
      cleanup();
      reject(reader.error || new Error('Logo decoding failed'));
    }, { once: true });
    reader.addEventListener('abort', () => {
      cleanup();
      reject(new DOMException('Request aborted', 'AbortError'));
    }, { once: true });
    signal?.addEventListener('abort', abort, { once: true });
    reader.readAsDataURL(blob);
  });
}

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
  return `<section class="instrument-page" data-detail-mount="${mountId}" data-detail-ticker="${esc(ticker)}" aria-labelledby="instrument-title">
    <nav class="instrument-breadcrumb" aria-label="详情路径">
      <a href="${esc(backRoute)}" data-back-breadcrumb>返回列表</a>
      <span aria-hidden="true">/</span>
      <span class="mono">${esc(ticker)}</span>
    </nav>

    <header id="modal-header" class="instrument-quote"></header>

    <section class="instrument-chart-panel" aria-labelledby="instrument-chart-title">
      <div class="instrument-chart-heading">
        <div>
          <span class="instrument-eyebrow">价格走势</span>
          <h2 id="instrument-chart-title">市场价格与成交量</h2>
        </div>
        <div class="instrument-chart-controls">
          <div id="tf-buttons" class="instrument-segmented" role="group" aria-label="图表周期"></div>
          <div id="adjustment-buttons" class="instrument-segmented instrument-adjustment" role="group" aria-label="价格复权方式"></div>
          <button id="chart-mode-toggle" class="instrument-icon-button" type="button" aria-pressed="false">
            ${renderIcon('show_chart', { size: 20 })}
            <span class="sr-only">切换图表类型</span>
          </button>
        </div>
      </div>
      <div id="chart-context" class="instrument-chart-context" role="status" aria-live="polite">
        正在确认图表口径与数据时间…
      </div>
      <div id="modal-chart" class="instrument-chart-stage"></div>
    </section>

    <section id="modal-stats" class="instrument-key-stats" aria-label="关键市场数据"></section>

    <div class="instrument-disclosures">
      <details class="instrument-disclosure" data-detail-panel="signals">
        <summary>
          <span><strong>技术信号</strong><small>顶底信号、强弱判断与分析</small></span>
          <span data-signals-state>展开后读取</span>
        </summary>
        <div id="top-bottom-signals" class="instrument-disclosure-body" aria-label="顶底信号分析">
          <div class="instrument-idle-state">展开后读取最新技术信号。</div>
        </div>
      </details>

      <details class="instrument-disclosure" data-detail-panel="options">
        <summary>
          <span><strong>期权与异动</strong><small>到期日、平值附近合约与异动提示</small></span>
          <span data-options-state>展开后读取</span>
        </summary>
        <div class="instrument-disclosure-body instrument-options-body">
          <section id="detail-option-summary" class="instrument-option-summary" aria-labelledby="detail-option-summary-title">
            <h3 id="detail-option-summary-title">期权摘要</h3>
            <p>展开后读取最近到期日与成交结构。</p>
          </section>
          <section id="option-alerts-section" class="instrument-option-alerts" aria-label="期权异动"></section>
          <section id="option-chain-container" class="instrument-option-chain" aria-label="期权链">
            <div class="instrument-idle-state">期权链尚未加载。</div>
          </section>
        </div>
      </details>
    </div>
  </section>`;
}

function renderHeaderAndStats(stock, backendMarketStatus = null, state = null) {
  const header = state ? queryInMount(state, '#modal-header') : document.getElementById('modal-header');
  const stats = state ? queryInMount(state, '#modal-stats') : document.getElementById('modal-stats');
  if (!header || !stats) return;

  const price = firstFinite(stock.price);
  const changePercent = firstFinite(stock.change_percent);
  const absoluteChange = firstFinite(stock.change);
  const toneClass = changePercent > 0 ? 'is-positive' : changePercent < 0 ? 'is-negative' : 'is-neutral';
  const changeText = changePercent == null
    ? '行情暂不可用'
    : `${changePercent > 0 ? '+' : ''}${changePercent.toFixed(2)}%${absoluteChange == null ? '' : ` · ${absoluteChange > 0 ? '+' : ''}${formatPrice(absoluteChange)}`}`;
  const titleText = stock.name || stock.ticker;
  const showTicker = String(titleText || '').trim().toUpperCase() !== String(stock.ticker || '').trim().toUpperCase();
  const initial = (stock.ticker || '?')[0];
  const logoUrl = state?.logoDataUrl || '';
  const marketStatus = getMarketStatusForTicker(
    stock.ticker,
    backendMarketStatus,
    stock.exchange || stock.market || '',
  );
  const sessionIcon = marketStatusIcon(marketStatus);
  const sessionTitle = `${marketStatus.phase} · ${marketStatus.nextEventLabel} ${marketStatus.nextEventMarketText}`;

  header.innerHTML = `
    <div class="instrument-identity">
      <div class="instrument-logo" data-logo-shell>
        ${state ? `<img ${logoUrl ? `src="${esc(logoUrl)}"` : ''} alt="${esc(stock.name_en || stock.ticker)} 标志" decoding="async" data-company-logo>` : ''}
        <span data-logo-fallback>${esc(initial)}</span>
      </div>
      <div>
        <span class="instrument-eyebrow">标的研究</span>
        <h1 id="instrument-title">${esc(titleText)}${showTicker ? `<span class="mono">${esc(stock.ticker)}</span>` : ''}</h1>
        <p class="instrument-session-line">
          ${renderIcon(sessionIcon, { className: `instrument-session-icon is-${marketStatus.tone}`, size: 17, label: sessionTitle })}
          <span>${esc(marketStatus.market.label)} · ${esc(marketStatus.phase)}</span>
          <span aria-hidden="true">·</span>
          <span>${esc(marketStatus.nextEventLabel)} ${esc(marketStatus.nextEventMarketText)}</span>
        </p>
      </div>
    </div>
    <div class="instrument-live-quote">
      <span>最新价格</span>
      <strong class="mono">${formatPrice(price)}</strong>
      <span class="mono ${toneClass}">${esc(changeText)}</span>
      <small data-detail-updated aria-live="polite">${esc(stock._updatedLabel || '等待行情更新')}</small>
    </div>`;

  const logoImage = header.querySelector('[data-company-logo]');
  if (state && logoImage) {
    const showLogo = (url) => {
      if (!url || !isDetailActive(state)) return;
      const currentImage = queryInMount(state, '[data-company-logo]');
      const shell = currentImage?.closest('[data-logo-shell]');
      if (!currentImage || !shell) return;
      currentImage.addEventListener('load', () => shell.classList.add('has-logo'), { once: true });
      currentImage.addEventListener('error', () => shell.classList.add('logo-failed'), { once: true });
      currentImage.src = url;
    };
    if (state.logoDataUrl) {
      showLogo(state.logoDataUrl);
    } else {
      if (!state.logoPromise && !state.logoFailed) {
        state.logoPromise = api.stockLogo(state.ticker, { signal: state.signal })
          .then((blob) => blobToDataUrl(blob, state.signal))
          .then((dataUrl) => {
            if (!isDetailActive(state)) return '';
            state.logoDataUrl = dataUrl;
            return dataUrl;
          })
          .catch((error) => {
            if (error?.name !== 'AbortError') state.logoFailed = true;
            return '';
          });
      }
      state.logoPromise?.then(showLogo);
    }
  }

  const quickStats = [
    ['开盘', formatPrice(stock.open ?? stock.o)],
    ['最高', formatPrice(stock.day_high ?? stock.high)],
    ['最低', formatPrice(stock.day_low ?? stock.low)],
    ['成交量', num(stock.volume)],
    ['市值', large(stock.market_cap)],
    ['市盈率（P/E）', firstFinite(stock.pe_ratio, stock.pe)?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || '—'],
  ];
  stats.innerHTML = `<div class="instrument-key-stats-heading">
      <span class="instrument-eyebrow">关键数据</span>
      <h2>今日价格与估值</h2>
    </div>
    <dl>
      ${quickStats.map(([key, value]) => `<div><dt>${esc(key)}</dt><dd class="mono">${esc(value)}</dd></div>`).join('')}
    </dl>`;
}

function renderChartContext(data, state) {
  const context = queryInMount(state, '#chart-context');
  if (!context) return;
  const stale = Boolean(data?._stale || data?.stale || data?.source_status === 'degraded');
  const empty = data?.source_status === 'empty' || !Array.isArray(data?.bars) || data.bars.length === 0;
  const exchangeTimezone = data?.exchange_timezone || 'America/New_York';
  const intraday = /(?:m|h)$/.test(String(data?.interval || state.range));
  const lastBarExchange = formatChartTimestamp(data?.last_bar_at, exchangeTimezone, { dateOnly: !intraday });
  const lastBarLocal = formatChartTimestamp(data?.last_bar_at, undefined, { dateOnly: !intraday });
  const fetchedLocal = formatChartTimestamp(data?.as_of, undefined);
  const period = PERIOD_LABELS[data?.period] || data?.period || TIMEFRAMES.find(([range]) => range === state.range)?.[1] || state.range;
  const adjustment = data?.price_adjustment === 'adjusted' ? '复权价格' : '原始价格';
  const extended = data?.include_extended_hours ? '含盘前 / 盘后' : '不含盘前 / 盘后';
  const statusLabel = stale ? '陈旧数据' : empty ? '暂无数据' : '数据正常';
  context.innerHTML = `
    <span>${esc(period)} · ${esc(data?.interval || state.range)} · ${esc(adjustment)}</span>
    <span>${esc(extended)} · 均线仅用常规盘</span>
    <span title="交易所时区：${esc(exchangeTimezone)}">${lastBarExchange ? `最新K线 ${esc(lastBarExchange)}（交易所）${lastBarLocal && lastBarLocal !== lastBarExchange ? ` · ${esc(lastBarLocal)}（本地）` : ''}` : 'K线时间待确认'}</span>
    <span class="instrument-data-status ${stale ? 'is-stale' : empty ? 'is-empty' : 'is-active'}">${statusLabel}</span>
    <span>${fetchedLocal ? `获取 ${esc(fetchedLocal)}（本地）` : '获取时间待确认'}</span>`;
}

function renderChartPayload(data, state) {
  const el = queryInMount(state, '#modal-chart');
  if (!el) return;
  state.chartHandle?.destroy?.();
  state.chartHandle = null;
  if (!Array.isArray(data?.bars) || data.bars.length === 0) {
    el.innerHTML = `<div class="instrument-empty-state">
      <strong>所选周期暂无价格数据</strong>
      <span>数据源没有返回有效K线，页面不会用零值或模拟价格填补。</span>
      <button type="button" data-chart-retry>重新读取</button>
    </div>`;
    el.querySelector('[data-chart-retry]')?.addEventListener('click', () => {
      loadChart(state.ticker, state.range, state, true);
    });
    return;
  }
  const handle = renderChart(el, data, data.visible || 0, {
    mode: state.chartMode,
    exchangeTimezone: data.exchange_timezone,
  });
  if (!isDetailActive(state)) {
    handle?.destroy?.();
    return;
  }
  state.chartHandle = handle;
}

async function loadChart(ticker, range, state, force = false) {
  const requestId = ++state.chartRequestId;
  state.range = range;
  state.chartController?.abort();
  const chartController = new AbortController();
  state.chartController = chartController;
  const el = queryInMount(state, '#modal-chart');
  const context = queryInMount(state, '#chart-context');
  if (!el) return;
  state.chartHandle?.destroy?.();
  state.chartHandle = null;
  state.lastChartData = null;
  el.innerHTML = `<div class="instrument-loading-state" role="status">
    <span aria-hidden="true"></span><strong>正在读取价格走势</strong>
  </div>`;
  if (context) context.textContent = '正在确认图表口径与数据时间…';
  if (force) invalidateCache(`c:${encodeURIComponent(ticker.toUpperCase())}:${range}:${state.adjustment}`);

  const data = await safe(api.chart(ticker, range, {
    signal: chartController.signal,
    adjustment: state.adjustment,
  }));
  if (!isDetailActive(state) || requestId !== state.chartRequestId || !el.isConnected) return;
  if (data.__error) {
    el.innerHTML = `<div class="instrument-error-state" role="alert">
      <strong>图表加载失败</strong>
      <span>行情源暂时没有响应，已停止显示旧周期或旧复权口径。</span>
      <button type="button" data-chart-retry>重试</button>
    </div>`;
    if (context) context.textContent = '图表口径未更新 · 未显示旧请求结果';
    el.querySelector('[data-chart-retry]')?.addEventListener('click', () => loadChart(ticker, range, state, true));
    return;
  }
  state.lastChartData = data;
  renderChartContext(data, state);
  renderChartPayload(data, state);
}

function summarizeOptionChain(chain = {}) {
  const groups = chain.grouped_by_strike && typeof chain.grouped_by_strike === 'object'
    ? Object.values(chain.grouped_by_strike)
    : [];
  let callVolume = 0;
  let putVolume = 0;
  let callOi = 0;
  let putOi = 0;
  groups.forEach((group = {}) => {
    callVolume += firstFinite(group.call?.volume, group.call?.vol) || 0;
    putVolume += firstFinite(group.put?.volume, group.put?.vol) || 0;
    callOi += firstFinite(group.call?.open_interest, group.call?.oi, group.call?.openInterest) || 0;
    putOi += firstFinite(group.put?.open_interest, group.put?.oi, group.put?.openInterest) || 0;
  });
  return {
    alerts: Array.isArray(chain.alerts) ? chain.alerts.length : 0,
    callVolume,
    putVolume,
    callOi,
    putOi,
    volumeRatio: callVolume > 0 ? putVolume / callVolume : null,
    oiRatio: callOi > 0 ? putOi / callOi : null,
  };
}

function renderOptionSummary(chain, expiration, state) {
  const container = queryInMount(state, '#detail-option-summary');
  if (!container) return;
  const summary = summarizeOptionChain(chain);
  const volumeRatio = Number.isFinite(summary.volumeRatio) ? summary.volumeRatio.toFixed(2) : '—';
  const oiRatio = Number.isFinite(summary.oiRatio) ? summary.oiRatio.toFixed(2) : '—';
  container.innerHTML = `
    <div>
      <span class="instrument-eyebrow">期权摘要</span>
      <h3 id="detail-option-summary-title">${esc(expiration || '最近到期')}</h3>
    </div>
    <dl>
      <div><dt>看跌 / 看涨成交</dt><dd class="mono">${volumeRatio}</dd></div>
      <div><dt>看跌 / 看涨持仓</dt><dd class="mono">${oiRatio}</dd></div>
      <div><dt>异动提示</dt><dd class="mono">${summary.alerts}</dd></div>
    </dl>
    <p>看涨 ${num(summary.callVolume)} · 看跌 ${num(summary.putVolume)}</p>`;
}

function renderOptionSummaryState(state, message, tone = '') {
  const container = queryInMount(state, '#detail-option-summary');
  if (!container) return;
  container.innerHTML = `
    <span class="instrument-eyebrow">期权摘要</span>
    <h3 id="detail-option-summary-title">${tone === 'error' ? '暂不可用' : '正在读取'}</h3>
    <p class="${tone ? `is-${tone}` : ''}">${esc(message)}</p>`;
}

function updateDisclosureState(state, selector, label) {
  const element = queryInMount(state, selector);
  if (element) element.textContent = label;
}

function renderAlertAnalysis(container, ticker, alerts, chain, expiration, state) {
  if (!container) return;
  const underlyingPrice = firstFinite(chain?.underlying_price);
  if (!(underlyingPrice > 0)) {
    container.innerHTML = '<p class="instrument-analysis-note">标的现价缺失，本次不生成依赖现价的异动分析。</p>';
    return;
  }
  renderAlertAnalysisButton(
    container,
    ticker,
    alerts,
    underlyingPrice,
    expiration,
    { signal: state.signal },
  );
}

async function loadSignals(ticker, state) {
  const requestId = ++state.signalRequestId;
  const element = queryInMount(state, '#top-bottom-signals');
  if (!element) return;
  element.innerHTML = '<div class="instrument-loading-state"><span aria-hidden="true"></span><strong>正在读取技术信号</strong></div>';
  updateDisclosureState(state, '[data-signals-state]', '正在读取');
  const data = await safe(api.topBottomSignals(ticker, { signal: state.signal }));
  if (!isDetailActive(state) || requestId !== state.signalRequestId) return;
  if (data.__error) {
    element.innerHTML = `<div class="instrument-error-state" role="alert">
      <strong>技术信号暂不可用</strong>
      <button type="button" data-signals-retry>重试</button>
    </div>`;
    updateDisclosureState(state, '[data-signals-state]', '读取失败');
    element.querySelector('[data-signals-retry]')?.addEventListener('click', () => loadSignals(ticker, state));
    return;
  }
  renderTopBottomSignals(element, ticker, data, { signal: state.signal });
  state.signalsLoaded = true;
  updateDisclosureState(state, '[data-signals-state]', data._stale ? '陈旧数据' : '已更新');
}

async function loadOptionAlertsAndChain(ticker, state) {
  const requestId = ++state.optionRequestId;
  const alertsSection = queryInMount(state, '#option-alerts-section');
  const chainSection = queryInMount(state, '#option-chain-container');
  if (!alertsSection || !chainSection) return;
  alertsSection.innerHTML = '';
  chainSection.innerHTML = '<div class="instrument-loading-state"><span aria-hidden="true"></span><strong>正在读取期权到期日</strong></div>';
  renderOptionSummaryState(state, '正在读取最近到期日与成交结构');
  updateDisclosureState(state, '[data-options-state]', '正在读取');

  const expirationsResponse = await safe(api.expirations(ticker, { signal: state.signal }));
  if (!isDetailActive(state) || requestId !== state.optionRequestId) return;
  if (expirationsResponse.__error) {
    chainSection.innerHTML = `<div class="instrument-error-state" role="alert">
      <strong>到期日加载失败</strong><button type="button" data-options-retry>重试</button>
    </div>`;
    renderOptionSummaryState(state, '期权到期日暂时无法读取', 'error');
    updateDisclosureState(state, '[data-options-state]', '读取失败');
    chainSection.querySelector('[data-options-retry]')?.addEventListener('click', () => loadOptionAlertsAndChain(ticker, state));
    return;
  }
  const expirations = expirationsResponse.expirations || [];
  if (!expirations.length) {
    chainSection.innerHTML = `<div class="instrument-empty-state">
      <strong>暂无可用期权到期日</strong><span>价格与技术信号仍可正常查看。</span>
    </div>`;
    renderOptionSummaryState(state, '当前没有可用期权到期日');
    state.optionsLoaded = true;
    updateDisclosureState(state, '[data-options-state]', '暂无合约');
    return;
  }

  const selected = expirations[0];
  renderOptionSummaryState(state, `正在读取 ${selected} 到期合约`);
  const chain = await safe(api.optionChain(ticker, selected, { signal: state.signal }));
  if (!isDetailActive(state) || requestId !== state.optionRequestId) return;
  if (chain.__error) {
    chainSection.innerHTML = `<div class="instrument-error-state" role="alert">
      <strong>期权链加载失败</strong><button type="button" data-options-retry>重试</button>
    </div>`;
    renderOptionSummaryState(state, `${selected} 到期合约暂不可用`, 'error');
    updateDisclosureState(state, '[data-options-state]', '读取失败');
    chainSection.querySelector('[data-options-retry]')?.addEventListener('click', () => loadOptionAlertsAndChain(ticker, state));
    return;
  }

  renderOptionSummary(chain, selected, state);
  const alerts = chain.alerts || [];
  if (alerts.length > 0) {
    alertsSection.innerHTML = `<section class="option-alert-section">${renderAlerts(alerts)}<div id="ai-analysis-mount"></div></section>`;
    renderAlertAnalysis(alertsSection.querySelector('#ai-analysis-mount'), ticker, alerts, chain, selected, state);
  }

  const hasUnderlyingPrice = firstFinite(chain.underlying_price) > 0;
  chainSection.innerHTML = `<section aria-labelledby="instrument-option-chain-title">
    <div class="instrument-option-heading">
      <div>
        <span class="instrument-eyebrow">期权链</span>
        <h3 id="instrument-option-chain-title">${hasUnderlyingPrice ? '平值附近 ±10 档' : '可用行权价'}</h3>
      </div>
      <label>
        <span>到期日</span>
        <select id="expiration-select" aria-label="期权到期日">
          ${expirations.map((expiration) => `<option value="${esc(expiration)}" ${expiration === selected ? 'selected' : ''}>${esc(expiration)}</option>`).join('')}
        </select>
      </label>
    </div>
    <div id="option-chain-table" class="instrument-option-table">${renderOptionChain(chain)}</div>
  </section>`;
  state.optionsLoaded = true;
  updateDisclosureState(state, '[data-options-state]', expirationsResponse._stale || chain._stale ? '陈旧数据' : '已更新');

  chainSection.querySelector('#expiration-select')?.addEventListener('change', async (event) => {
    const expiration = event.target.value;
    const selectionRequestId = ++state.expirationRequestId;
    const table = chainSection.querySelector('#option-chain-table');
    if (!table) return;
    table.innerHTML = '<div class="instrument-loading-state"><span aria-hidden="true"></span><strong>正在切换到期日</strong></div>';
    renderOptionSummaryState(state, `正在读取 ${expiration} 到期合约`);
    updateDisclosureState(state, '[data-options-state]', '正在切换');
    const newChain = await safe(api.optionChain(ticker, expiration, { signal: state.signal }));
    if (
      !isDetailActive(state)
      || requestId !== state.optionRequestId
      || selectionRequestId !== state.expirationRequestId
      || !table.isConnected
    ) return;
    if (newChain.__error) {
      table.innerHTML = '<div class="instrument-error-state" role="alert"><strong>该到期日加载失败</strong></div>';
      renderOptionSummaryState(state, `${expiration} 到期合约暂不可用`, 'error');
      updateDisclosureState(state, '[data-options-state]', '读取失败');
      return;
    }
    table.innerHTML = renderOptionChain(newChain);
    const chainTitle = chainSection.querySelector('#instrument-option-chain-title');
    if (chainTitle) {
      chainTitle.textContent = firstFinite(newChain.underlying_price) > 0 ? '平值附近 ±10 档' : '可用行权价';
    }
    renderOptionSummary(newChain, expiration, state);
    updateDisclosureState(state, '[data-options-state]', newChain._stale ? '陈旧数据' : '已更新');
    const newAlerts = newChain.alerts || [];
    if (newAlerts.length > 0) {
      alertsSection.innerHTML = `<section class="option-alert-section">${renderAlerts(newAlerts)}<div id="ai-analysis-mount"></div></section>`;
      renderAlertAnalysis(alertsSection.querySelector('#ai-analysis-mount'), ticker, newAlerts, newChain, expiration, state);
    } else {
      alertsSection.innerHTML = '';
    }
  });
}

export function mountDetail(tickerFromRoute, options = {}) {
  ensureDetailStyles();
  const ticker = String(tickerFromRoute || '').trim().toUpperCase();
  if (!ticker) {
    location.hash = '#watchlist';
    return;
  }
  const backRoute = /^#(watchlist|sectors|earnings|screener)$/.test(options.backRoute || '')
    ? options.backRoute
    : '#watchlist';

  const mountId = ++__activeMountId;
  const abortController = new AbortController();
  const state = {
    mountId,
    ticker,
    range: '5m',
    adjustment: readStoredValue(CHART_ADJUSTMENT_KEY, ['raw', 'adjusted'], 'raw'),
    chartMode: readStoredValue(CHART_MODE_KEY, ['candles', 'line'], 'candles'),
    cancelled: false,
    chartHandle: null,
    chartController: null,
    chartTimer: null,
    quoteTimer: null,
    chartRequestId: 0,
    quoteRequestId: 0,
    marketRequestId: 0,
    signalRequestId: 0,
    optionRequestId: 0,
    expirationRequestId: 0,
    signalsLoaded: false,
    optionsLoaded: false,
    latestStock: null,
    lastChartData: null,
    marketStatus: null,
    abortController,
    signal: abortController.signal,
    logoPromise: null,
    logoDataUrl: '',
    logoFailed: false,
  };

  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = renderShell(ticker, backRoute, mountId);

  queryInMount(state, '[data-back-breadcrumb]')?.addEventListener('click', (event) => {
    event.preventDefault();
    location.hash = backRoute;
  });

  const timeframeButtons = queryInMount(state, '#tf-buttons');
  const drawTimeframes = () => {
    if (!timeframeButtons || !isDetailActive(state)) return;
    timeframeButtons.innerHTML = TIMEFRAMES.map(([range, label]) => `
      <button type="button" class="${range === state.range ? 'is-active' : ''}" data-range="${range}" aria-pressed="${range === state.range}">${label}</button>
    `).join('');
  };
  drawTimeframes();
  timeframeButtons?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-range]');
    if (!button || !isDetailActive(state) || button.dataset.range === state.range) return;
    state.range = button.dataset.range;
    drawTimeframes();
    loadChart(ticker, state.range, state);
  });

  const adjustmentButtons = queryInMount(state, '#adjustment-buttons');
  const drawAdjustment = () => {
    if (!adjustmentButtons || !isDetailActive(state)) return;
    adjustmentButtons.innerHTML = [
      ['raw', '原始'],
      ['adjusted', '复权'],
    ].map(([value, label]) => `
      <button type="button" class="${value === state.adjustment ? 'is-active' : ''}" data-adjustment="${value}" aria-pressed="${value === state.adjustment}">${label}</button>
    `).join('');
  };
  drawAdjustment();
  adjustmentButtons?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-adjustment]');
    if (!button || !isDetailActive(state) || button.dataset.adjustment === state.adjustment) return;
    state.adjustment = button.dataset.adjustment;
    writeStoredValue(CHART_ADJUSTMENT_KEY, state.adjustment);
    drawAdjustment();
    loadChart(ticker, state.range, state);
  });

  const chartModeButton = queryInMount(state, '#chart-mode-toggle');
  const drawChartMode = () => {
    if (!chartModeButton) return;
    const meta = chartModeMeta(state.chartMode);
    chartModeButton.title = meta.title;
    chartModeButton.setAttribute('aria-label', meta.title);
    chartModeButton.setAttribute('aria-pressed', state.chartMode === 'line' ? 'true' : 'false');
    chartModeButton.innerHTML = `${renderIcon(meta.icon, { size: 20 })}<span class="sr-only">${meta.title}</span>`;
  };
  drawChartMode();
  chartModeButton?.addEventListener('click', () => {
    if (!isDetailActive(state)) return;
    state.chartMode = state.chartMode === 'line' ? 'candles' : 'line';
    writeStoredValue(CHART_MODE_KEY, state.chartMode);
    drawChartMode();
    if (state.lastChartData) renderChartPayload(state.lastChartData, state);
  });

  const placeholder = {
    ticker,
    name: ticker,
    name_en: ticker,
    _updatedLabel: '正在加载行情…',
  };
  state.latestStock = placeholder;
  renderHeaderAndStats(placeholder, state.marketStatus, state);

  const loadQuote = async (force = false) => {
    const requestId = ++state.quoteRequestId;
    if (force) invalidateCache(`s:${encodeURIComponent(ticker)}`);
    const data = await safe(api.stock(ticker, { signal: state.signal }));
    if (!isDetailActive(state) || requestId !== state.quoteRequestId) return;
    if (data.__error) {
      state.latestStock = { ...state.latestStock, _updatedLabel: '行情刷新失败 · 保留上次显示' };
    } else {
      const fetched = data.fetched_at || data.as_of;
      const parsed = fetched ? new Date(fetched) : new Date();
      const time = Number.isNaN(parsed.getTime()) ? new Date() : parsed;
      const stale = data._stale || data.stale || data.source_status === 'degraded';
      state.latestStock = {
        ...data,
        ticker: data.ticker || ticker,
        _updatedLabel: `${stale ? '陈旧数据' : '更新'} ${time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
      };
    }
    renderHeaderAndStats(state.latestStock, state.marketStatus, state);
  };

  const loadMarketStatus = async (force = false) => {
    const requestId = ++state.marketRequestId;
    if (force) invalidateCache('mkt');
    const data = await safe(api.marketStatus({ signal: state.signal }));
    if (!isDetailActive(state) || requestId !== state.marketRequestId) return;
    if (!data.__error) state.marketStatus = data;
    renderHeaderAndStats(state.latestStock, state.marketStatus, state);
  };

  loadQuote();
  loadMarketStatus();
  loadChart(ticker, state.range, state);

  state.chartTimer = setInterval(() => {
    if (!document.hidden && isDetailActive(state)) loadChart(ticker, state.range, state, true);
  }, CHART_REFRESH_MS);
  state.quoteTimer = setInterval(() => {
    if (!document.hidden && isDetailActive(state)) {
      loadQuote(true);
      loadMarketStatus(true);
    }
  }, QUOTE_REFRESH_MS);

  queryInMount(state, '[data-detail-panel="signals"]')?.addEventListener('toggle', (event) => {
    if (event.currentTarget.open && !state.signalsLoaded) loadSignals(ticker, state);
  });
  queryInMount(state, '[data-detail-panel="options"]')?.addEventListener('toggle', (event) => {
    if (event.currentTarget.open && !state.optionsLoaded) loadOptionAlertsAndChain(ticker, state);
  });

  const onVisibility = () => {
    if (!document.hidden && isDetailActive(state)) {
      loadQuote(true);
      loadMarketStatus(true);
    }
  };
  document.addEventListener('visibilitychange', onVisibility);

  const cleanup = () => {
    state.cancelled = true;
    state.chartRequestId += 1;
    state.quoteRequestId += 1;
    state.marketRequestId += 1;
    state.signalRequestId += 1;
    state.optionRequestId += 1;
    state.chartController?.abort();
    state.abortController.abort();
    state.logoDataUrl = '';
    document.querySelector(`[data-detail-mount="${state.mountId}"]`)
      ?.querySelectorAll('img[data-company-logo]')
      .forEach((image) => {
        image.removeAttribute('src');
        image.remove();
      });
    if (state.chartTimer) clearInterval(state.chartTimer);
    if (state.quoteTimer) clearInterval(state.quoteTimer);
    document.removeEventListener('visibilitychange', onVisibility);
    state.chartHandle?.destroy?.();
    state.chartHandle = null;
    window.removeEventListener('hashchange', cleanup);
  };
  window.addEventListener('hashchange', cleanup, { once: true });
}

export const renderDetail = mountDetail;
