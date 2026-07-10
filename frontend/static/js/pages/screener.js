import { api } from '../api.js';

const state = {
  timeframe: 'all',
  profile: 'balanced',
  top: 20,
  sectorId: '',
  loading: false,
  payload: null,
  profiles: null,
};

const FALLBACK_OPTION_SOURCES = [
  { name: 'MarketData.app', url: 'https://www.marketdata.app/docs/api/', access: 'Free Forever', note: '延迟期权链/报价，100 daily credits' },
  { name: 'Tradier', url: 'https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains.md', access: 'Developer', note: '期权链、IV/Greeks、expiration/strike' },
  { name: 'tastytrade', url: 'https://tastytrade.com/api/', access: 'Broker', note: '实时报价与期权链，需要账户登录' },
  { name: 'Alpha Vantage', url: 'https://www.alphavantage.co/documentation/', access: 'Free key', note: '有期权端点，完整历史/实时多为 premium' },
];

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function formatScore(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : '—';
}

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `$${number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPercent(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `${number > 0 ? '+' : ''}${number.toFixed(digits)}%`;
}

function scoreTone(score) {
  const value = Number(score);
  if (!Number.isFinite(value)) return 'neutral';
  if (value >= 72) return 'strong';
  if (value >= 58) return 'watch';
  return 'weak';
}

function sourceStatusLabel(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'active') return '已启用';
  if (normalized === 'degraded') return '降级';
  if (normalized === 'not_configured') return '未配置';
  if (normalized === 'placeholder') return '待接入';
  if (normalized === 'disabled') return '关闭';
  return '未知';
}

function sourceStatusTone(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'active') return 'active';
  if (normalized === 'degraded') return 'degraded';
  return 'idle';
}

function navigateToDetail(ticker) {
  if (!ticker) return;
  window.location.hash = `#detail/${encodeURIComponent(ticker)}`;
}

function renderScoreBar(value, label) {
  const score = Math.max(0, Math.min(100, Number(value) || 0));
  return `
    <span class="strength-scorebar" title="${escapeHtml(label)} ${score.toFixed(1)}">
      <small>${escapeHtml(label)}</small>
      <i><b style="width:${score}%"></b></i>
      <em class="mono font-data-mono" data-numeric>${score.toFixed(0)}</em>
    </span>
  `;
}

function renderMarketCard(market = {}) {
  const tone = scoreTone(market.score);
  const warnings = Array.isArray(market.warnings) ? market.warnings : [];
  return `
    <section class="strength-market-card strength-market-card--${tone}">
      <span class="label-caps">市场状态</span>
      <div class="strength-market-score">
        <strong class="mono font-data-mono" data-numeric>${formatScore(market.score)}</strong>
        <span>${escapeHtml(market.label || '中性')}</span>
      </div>
      <div class="strength-market-metrics">
        <span><small>趋势</small><b class="mono">${formatScore(market.index_trend_score)}</b></span>
        <span><small>动量</small><b class="mono">${formatScore(market.market_momentum_score)}</b></span>
        <span><small>宽度</small><b class="mono">${formatScore(market.market_breadth_score)}</b></span>
        <span><small>量能</small><b class="mono">${formatScore(market.market_volume_score)}</b></span>
        <span><small>价差</small><b class="mono">${formatScore(market.risk_on_spread_score)}</b></span>
        <span><small>风险偏好</small><b class="mono">${formatScore(market.risk_appetite_score)}</b></span>
      </div>
      <div class="strength-market-grid">
        <span><small>SPY 20D</small><b class="mono ${Number(market.spy_20d) >= 0 ? 'up' : 'down'}">${formatPercent(market.spy_20d)}</b></span>
        <span><small>QQQ 20D</small><b class="mono ${Number(market.qqq_20d) >= 0 ? 'up' : 'down'}">${formatPercent(market.qqq_20d)}</b></span>
        <span><small>IWM 20D</small><b class="mono ${Number(market.iwm_20d) >= 0 ? 'up' : 'down'}">${formatPercent(market.iwm_20d)}</b></span>
        <span><small>VIX</small><b class="mono">${market.vix == null ? '—' : Number(market.vix).toFixed(2)}</b></span>
      </div>
      ${warnings.length ? `<div class="strength-market-warnings">${warnings.slice(0, 2).map((warning) => `<span>${escapeHtml(warning)}</span>`).join('')}</div>` : ''}
    </section>
  `;
}

