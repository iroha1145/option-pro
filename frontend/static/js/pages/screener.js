import { api } from '../api.js';
import { renderIcon } from '../icons.js';
import { renderMarketStrengthPlaceholder } from '../components/marketStrengthPlaceholder.js';

const SCREENER_STYLES_ID = 'optix-screener-v3-styles';

function ensureScreenerStyles() {
  if (document.getElementById(SCREENER_STYLES_ID)) return;
  const link = document.createElement('link');
  link.id = SCREENER_STYLES_ID;
  link.rel = 'stylesheet';
  link.href = new URL('../../css/optix-screener-v3.css', import.meta.url).href;
  document.head.append(link);
}

const state = {
  timeframe: 'all',
  profile: 'balanced',
  top: 20,
  sectorId: '',
  loading: false,
  payload: null,
  profiles: null,
  error: '',
  expandedTicker: '',
  mountGeneration: 0,
  requestGeneration: 0,
};

const FALLBACK_OPTION_SOURCES = [
  { name: 'MarketData.app', url: 'https://www.marketdata.app/docs/api/', access: '免费额度' },
  { name: 'Tradier', url: 'https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains.md', access: '开发者账户' },
  { name: 'tastytrade', url: 'https://tastytrade.com/api/', access: '经纪账户' },
  { name: 'Alpha Vantage', url: 'https://www.alphavantage.co/documentation/', access: '免费密钥' },
];

const TIMEFRAME_OPTIONS = [
  ['all', '综合'],
  ['short', '短期'],
  ['mid', '中期'],
  ['long', '长期'],
];

const PROFILE_OPTIONS = [
  ['balanced', '均衡'],
  ['conservative', '保守'],
  ['aggressive', '进取'],
];

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return url.protocol === 'https:' ? url.href : '';
  } catch (_) {
    return '';
  }
}