function renderSpreadPanel(payload = {}) {
  const market = payload.market_regime || {};
  const spreads = payload.spread_matrix || market.spread_matrix || {};
  const order = ['qqq_spy', 'xlk_spy', 'soxx_xlk', 'iwm_spy', 'rsp_spy', 'xly_xlp', 'hyg_ief', 'spy_gld'];
  const items = order.map((key) => spreads[key]).filter(Boolean);
  if (!items.length && !market.risk_on_spread_score) return '';
  return `
    <section class="strength-spread-panel">
      <div class="section-card-heading">
        <span class="label-caps">价差矩阵</span>
        <h2>${escapeHtml(market.risk_on_spread_label || '风险偏好价差')}</h2>
      </div>
      <div class="strength-spread-score">
        <strong class="mono font-data-mono" data-numeric>${formatScore(market.risk_on_spread_score)}</strong>
        <span>${escapeHtml(market.market_context?.valuation_status === 'not_available' ? '估值暂不参与短线触发' : 'Market Context')}</span>
      </div>
      <div class="strength-spread-list">
        ${items.slice(0, 8).map((item) => {
          const score = Number(item.score);
          const tone = score >= 65 ? 'up' : (score < 45 ? 'down' : '');
          return `
            <div class="strength-spread-item">
              <span>
                <strong>${escapeHtml(item.name || item.key)}</strong>
                <small>${escapeHtml(item.label || '中性')}</small>
              </span>
              <em class="mono ${tone}" data-numeric>${formatScore(item.score)}</em>
            </div>
          `;
        }).join('')}
      </div>
    </section>
  `;
}

function renderSectorRail(sectors = []) {
  const sectorOptions = [{ id: '', name: '全部', avg_strength: null }, ...sectors];
  return `
    <section class="strength-side-panel">
      <div class="section-card-heading">
        <span class="label-caps">板块强度</span>
        <h2>主题轮动</h2>
      </div>
      <div class="strength-sector-list">
        ${sectorOptions.map((sector) => {
          const id = sector.id || sector.sector_id || '';
          const active = String(state.sectorId || '') === String(id || '');
          const score = sector.avg_strength == null ? null : Number(sector.avg_strength);
          return `
            <button class="strength-sector-item ${active ? 'is-active' : ''}" type="button" data-sector-id="${escapeHtml(id)}">
              <span>
                <strong>${escapeHtml(sector.name || id || '全部')}</strong>
                ${sector.count ? `<small>${sector.count} 只</small>` : '<small>主题池</small>'}
              </span>
              <em class="mono font-data-mono" data-numeric>${Number.isFinite(score) ? score.toFixed(1) : 'ALL'}</em>
            </button>
          `;
        }).join('')}
      </div>
    </section>
  `;
}

function renderDataNote(dataSources = {}) {
  const fundamentals = dataSources.fundamentals || {};
  const options = dataSources.options || {};
  const optionStatus = String(options.status || '').toLowerCase();
  const candidates = Array.isArray(options.candidates) && options.candidates.length
    ? options.candidates
    : FALLBACK_OPTION_SOURCES;
  const names = candidates.slice(0, 4).map((item) => item.name).join(' / ');
  const optionProvider = options.provider || 'Yahoo/yfinance / MarketData.app';
  const optionText = optionStatus === 'active'
    ? `期权热度 ${optionProvider} ${sourceStatusLabel(options.status)}`
    : `期权热度 ${sourceStatusLabel(options.status || 'placeholder')}，可接入 ${names}`;
  return `
    <p class="detail-muted strength-data-note">
      价格源 Yahoo/yfinance · Finnhub ${sourceStatusLabel(fundamentals.status)} · ${escapeHtml(optionText)}
    </p>
  `;
}