function isMounted(generation = state.mountGeneration) {
  return generation === state.mountGeneration
    && window.location.hash.replace(/^#/, '').split('/')[0] === 'screener'
    && Boolean(document.querySelector('.screening-page'));
}

function formatScore(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : '—';
}

function formatMoney(value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `$${number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPercent(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `${number > 0 ? '+' : ''}${number.toFixed(digits)}%`;
}

function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function scoreTone(score) {
  if (score === null || score === undefined || score === '') return 'neutral';
  const value = Number(score);
  if (!Number.isFinite(value)) return 'neutral';
  if (value >= 72) return 'strong';
  if (value >= 58) return 'watch';
  return 'weak';
}

function changeTone(value) {
  if (value === null || value === undefined || value === '') return 'neutral';
  const number = Number(value);
  if (!Number.isFinite(number)) return 'neutral';
  return number >= 0 ? 'positive' : 'negative';
}

function sourceStatusLabel(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'active') return '正常';
  if (normalized === 'degraded') return '降级';
  if (normalized === 'not_configured') return '未配置';
  if (normalized === 'placeholder') return '待接入';
  if (normalized === 'disabled') return '已关闭';
  if (normalized === 'skipped') return '本次跳过';
  return '状态未知';
}

function sourceStatusTone(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'active') return 'positive';
  if (normalized === 'degraded') return 'warning';
  return 'muted';
}

function currentFocusDescriptor() {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !active.closest('.screening-page')) return null;
  if (active.id) return { type: 'id', value: active.id };
  if (active.dataset.value && active.closest('[data-control="timeframe"]')) {
    return { type: 'timeframe', value: active.dataset.value };
  }
  if (active.dataset.sectorId !== undefined) return { type: 'sector', value: active.dataset.sectorId };
  if (active.dataset.resultToggle) return { type: 'toggle', value: active.dataset.resultToggle };
  return null;
}

function restoreFocus(descriptor) {
  if (!descriptor) return;
  let candidates = [];
  if (descriptor.type === 'id') candidates = [...document.querySelectorAll('[id]')].filter((item) => item.id === descriptor.value);
  if (descriptor.type === 'timeframe') candidates = [...document.querySelectorAll('[data-control="timeframe"] [data-value]')].filter((item) => item.dataset.value === descriptor.value);
  if (descriptor.type === 'sector') candidates = [...document.querySelectorAll('[data-sector-id]')].filter((item) => item.dataset.sectorId === descriptor.value);
  if (descriptor.type === 'toggle') candidates = [...document.querySelectorAll('[data-result-toggle]')].filter((item) => item.dataset.resultToggle === descriptor.value);
  candidates[0]?.focus({ preventScroll: true });
}

function renderStepHeading(step, eyebrow, title, description = '') {
  return `
    <div class="screening-step-heading">
      <span class="screening-step-number" aria-hidden="true">${escapeHtml(step)}</span>
      <span class="screening-step-copy">
        <small>${escapeHtml(eyebrow)}</small>
        <strong>${escapeHtml(title)}</strong>
        ${description ? `<em>${escapeHtml(description)}</em>` : ''}
      </span>
    </div>
  `;
}

function renderSectorPicker(sectors = []) {
  const sectorOptions = [{ id: '', name: '全部板块', avg_strength: null }, ...sectors];
  return `
    <div class="screening-sector-field">
      <span class="screening-field-label">板块范围</span>
      <div class="screening-sector-list" role="group" aria-label="板块范围" data-arrow-nav>
        ${sectorOptions.map((sector) => {
          const id = sector.id || sector.sector_id || '';
          const active = String(state.sectorId || '') === String(id || '');
          const count = Number(sector.count);
          return `
            <button type="button" class="screening-sector-button ${active ? 'is-active' : ''}" data-sector-id="${escapeHtml(id)}" aria-pressed="${active}">
              <span>${escapeHtml(sector.name || id || '全部板块')}</span>
              ${Number.isFinite(count) && count > 0 ? `<small>${Math.trunc(count)}</small>` : ''}
            </button>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function renderControls(sectors = []) {
  return `
    <section class="screening-section screening-controls" aria-labelledby="screening-filter-title" data-motion-reveal data-motion-key="screener-controls">
      <div class="screening-section-topline">
        ${renderStepHeading('01', '扫描设置', '设定候选范围', '更改条件后会自动重新扫描')}
        <button id="strength-run" class="screening-run-button" type="button" ${state.loading ? 'disabled' : ''}>
          ${renderIcon('radar', { size: 18 })}
          <span>${state.loading ? '扫描中' : '重新扫描'}</span>
        </button>
      </div>
      <div class="screening-control-grid">
        <div class="screening-control-field screening-control-field--timeframe">
          <span class="screening-field-label">观察周期</span>
          <div class="screening-segment" data-control="timeframe" role="group" aria-label="观察周期" data-arrow-nav>
            ${TIMEFRAME_OPTIONS.map(([value, label]) => `
              <button type="button" data-value="${value}" class="${state.timeframe === value ? 'is-active' : ''}" aria-pressed="${state.timeframe === value}">${label}</button>
            `).join('')}
          </div>
        </div>
        <label class="screening-control-field">
          <span class="screening-field-label">评分偏好</span>
          <select id="strength-profile">
            ${PROFILE_OPTIONS.map(([value, label]) => `<option value="${value}" ${state.profile === value ? 'selected' : ''}>${label}</option>`).join('')}
          </select>
        </label>
        <label class="screening-control-field">
          <span class="screening-field-label">候选数量</span>
          <select id="strength-top">
            <option value="20" ${state.top === 20 ? 'selected' : ''}>20 只</option>
            <option value="30" ${state.top === 30 ? 'selected' : ''}>30 只</option>
            <option value="50" ${state.top === 50 ? 'selected' : ''}>50 只</option>
          </select>
        </label>
      </div>
      ${renderSectorPicker(sectors)}
    </section>
  `;
}

function renderMetric(label, value, tone = 'neutral') {
  return `
    <span class="screening-market-metric screening-market-metric--${tone}">
      <small>${escapeHtml(label)}</small>
      <strong class="screening-data">${escapeHtml(value)}</strong>
    </span>
  `;
}

function renderDataSources(dataSources = {}) {
  const prices = dataSources.prices || { provider: 'Yahoo/yfinance', status: 'active' };
  const fundamentals = dataSources.fundamentals || {};
  const options = dataSources.options || {};
  const candidates = Array.isArray(options.candidates) && options.candidates.length
    ? options.candidates
    : FALLBACK_OPTION_SOURCES;
  const sources = [
    ['价格与成交量', prices.provider || 'Yahoo/yfinance', prices.status || 'active'],
    ['基本面', fundamentals.provider || 'Finnhub', fundamentals.status || 'not_configured'],
    ['期权热度', options.provider || 'Yahoo/yfinance / MarketData.app', options.status || 'placeholder'],
  ];
  return `
    <div class="screening-source-block">
      <h3>数据输入</h3>
      <div class="screening-source-list">
        ${sources.map(([label, provider, status]) => `
          <div class="screening-source-row">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(provider)}</strong>
            <em class="screening-source-status screening-source-status--${sourceStatusTone(status)}">${sourceStatusLabel(status)}</em>
          </div>
        `).join('')}
      </div>
      ${options.message ? `<p class="screening-source-note">${escapeHtml(options.message)}</p>` : ''}
      ${String(options.status || '').toLowerCase() !== 'active' ? `
        <div class="screening-source-links" aria-label="可选期权数据源">
          ${candidates.slice(0, 4).map((item) => {
            const href = safeExternalUrl(item.url);
            return href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.name)}<small>${escapeHtml(item.access || '')}</small></a>` : '';
          }).join('')}
        </div>
      ` : ''}
    </div>
  `;
}

function renderMarketBreakdown(payload = {}) {
  const market = payload.market_regime || {};
  const spreads = payload.spread_matrix || market.spread_matrix || {};
  const spreadOrder = ['qqq_spy', 'xlk_spy', 'soxx_xlk', 'iwm_spy', 'rsp_spy', 'xly_xlp', 'hyg_ief', 'spy_gld'];
  const spreadItems = spreadOrder.map((key) => spreads[key]).filter(Boolean);
  const warnings = Array.isArray(market.warnings) ? market.warnings : [];
  const dimensions = [
    ['指数趋势', market.index_trend_score],
    ['市场动量', market.market_momentum_score],
    ['市场宽度', market.market_breadth_score],
    ['成交量', market.market_volume_score],
    ['风险价差', market.risk_on_spread_score],
    ['风险偏好', market.risk_appetite_score],
  ];
  return `
    <div class="screening-market-detail-grid">
      <div class="screening-dimension-block">
        <h3>市场维度</h3>
        <div class="screening-dimension-list">
          ${dimensions.map(([label, value]) => `
            <span><small>${escapeHtml(label)}</small><strong class="screening-data">${formatScore(value, 0)}</strong></span>
          `).join('')}
        </div>
        ${warnings.length ? `<div class="screening-market-warnings">${warnings.slice(0, 3).map((warning) => `<p>${escapeHtml(warning)}</p>`).join('')}</div>` : ''}
      </div>
      ${spreadItems.length ? `
        <div class="screening-spread-block">
          <h3>风险价差</h3>
          <div class="screening-spread-list">
            ${spreadItems.slice(0, 6).map((item) => `
              <span>
                <small>${escapeHtml(item.name || item.key || '')}</small>
                <strong>${escapeHtml(item.label || '中性')}</strong>
                <em class="screening-data">${formatScore(item.score, 0)}</em>
              </span>
            `).join('')}
          </div>
        </div>
      ` : ''}
      ${renderDataSources(payload.data_sources)}
    </div>
  `;
}

function scanStatus(payload = {}) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  if (state.loading) {
    return {
      tone: 'loading',
      title: state.payload ? '正在更新候选' : '正在扫描股票池',
      description: state.payload ? '市场背景暂时保留上次结果' : '首次扫描通常约需 30 秒，正在汇总趋势、量价与风险环境',
    };
  }
  if (state.error) {
    return {
      tone: 'error',
      title: '扫描没有完成',
      description: state.error,
    };
  }
  if (!state.payload) {
    return { tone: 'idle', title: '等待扫描', description: '设定范围后生成候选列表' };
  }
  return {
    tone: rows.length ? 'ready' : 'idle',
    title: rows.length ? `已找到 ${rows.length} 只候选` : '本轮没有符合条件的标的',
    description: payload._cached || payload._client_cached ? '使用有效缓存，减少重复等待' : '结果已经更新',
  };
}

function renderMarketContext(payload = {}) {
  const market = payload.market_regime || {};
  const status = scanStatus(payload);
  const asOf = formatTimestamp(payload.as_of);
  const expiresAt = formatTimestamp(payload.cache_expires_at);
  const score = formatScore(market.score, 0);
  const marketLabel = market.label || (state.loading ? '计算中' : '暂无判断');
  const firstWarning = Array.isArray(market.warnings) ? market.warnings[0] : '';
  return `
    <section class="screening-section screening-context" aria-labelledby="screening-context-title" aria-busy="${state.loading}" data-motion-reveal data-motion-key="screener-context">
      ${renderStepHeading('02', '扫描状态', '理解市场背景', '先判断环境，再查看个股')}
      <div class="screening-context-strip">
        <div class="screening-status screening-status--${status.tone}" ${state.error ? 'role="alert"' : 'role="status"'}>
          <i aria-hidden="true"></i>
          <span>
            <strong id="screening-context-title">${escapeHtml(status.title)}</strong>
            <small>${escapeHtml(status.description)}</small>
          </span>
          ${state.error ? '<button id="strength-retry" type="button">重试扫描</button>' : ''}
        </div>
        <div class="screening-market-callout screening-market-callout--${scoreTone(market.score)}">
          <span>
            <small>市场判断</small>
            <strong>${escapeHtml(marketLabel)}</strong>
          </span>
          <em class="screening-data">${score}</em>
        </div>
        <div class="screening-market-snapshot" aria-label="主要市场指标">
          ${renderMetric('SPY 20日', formatPercent(market.spy_20d), changeTone(market.spy_20d))}
          ${renderMetric('QQQ 20日', formatPercent(market.qqq_20d), changeTone(market.qqq_20d))}
          ${renderMetric('波动率指数', market.vix == null ? '—' : formatScore(market.vix, 2))}
        </div>
      </div>
      ${firstWarning ? `<p class="screening-market-note"><span>注意</span>${escapeHtml(firstWarning)}</p>` : ''}
      <div class="screening-context-footer">
        <span>${asOf ? `数据时间 ${escapeHtml(asOf)}` : '等待数据时间'}</span>
        <span>${payload._cached || payload._client_cached ? '缓存结果' : '本轮结果'}${expiresAt ? ` · 有效至 ${escapeHtml(expiresAt)}` : ''}</span>
      </div>
      <details class="screening-context-details">
        <summary>查看市场分项与数据说明 ${renderIcon('chevron_right', { size: 17 })}</summary>
        ${renderMarketBreakdown(payload)}
      </details>
    </section>
  `;
}

function renderScoreBar(value, label) {
  const numeric = value === null || value === undefined || value === '' ? Number.NaN : Number(value);
  const score = Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0;
  const display = Number.isFinite(numeric) ? score.toFixed(0) : '—';
  return `
    <span class="screening-scorebar" role="progressbar" aria-label="${escapeHtml(label)}评分" aria-valuemin="0" aria-valuemax="100" ${Number.isFinite(numeric) ? `aria-valuenow="${score.toFixed(0)}"` : 'aria-valuetext="暂无数据"'}>
      <small>${escapeHtml(label)}</small>
      <i aria-hidden="true"><b style="width:${score}%"></b></i>
      <strong class="screening-data">${display}</strong>
    </span>
  `;
}

function renderResultCard(row, index) {
  const ticker = String(row.ticker || '').toUpperCase();
  const expanded = state.expandedTicker === ticker;
  const explanationId = `screening-explanation-${index}`;
  const toggleId = `screening-explanation-toggle-${index}`;
  const detailHref = `#detail/${encodeURIComponent(ticker)}`;
  const reasons = Array.isArray(row.reasons) ? row.reasons.filter(Boolean) : [];
  const warnings = Array.isArray(row.warnings) ? row.warnings.filter(Boolean) : [];
  const tags = Array.isArray(row.tags) ? row.tags.filter(Boolean) : [];
  const primaryReason = reasons[0] || '综合强度位于当前股票池前列';
  const detailReasons = reasons.length ? reasons : [primaryReason];
  const verdict = row.classification || row.label || '观察';
  const tone = scoreTone(row.final_score);
  const movement = changeTone(row.change_pct);
  const changeSymbol = movement === 'positive' ? '↑' : movement === 'negative' ? '↓' : '';
  const quality = row.data_quality == null ? Number.NaN : Number(row.data_quality);
  const priceAction = row.price_action || row.breakdown?.price_action_detail || {};
  const support = priceAction.support == null ? Number.NaN : Number(priceAction.support);
  const resistance = priceAction.resistance == null ? Number.NaN : Number(priceAction.resistance);
  const volumeTruth = row.volume_truth || row.vol_price_match || {};
  const optionStatus = row.option_context?.source_status || 'placeholder';
  const optionHeat = row.option_heat_score == null ? Number.NaN : Number(row.option_heat_score);
  const detailFacts = [
    Number.isFinite(quality) ? `数据覆盖 ${quality.toFixed(0)}%` : '',
    priceAction.structure_label ? `价格结构 ${priceAction.structure_label}` : '',
    Number.isFinite(support) ? `支撑 ${formatMoney(support)}` : '',
    Number.isFinite(resistance) ? `阻力 ${formatMoney(resistance)}` : '',
    volumeTruth.setup_label && volumeTruth.status === 'active' ? `量价 ${volumeTruth.setup_label}` : '',
    optionStatus === 'active' && Number.isFinite(optionHeat) ? `期权热度 ${formatScore(optionHeat)}` : '',
    optionStatus !== 'active' ? `期权信号 ${sourceStatusLabel(optionStatus)}` : '',
  ].filter(Boolean);
  const rank = String(index + 1).padStart(2, '0');
  const lead = index === 0;
  const leadDimensions = [
    ['短期', row.score_short],
    ['中期', row.score_mid],
    ['长期', row.score_long],
  ];
  return `
    <article class="screening-candidate screening-candidate--${tone} ${lead ? 'screening-candidate--lead' : ''} ${expanded ? 'is-expanded' : ''}" role="listitem"
      data-motion-card data-motion-key="screener-${escapeHtml(ticker)}" ${lead ? 'data-motion-lens data-motion-spotlight' : ''}>
      <div class="screening-candidate-summary">
        <a class="screening-candidate-link" href="${detailHref}" aria-label="打开 ${escapeHtml(ticker)} 研究页">
          <span class="screening-candidate-rank" aria-hidden="true">
            <b class="screening-data">${rank}</b>
            ${lead ? '<small>本轮首选</small>' : '<small>研究序列</small>'}
          </span>
          <span class="screening-candidate-main">
            <span class="screening-candidate-identity">
              <strong class="screening-data">${escapeHtml(ticker || '—')}</strong>
              <small>${escapeHtml(row.name || row.sector_name || '名称暂缺')}</small>
            </span>
            <span class="screening-candidate-price">
              <strong class="screening-data">${formatMoney(row.price)}</strong>
              <small class="screening-change screening-change--${movement}"><span aria-hidden="true">${changeSymbol}</span>${formatPercent(row.change_pct)}</small>
            </span>
            <span class="screening-primary-reason">${escapeHtml(primaryReason)}</span>
          </span>
          <span class="screening-candidate-evaluation">
            <span class="screening-candidate-score">
              <small>综合评分</small>
              <strong class="screening-data">${formatScore(row.final_score, 0)}</strong>
              <em>/ 100</em>
            </span>
            <span class="screening-verdict screening-verdict--${tone}"><i aria-hidden="true"></i>${escapeHtml(verdict)}</span>
            ${lead ? `
              <span class="screening-lead-dimensions" aria-label="首选标的核心维度">
                ${leadDimensions.map(([label, value]) => {
                  const numeric = Number(value);
                  const dimensionScore = Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0;
                  return `
                    <span>
                      <small>${escapeHtml(label)}</small>
                      <i aria-hidden="true"><b style="width:${dimensionScore}%"></b></i>
                      <strong class="screening-data">${formatScore(value, 0)}</strong>
                    </span>
                  `;
                }).join('')}
              </span>
            ` : ''}
          </span>
        </a>
        <button id="${toggleId}" type="button" class="screening-explain-button" data-result-toggle="${escapeHtml(ticker)}" aria-expanded="${expanded}" aria-controls="${explanationId}" aria-label="${expanded ? '收起' : '展开'} ${escapeHtml(ticker)} 入选理由">
          <span>${expanded ? '收起理由' : '查看理由'}</span>
          ${renderIcon('chevron_right', { size: 17 })}
        </button>
      </div>
      ${expanded ? `
        <div id="${explanationId}" class="screening-candidate-detail" role="region" aria-labelledby="${toggleId}">
          <div class="screening-explanation-copy">
            <div>
              <h3>入选依据</h3>
              <ul>${detailReasons.slice(0, 4).map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>
            </div>
            ${warnings.length ? `<div class="screening-warning-list"><h3>需要留意</h3><ul>${warnings.slice(0, 3).map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}</ul></div>` : ''}
          </div>
          <div class="screening-score-grid">
            ${renderScoreBar(row.score_short, '短期')}
            ${renderScoreBar(row.score_mid, '中期')}
            ${renderScoreBar(row.score_long, '长期')}
            ${renderScoreBar(row.breakout_quality_score ?? row.breakdown?.breakout, '突破')}
            ${renderScoreBar(row.price_action_score ?? row.breakdown?.price_action, '结构')}
            ${renderScoreBar(row.sector_score, '板块')}
          </div>
          ${detailFacts.length || tags.length ? `
            <div class="screening-detail-facts">
              ${tags.slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}
              ${detailFacts.map((fact) => `<span>${escapeHtml(fact)}</span>`).join('')}
            </div>
          ` : ''}
        </div>
      ` : ''}
    </article>
  `;
}

function renderResults(payload = {}) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  const timeframeLabel = Object.fromEntries(TIMEFRAME_OPTIONS)[state.timeframe] || '综合';
  const profileLabel = Object.fromEntries(PROFILE_OPTIONS)[state.profile] || '均衡';
  let body = '';
  if (state.loading) {
    body = `
      <div class="screening-result-skeleton" aria-hidden="true">
        ${Array.from({ length: 6 }, () => '<span><i></i><i></i><i></i><i></i></span>').join('')}
      </div>
    `;
  } else if (state.error) {
    body = '<div class="screening-result-state" role="status"><strong>候选列表暂不可用</strong><span>返回上方重试扫描，不会更改当前条件。</span></div>';
  } else if (!rows.length) {
    body = '<div class="screening-result-state" role="status"><strong>本轮没有候选</strong><span>放宽板块范围或切换评分偏好后会自动重扫。</span></div>';
  } else {
    body = rows.map(renderResultCard).join('');
  }
  const hasCandidates = !state.loading && !state.error && rows.length > 0;
  return `
    <section class="screening-section screening-results" aria-labelledby="screening-results-title" aria-busy="${state.loading}" data-motion-reveal data-motion-key="screener-results">
      <div class="screening-results-heading">
        ${renderStepHeading('03', '候选列表', '查看优先研究标的', '默认只显示最重要的判断与理由')}
        <span class="screening-result-context">
          <strong id="screening-results-title">${state.loading ? '生成中' : `${rows.length} 只候选`}</strong>
          <small>${escapeHtml(profileLabel)} · ${escapeHtml(timeframeLabel)}${state.sectorId ? ' · 已限板块' : ''}</small>
        </span>
      </div>
      <div class="screening-results-surface" data-result-state="${hasCandidates ? 'ready' : state.loading ? 'loading' : state.error ? 'error' : 'empty'}" ${hasCandidates ? 'role="list"' : ''}>
        ${body}
      </div>
    </section>
  `;
}

function renderShell() {
  const app = document.getElementById('app');
  if (!app) return;
  const focusDescriptor = currentFocusDescriptor();
  const payload = state.payload || {};
  const timeframeLabel = Object.fromEntries(TIMEFRAME_OPTIONS)[state.timeframe] || '综合';
  const profileLabel = Object.fromEntries(PROFILE_OPTIONS)[state.profile] || '均衡';
  const sectors = Array.isArray(payload.sectors) && payload.sectors.length
    ? payload.sectors
    : (state.profiles?.sectors || []);
  app.innerHTML = `
    <section class="screening-page" aria-labelledby="screener-title" data-motion-page="screener">
      <header class="screening-intro" data-motion-reveal data-motion-key="screener-intro">
        <div>
          <span class="screening-kicker">Optix Pro · 股票扫描</span>
          <h1 id="screener-title">从市场里，筛出值得研究的股票</h1>
          <p>先设条件，再看环境和候选；详细依据只在需要时展开。</p>
        </div>
        <div class="screening-plan" aria-label="当前扫描方案">
          <small>当前方案</small>
          <strong>${escapeHtml(profileLabel)}</strong>
          <span>${escapeHtml(timeframeLabel)} · 前 ${state.top} 只</span>
        </div>
      </header>
      <div class="screening-flow">
        <div class="screening-console" data-motion-key="screener-console" data-motion-lens>
          ${renderControls(sectors)}
          ${renderMarketContext(payload)}
          ${renderMarketStrengthPlaceholder({ variant: 'screener' })}
        </div>
        ${renderResults(payload)}
      </div>
    </section>
  `;
  bindEvents();
  if (focusDescriptor) requestAnimationFrame(() => restoreFocus(focusDescriptor));
}

function bindArrowNavigation(group) {
  const buttons = [...group.querySelectorAll('button:not(:disabled)')];
  group.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const current = event.target.closest('button');
    const index = buttons.indexOf(current);
    if (index < 0 || !buttons.length) return;
    event.preventDefault();
    let nextIndex = index;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + buttons.length) % buttons.length;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % buttons.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = buttons.length - 1;
    buttons[nextIndex]?.focus();
    buttons[nextIndex]?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
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
    state.top = Number(event.target.value) || 20;
    runScan();
  });
  document.getElementById('strength-run')?.addEventListener('click', () => runScan());
  document.getElementById('strength-retry')?.addEventListener('click', () => runScan());
  document.querySelectorAll('.screening-sector-button').forEach((button) => {
    button.addEventListener('click', () => {
      state.sectorId = button.dataset.sectorId || '';
      runScan();
    });
  });
  document.querySelector('.screening-results-surface')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-result-toggle]');
    if (!button) return;
    const ticker = button.dataset.resultToggle || '';
    state.expandedTicker = state.expandedTicker === ticker ? '' : ticker;
    renderShell();
  });
  document.querySelectorAll('[data-arrow-nav]').forEach(bindArrowNavigation);
}