function renderCacheNote(payload = {}) {
  if (!payload.cache_expires_at) return '';
  const expires = new Date(payload.cache_expires_at);
  const timeLabel = Number.isNaN(expires.getTime())
    ? ''
    : expires.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const status = payload._cached || payload._client_cached ? '缓存命中' : '刚刚刷新';
  return `<p class="detail-muted strength-cache-note">${status}${timeLabel ? ` · 保留到 ${escapeHtml(timeLabel)}` : ''}</p>`;
}

function renderDataSourcePanel(dataSources = {}) {
  const fundamentals = dataSources.fundamentals || {};
  const options = dataSources.options || {};
  const candidates = Array.isArray(options.candidates) && options.candidates.length
    ? options.candidates
    : FALLBACK_OPTION_SOURCES;
  const fundamentalsTone = sourceStatusTone(fundamentals.status || 'not_configured');
  const optionsTone = sourceStatusTone(options.status || 'placeholder');
  const optionsProvider = options.provider || 'Yahoo/yfinance / MarketData.app';
  const broad = options.broad || {};
  const refinement = options.refinement || {};
  return `
    <section class="strength-source-panel">
      <div class="section-card-heading">
        <span class="label-caps">数据源</span>
        <h2>信号输入</h2>
      </div>
      <div class="strength-source-stack">
        <div class="strength-source-row">
          <span>价格/量能</span>
          <strong>Yahoo/yfinance</strong>
          <em class="is-active">已启用</em>
        </div>
        <div class="strength-source-row">
          <span>基本面</span>
          <strong>Finnhub</strong>
          <em class="is-${fundamentalsTone}">${sourceStatusLabel(fundamentals.status)}</em>
        </div>
        <div class="strength-source-row">
          <span>期权热度</span>
          <strong>${escapeHtml(optionsProvider)}</strong>
          <em class="is-${optionsTone}">${sourceStatusLabel(options.status || 'placeholder')}</em>
        </div>
        ${broad.provider ? `
          <div class="strength-source-row">
            <span>期权粗筛</span>
            <strong>${escapeHtml(broad.provider)}</strong>
            <em class="is-${sourceStatusTone(broad.status || 'placeholder')}">${sourceStatusLabel(broad.status || 'placeholder')}</em>
          </div>
        ` : ''}
        ${refinement.provider ? `
          <div class="strength-source-row">
            <span>前排精修</span>
            <strong>${escapeHtml(refinement.provider)}</strong>
            <em class="is-${sourceStatusTone(refinement.status || 'placeholder')}">${sourceStatusLabel(refinement.status || 'placeholder')}</em>
          </div>
        ` : ''}
      </div>
      <div class="strength-option-source-list" aria-label="可选免费期权数据源">
        ${candidates.slice(0, 4).map((item) => `
          <a href="${escapeHtml(item.url || '#')}" target="_blank" rel="noreferrer">
            <strong>${escapeHtml(item.name)}</strong>
            <span>${escapeHtml(item.access || '')}</span>
          </a>
        `).join('')}
      </div>
      <p class="strength-source-note">${escapeHtml(options.message || '期权热度当前为中性占位，待接入真实期权流/IV历史。')}</p>
    </section>
  `;
}