async function loadProfiles() {
  try {
    state.profiles = await api.strengthProfiles();
  } catch (_) {
    state.profiles = null;
  }
}

async function runScan(mountGeneration = state.mountGeneration) {
  if (!isMounted(mountGeneration)) return;
  const requestGeneration = ++state.requestGeneration;
  state.loading = true;
  state.error = '';
  state.expandedTicker = '';
  renderShell();
  try {
    const payload = await api.strengthScan({
      timeframe: state.timeframe,
      profile: state.profile,
      top: state.top,
      sector_id: state.sectorId,
    });
    if (!isMounted(mountGeneration) || requestGeneration !== state.requestGeneration) return;
    state.payload = payload;
  } catch (error) {
    if (!isMounted(mountGeneration) || requestGeneration !== state.requestGeneration) return;
    state.payload = { rows: [], sectors: [], market_regime: {}, data_sources: {} };
    state.error = '选股服务暂时无法连接，请稍后重试。';
  } finally {
    if (!isMounted(mountGeneration) || requestGeneration !== state.requestGeneration) return;
    state.loading = false;
    renderShell();
  }
}

export function renderScreener() {
  ensureScreenerStyles();
  const mountGeneration = ++state.mountGeneration;
  state.requestGeneration += 1;
  state.loading = false;
  state.error = '';
  state.expandedTicker = '';
  renderShell();
  if (!state.profiles) loadProfiles();
  runScan(mountGeneration);
}