function renderResultCard(row, index) {
  const tone = scoreTone(row.final_score);
  const changeTone = Number(row.change_pct) >= 0 ? 'up' : 'down';
  const warnings = Array.isArray(row.warnings) ? row.warnings : [];
  const reasons = Array.isArray(row.reasons) ? row.reasons : [];
  const tags = Array.isArray(row.tags) ? row.tags : [];
  const quality = Number(row.data_quality);
  const optionStatus = row.option_context?.source_status || 'placeholder';
  const optionHeat = Number(row.option_heat_score);
  const avgIv = Number(row.option_context?.iv_average);
  const optionProvider = row.option_context?.provider || '';
  const volumeTruth = row.volume_truth || row.vol_price_match || {};
  const effortResult = Number(volumeTruth.effort_result_ratio);
  const priceAction = row.price_action || row.breakdown?.price_action_detail || {};
  const paSupport = Number(priceAction.support);
  const paResistance = Number(priceAction.resistance);
  const metaChips = [
    Number.isFinite(quality) ? `数据覆盖 ${quality}%` : '',
    priceAction.structure_label && priceAction.structure !== 'range' ? `结构 ${priceAction.structure_label}` : '',
    Number.isFinite(paSupport) ? `支撑 ${formatMoney(paSupport)}` : '',
    Number.isFinite(paResistance) ? `阻力 ${formatMoney(paResistance)}` : '',
    volumeTruth.setup_label && volumeTruth.status === 'active' ? `量价 ${volumeTruth.setup_label}` : '',
    Number.isFinite(effortResult) ? `努力/结果 ${effortResult.toFixed(2)}` : '',
    optionStatus === 'active' && optionProvider ? `期权源 ${optionProvider}` : '',
    optionStatus === 'active' && Number.isFinite(optionHeat) ? `期权热度 ${formatScore(optionHeat)}` : '',
    optionStatus === 'active' && Number.isFinite(avgIv) ? `IV ${(avgIv * 100).toFixed(1)}%` : '',
    optionStatus === 'placeholder' ? '期权源 待接入' : '',
    row.fundamental_score != null ? `基本面 ${formatScore(row.fundamental_score)}` : '',
  ].filter(Boolean);
  return `
    <article class="strength-result-card strength-result-card--${tone}">
      <button type="button" class="strength-result-main" data-ticker="${escapeHtml(row.ticker)}" aria-label="打开 ${escapeHtml(row.ticker)} 详情">
        <span class="strength-rank mono font-data-mono" data-numeric>${String(index + 1).padStart(2, '0')}</span>
        <span class="strength-identity">
          <strong class="mono">${escapeHtml(row.ticker)}</strong>
          <small>${escapeHtml(row.name || row.sector_name || '')}</small>
        </span>
        <span class="strength-sector-chip">${escapeHtml(row.sector_name || '主题池')}</span>
        <span class="strength-price">
          <strong class="mono font-data-mono" data-numeric>${formatMoney(row.price)}</strong>
          <small class="mono ${changeTone}" data-numeric>${formatPercent(row.change_pct)}</small>
        </span>
        <span class="strength-final-score mono font-data-mono" data-numeric>${formatScore(row.final_score)}</span>
      </button>
      <div class="strength-result-detail">
        <div class="strength-classification">
          <span>${escapeHtml(row.classification || row.label || '观察')}</span>
          ${tags.slice(0, 5).map((tag) => `<em>${escapeHtml(tag)}</em>`).join('')}
        </div>
        <div class="strength-score-grid">
          ${renderScoreBar(row.score_short, '短')}
          ${renderScoreBar(row.score_mid, '中')}
          ${renderScoreBar(row.score_long, '长')}
          ${renderScoreBar(row.breakout_quality_score ?? row.breakdown?.breakout, '突破')}
          ${renderScoreBar(row.price_action_score ?? row.breakdown?.price_action, '结构')}
          ${renderScoreBar(row.sector_score, '板块')}
        </div>
        ${metaChips.length ? `<div class="strength-meta-row">${metaChips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join('')}</div>` : ''}
        <div class="strength-reasons">
          ${reasons.slice(0, 3).map((reason) => `<span>${escapeHtml(reason)}</span>`).join('')}
        </div>
        ${warnings.length ? `<div class="strength-warnings">${warnings.slice(0, 2).map((warning) => `<span>${escapeHtml(warning)}</span>`).join('')}</div>` : ''}
      </div>
    </article>
  `;
}

function renderResults(payload) {
  const rows = Array.isArray(payload?.rows) ? payload.rows : [];
  if (state.loading) {
    return '<section class="strength-results-panel"><div class="strength-loading">正在扫描主题股票池…</div></section>';
  }
  if (!rows.length) {
    return '<section class="strength-results-panel"><div class="detail-muted">暂无符合条件的结果。</div></section>';
  }
  return `
    <section class="strength-results-panel">
      <div class="section-card-heading">
        <span class="label-caps">候选榜单</span>
        <h2>${rows.length} 只标的</h2>
        ${renderDataNote(payload.data_sources)}
        ${renderCacheNote(payload)}
      </div>
      <div class="strength-result-list">
        ${rows.map(renderResultCard).join('')}
      </div>
    </section>
  `;
}

function renderControls() {
  return `
    <section class="strength-controls">
      <div class="strength-segment" data-control="timeframe" aria-label="周期">
        ${[
          ['all', '综合'],
          ['short', '短期'],
          ['mid', '中期'],
          ['long', '长期'],
        ].map(([value, label]) => `<button type="button" data-value="${value}" class="${state.timeframe === value ? 'active' : ''}">${label}</button>`).join('')}
      </div>
      <div class="strength-control-fields">
        <label>
          <span>风格</span>
          <select id="strength-profile">
            <option value="balanced" ${state.profile === 'balanced' ? 'selected' : ''}>均衡</option>
            <option value="conservative" ${state.profile === 'conservative' ? 'selected' : ''}>保守</option>
            <option value="aggressive" ${state.profile === 'aggressive' ? 'selected' : ''}>激进</option>
          </select>
        </label>
        <label>
          <span>数量</span>
          <select id="strength-top">
            <option value="20" ${state.top === 20 ? 'selected' : ''}>Top 20</option>
            <option value="30" ${state.top === 30 ? 'selected' : ''}>Top 30</option>
            <option value="50" ${state.top === 50 ? 'selected' : ''}>Top 50</option>
          </select>
        </label>
        <button id="strength-run" class="strength-run-button" type="button">
          <span class="material-symbols-outlined" aria-hidden="true">radar</span>
          <span>扫描</span>
        </button>
      </div>
    </section>
  `;
}

function renderShell() {
  const app = document.getElementById('app');
  if (!app) return;
  const payload = state.payload || {};
  app.innerHTML = `
    <section class="screener-page" aria-labelledby="screener-title">
      <header class="terminal-header">
        <div>
          <span class="label-caps">选股</span>
          <h1 id="screener-title">Strength Radar</h1>
          <p>主题池强势排名 · 趋势、相对强度、量能与风险联动</p>
        </div>
      </header>
      ${renderControls()}
      <div class="strength-layout">
        <div class="strength-primary">
          ${renderResults(payload)}
        </div>
        <aside class="strength-aside">
          ${renderMarketCard(payload.market_regime)}
          ${renderSpreadPanel(payload)}
          ${renderDataSourcePanel(payload.data_sources)}
          ${renderSectorRail(payload.sectors || [])}
        </aside>
      </div>
    </section>
  `;
  bindEvents();
}

function bindEvents() {
  document.querySelectorAll('[data-control="timeframe"] button').forEach((button) => {
    button.addEventListener('click', () => {
      state.timeframe = button.dataset.value || 'all';
      runScan();
    });
  });
  document.getElementById('strength-profile')?.addEventListener('change', (event) => {
    state.profile = event.target.value;
    runScan();
  });
  document.getElementById('strength-top')?.addEventListener('change', (event) => {
    state.top = Number(event.target.value) || 30;
    runScan();
  });
  document.getElementById('strength-run')?.addEventListener('click', () => runScan());
  document.querySelectorAll('.strength-sector-item').forEach((button) => {
    button.addEventListener('click', () => {
      state.sectorId = button.dataset.sectorId || '';
      runScan();
    });
  });
  document.querySelectorAll('.strength-result-main[data-ticker]').forEach((button) => {
    button.addEventListener('click', () => navigateToDetail(button.dataset.ticker));
  });
}

async function loadProfiles() {
  try {
    state.profiles = await api.strengthProfiles();
  } catch (_) {
    state.profiles = null;
  }
}

async function runScan() {
  state.loading = true;
  renderShell();
  try {
    state.payload = await api.strengthScan({
      timeframe: state.timeframe,
      profile: state.profile,
      top: state.top,
      sector_id: state.sectorId,
    });
  } catch (error) {
    state.payload = { rows: [], sectors: [], market_regime: {}, error: error.message };
  } finally {
    state.loading = false;
    renderShell();
  }
}

export async function renderScreener() {
  if (!state.profiles) await loadProfiles();
  renderShell();
  runScan();
}
