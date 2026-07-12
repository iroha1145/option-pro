import { api } from '../api.js?v=20260712b';
import { renderIcon } from '../icons.js';

const BREAKOUT_STYLE_ID = 'optix-breakouts-v3-styles';
const PAGE_SIZE = 24;
const AUTO_REFRESH_MS = 60 * 1000;
const TICKER_PATTERN = /^[A-Z][A-Z0-9.-]{0,14}$/;

const SETUP_OPTIONS = [
  ['', '全部形态'],
  ['DAILY_BASE_BREAKOUT', '日线基底突破'],
  ['OPENING_RANGE_BREAKOUT', '开盘区间突破'],
  ['PREMARKET_GAP', '盘前跳空'],
  ['GAP_AND_GO', '跳空延续'],
  ['GAP_HOLD', '跳空守稳'],
  ['GAP_FADE', '跳空回落'],
  ['RETEST_BREAKOUT', '回踩再突破'],
  ['MOMENTUM_SPIKE', '动量异动'],
  ['RECOVERY_BREAKOUT', '修复突破'],
];

const LIFECYCLE_OPTIONS = [
  ['', '全部状态'],
  ['DISCOVERED', '已发现'],
  ['WATCHING', '观察中'],
  ['TRIGGERED', '已触发'],
  ['CONFIRMED', '已确认'],
  ['HOLDING', '保持中'],
  ['RETESTING', '回踩中'],
  ['RETEST_HELD', '回踩守住'],
  ['REACCELERATING', '重新加速'],
  ['EXTENDED', '过度延伸'],
  ['FAILED', '突破失败'],
  ['EXPIRED', '已过期'],
];

const SESSION_OPTIONS = [
  ['', '全部时段'],
  ['premarket', '盘前'],
  ['regular', '正常交易'],
  ['postmarket', '盘后'],
  ['closed', '休市'],
];

const SETUP_LABELS = Object.fromEntries(SETUP_OPTIONS);
const LIFECYCLE_LABELS = Object.fromEntries(LIFECYCLE_OPTIONS);
const SESSION_LABELS = Object.fromEntries(SESSION_OPTIONS);

const LIFECYCLE_TONES = {
  DISCOVERED: 'neutral',
  WATCHING: 'watching',
  TRIGGERED: 'triggered',
  CONFIRMED: 'confirmed',
  HOLDING: 'confirmed',
  RETESTING: 'watching',
  RETEST_HELD: 'confirmed',
  REACCELERATING: 'triggered',
  EXTENDED: 'warning',
  FAILED: 'negative',
  EXPIRED: 'neutral',
};

const SCORE_LABELS = {
  intrinsic_strength_score: '内在强度',
  base_quality_score: '基底质量',
  breakout_confirmation_score: '突破确认',
  liquidity_quality_score: '流动性',
  chase_risk_score: '追高风险',
  sector_fit_score: '板块适配',
  market_fit_score: '市场适配',
  breakout_quality_score: '突破质量',
  alert_priority_score: '告警优先级',
  data_confidence_score: '数据可信度',
  range_persistence: '区间强势持续度',
  discovery: '候选发现',
  daily_prices: '日线行情',
  intraday_prices: '盘中行情',
  strength: '内在强势',
  market_shape: '大盘形态',
};

let stylesheetPromise = null;
let activePageGeneration = 0;

const state = {
  filters: defaultFilters(),
  status: null,
  payload: null,
  events: [],
  nextCursor: null,
  pageMode: 'current',
  visibleCount: PAGE_SIZE,
  loading: false,
  refreshing: false,
  loadingMore: false,
  error: '',
  selectedEventId: '',
  selectedTicker: '',
  detail: null,
  tickerHistory: null,
  detailLoading: false,
  detailError: '',
  detailReturnFocusKey: '',
  requestGeneration: 0,
  detailGeneration: 0,
  controller: null,
  detailController: null,
  refreshTimer: null,
};

function defaultFilters() {
  return {
    date: '',
    ticker: '',
    setup_type: '',
    lifecycle_state: '',
    session: '',
    min_priority: '',
  };
}

function ensureBreakoutStylesheet() {
  const existing = document.getElementById(BREAKOUT_STYLE_ID);
  if (existing?.sheet) return Promise.resolve();
  if (stylesheetPromise) return stylesheetPromise;
  stylesheetPromise = new Promise((resolve, reject) => {
    const link = existing || document.createElement('link');
    link.id = BREAKOUT_STYLE_ID;
    link.rel = 'stylesheet';
    link.href = new URL('../../css/optix-breakouts-v3.css', import.meta.url).href;
    link.addEventListener('load', resolve, { once: true });
    link.addEventListener('error', () => {
      link.remove();
      reject(new Error('突破雷达样式未能载入'));
    }, { once: true });
    if (!existing) document.head.appendChild(link);
  }).catch((error) => {
    stylesheetPromise = null;
    throw error;
  });
  return stylesheetPromise;
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function finite(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function clampScore(value) {
  const number = finite(value);
  return number === null ? null : Math.max(0, Math.min(100, number));
}

function formatScore(value, digits = 0) {
  const number = finite(value);
  return number === null ? '—' : number.toFixed(digits);
}

function formatPrice(value) {
  const number = finite(value);
  if (number === null) return '—';
  return `$${number.toLocaleString('en-US', {
    minimumFractionDigits: number >= 1000 ? 0 : 2,
    maximumFractionDigits: number >= 1000 ? 2 : 2,
  })}`;
}

function formatPercent(value, { signed = true } = {}) {
  const number = finite(value);
  if (number === null) return '—';
  const sign = signed && number > 0 ? '+' : '';
  return `${sign}${number.toFixed(2)}%`;
}

function formatMultiple(value) {
  const number = finite(value);
  return number === null ? '—' : `${number.toFixed(2)}×`;
}

function formatTimestamp(value, { seconds = false } = {}) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: seconds ? '2-digit' : undefined,
    hour12: false,
  });
}

function formatMarketTimestamp(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.toLocaleString('zh-CN', {
    timeZone: 'America/New_York',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })} 美东`;
}

function formatAge(seconds) {
  const number = finite(seconds);
  if (number === null) return '时间未知';
  if (number < 60) return `${Math.max(0, Math.floor(number))} 秒前`;
  if (number < 3600) return `${Math.floor(number / 60)} 分钟前`;
  if (number < 86400) return `${Math.floor(number / 3600)} 小时前`;
  return `${Math.floor(number / 86400)} 天前`;
}

function changeTone(value) {
  const number = finite(value);
  if (number === null || number === 0) return 'neutral';
  return number > 0 ? 'positive' : 'negative';
}

function labelFor(map, value, fallback = '未分类') {
  return map[String(value || '')] || fallback;
}

function isValidTicker(value) {
  const ticker = String(value || '').trim().toUpperCase();
  return !ticker || (
    TICKER_PATTERN.test(ticker)
    && !ticker.endsWith('.')
    && !ticker.endsWith('-')
    && !ticker.includes('..')
    && !ticker.includes('--')
  );
}

function statusScanId(status) {
  return String(status?.latest_completed_scan?.scan_run_id || '') || null;
}

function payloadScanId(payload) {
  return String(payload?.scan_run_id || '') || null;
}

function snapshotsAligned(status, payload) {
  return statusScanId(status) === payloadScanId(payload);
}

function scoreTone(value, { risk = false } = {}) {
  const score = clampScore(value);
  if (score === null) return 'neutral';
  if (risk) {
    if (score >= 70) return 'negative';
    if (score >= 45) return 'warning';
    return 'positive';
  }
  if (score >= 80) return 'positive';
  if (score >= 60) return 'accent';
  if (score >= 40) return 'warning';
  return 'neutral';
}

function lifecycleTone(event) {
  return LIFECYCLE_TONES[String(event?.lifecycle_state || '')] || 'neutral';
}

function hasActiveFilters() {
  return Object.values(state.filters).some(Boolean);
}

function isMounted(generation = activePageGeneration) {
  return generation === activePageGeneration
    && window.location.hash.replace(/^#/, '').split('/')[0] === 'breakouts'
    && Boolean(document.querySelector('.breakout-page'));
}

function captureFocus() {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement)) return null;
  const key = active.dataset.focusKey || active.id;
  if (!key) return null;
  return {
    key,
    start: typeof active.selectionStart === 'number' ? active.selectionStart : null,
    end: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
  };
}

function restoreFocus(descriptor) {
  if (!descriptor) return;
  const target = document.querySelector(`[data-focus-key="${CSS.escape(descriptor.key)}"]`)
    || document.getElementById(descriptor.key);
  if (!(target instanceof HTMLElement)) return;
  target.focus({ preventScroll: true });
  if (
    descriptor.start !== null
    && descriptor.end !== null
    && typeof target.setSelectionRange === 'function'
  ) {
    target.setSelectionRange(descriptor.start, descriptor.end);
  }
}

function renderSelectOptions(options, selected) {
  return options.map(([value, label]) => (
    `<option value="${escapeHtml(value)}" ${value === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`
  )).join('');
}

function renderScoreBar(value, label, { risk = false } = {}) {
  const score = clampScore(value);
  const tone = scoreTone(score, { risk });
  return `
    <div class="breakout-score breakout-score--${tone}">
      <span><small>${escapeHtml(label)}</small><strong class="mono">${formatScore(score)}</strong></span>
      <i role="progressbar" aria-label="${escapeHtml(label)}" aria-valuemin="0" aria-valuemax="100" ${score === null ? 'aria-valuetext="暂无数据"' : `aria-valuenow="${score.toFixed(0)}"`}>
        <b style="width:${score === null ? 0 : score}%"></b>
      </i>
    </div>
  `;
}

function renderScoreOrbit(value) {
  const score = clampScore(value);
  return `
    <div class="breakout-score-orbit breakout-score-orbit--${scoreTone(score)}" style="--orbit-score:${score === null ? 0 : score}" aria-label="告警优先级 ${score === null ? '暂无数据' : `${score.toFixed(0)} 分`}">
      <span><strong class="mono">${formatScore(score)}</strong><small>告警优先级</small></span>
    </div>
  `;
}

function priceDistance(current, reference) {
  const price = finite(current);
  const base = finite(reference);
  if (price === null || base === null || base === 0) return null;
  return ((price / base) - 1) * 100;
}

function renderPriceLevel(label, value, current, tone = 'neutral') {
  const distance = label === '当前价' ? null : priceDistance(current, value);
  return `
    <li class="breakout-price-level breakout-price-level--${tone}">
      <span><small>${escapeHtml(label)}</small><strong class="mono">${formatPrice(value)}</strong></span>
      <em class="mono">${distance === null ? '' : `${formatPercent(distance)} 至现价`}</em>
    </li>
  `;
}

function signalSummary(event) {
  const items = [];
  const confirmation = finite(event.breakout_confirmation_score);
  const rvol = finite(event.rvol_time_of_day);
  const pivotDistance = priceDistance(event.current_price, event.pivot_price);
  const risk = finite(event.chase_risk_score);
  if (confirmation !== null) items.push(`确认强度 ${confirmation.toFixed(0)}`);
  if (rvol !== null) items.push(`同时段量能 ${rvol.toFixed(2)}×`);
  if (pivotDistance !== null) items.push(`距枢轴 ${formatPercent(pivotDistance)}`);
  if (risk !== null && risk >= 65) items.push('追高风险偏高');
  return items.length ? items.join(' · ') : '结构与评分仍在等待完整数据';
}

function systemState() {
  const status = state.status;
  const payload = state.payload;
  if (state.loading && !status && !payload) {
    return { tone: 'watching', title: '正在读取雷达状态', copy: '正在连接只读快照和工作进程状态。' };
  }
  if (state.error && !status && !payload) {
    return { tone: 'negative', title: '暂时无法连接雷达', copy: state.error };
  }
  if (status?.enabled === false || payload?.status === 'disabled') {
    return { tone: 'disabled', title: '突破雷达尚未启用', copy: '页面已接入，后台扫描开关仍处于关闭状态。' };
  }
  if (status?.database?.status === 'unavailable') {
    return { tone: 'negative', title: '雷达数据服务不可用', copy: '数据库无法读取，已有结果不会被伪装为空扫描。' };
  }
  if (status?.status === 'stale' || payload?.status === 'stale') {
    return { tone: 'warning', title: '正在使用陈旧快照', copy: '数据源暂时不稳定，页面保留最近一次完整结果。' };
  }
  if (status?.status === 'degraded' || payload?.status === 'degraded') {
    return {
      tone: 'warning',
      title: '部分数据源降级',
      copy: status?.latest_completed_scan
        ? '可用事件继续展示，缺失字段保持为空。'
        : '尚无可靠的完整快照，雷达不会用失败扫描填充结果。',
    };
  }
  if (status?.enabled && !status.latest_completed_scan) {
    return { tone: 'watching', title: '等待首次完整扫描', copy: '工作进程已经启用，完整快照发布后会自动出现。' };
  }
  if (status?.status === 'unavailable' || payload?.status === 'unavailable') {
    return { tone: 'watching', title: '尚无可用快照', copy: '后台尚未发布可读取的完整扫描。' };
  }
  return { tone: 'positive', title: '雷达运行中', copy: '显示后台已经完成并原子发布的扫描快照。' };
}

function providerSummary() {
  const providers = Array.isArray(state.status?.provider_health)
    ? state.status.provider_health
    : [];
  const provider = providers[0];
  if (!provider) return {
    label: '数据源',
    value: state.loading && !state.status
      ? '读取中'
      : !state.status && state.payload
        ? '状态未读取'
        : state.status?.enabled ? '等待记录' : '未启动',
    tone: 'neutral',
  };
  const labels = { active: '正常', stale: '陈旧', degraded: '降级', unavailable: '不可用' };
  return {
    label: provider.provider || '数据源',
    value: labels[provider.status] || provider.status || '未知',
    tone: provider.status === 'active' ? 'positive' : provider.status === 'stale' ? 'warning' : 'negative',
  };
}

function renderStatusStrip() {
  const system = systemState();
  const latest = state.status?.latest_completed_scan;
  const provider = providerSummary();
  const worker = state.status?.worker;
  const workerLabels = {
    idle: '待机', running: '扫描中', publishing: '发布中', degraded: '降级',
    lease_lost: '租约失效', stopped: '已停止',
  };
  const rangeMode = state.status?.range_persistence_mode;
  const rangeCopy = state.loading && !state.status
    ? '读取中'
    : !state.status && state.payload
      ? '状态未读取'
    : rangeMode === 'enabled'
    ? '已参与评分'
    : rangeMode === 'shadow'
      ? '影子观察，不参与排序'
      : '未计算';
  return `
    <section class="breakout-system breakout-system--${system.tone}" aria-label="突破雷达运行状态" data-motion-reveal data-motion-key="breakout-system">
      <div class="breakout-system__headline" role="status">
        <i aria-hidden="true"></i>
        <span><strong>${escapeHtml(system.title)}</strong><small>${escapeHtml(system.copy)}</small></span>
      </div>
      <div class="breakout-system__facts">
        <span><small>最近快照</small><strong>${latest?.published_at ? escapeHtml(formatTimestamp(latest.published_at)) : '—'}</strong></span>
        <span><small>扫描时段</small><strong>${escapeHtml(labelFor(SESSION_LABELS, latest?.session, '—'))}</strong></span>
        <span class="breakout-fact--${provider.tone}"><small>${escapeHtml(provider.label)}</small><strong>${escapeHtml(provider.value)}</strong></span>
        <span><small>工作进程</small><strong>${escapeHtml(workerLabels[worker?.status] || (state.loading && !state.status ? '读取中' : !state.status && state.payload ? '状态未读取' : state.status?.enabled ? '等待心跳' : '未启动'))}</strong></span>
        <span><small>区间持续度</small><strong>${escapeHtml(rangeCopy)}</strong></span>
      </div>
    </section>
  `;
}

function renderFilters() {
  const hasFilters = hasActiveFilters();
  return `
    <section class="breakout-filter-panel" aria-labelledby="breakout-filter-title" data-motion-reveal data-motion-key="breakout-filters">
      <div class="breakout-filter-panel__heading">
        <span><small>信号筛选</small><h2 id="breakout-filter-title">收窄当前完整快照</h2></span>
        ${hasFilters ? '<button type="button" class="breakout-filter-reset" data-filter-reset data-focus-key="breakout-filter-reset">清除条件</button>' : ''}
      </div>
      <form id="breakout-filter-form" class="breakout-filter-grid">
        <label class="breakout-filter breakout-filter--ticker">
          <span>股票代码</span>
          <input id="breakout-ticker-filter" data-focus-key="breakout-ticker-filter" name="ticker" type="search" value="${escapeHtml(state.filters.ticker)}" placeholder="例如 AAPL" maxlength="15" pattern="[A-Za-z](?:(?!.*(?:\\.\\.|--))[A-Za-z0-9.\\x2D]{0,13}[A-Za-z0-9])?" title="以字母开头，且不能以点或连字符结尾" autocomplete="off">
        </label>
        <label class="breakout-filter">
          <span>突破形态</span>
          <select data-focus-key="breakout-setup-filter" name="setup_type">${renderSelectOptions(SETUP_OPTIONS, state.filters.setup_type)}</select>
        </label>
        <label class="breakout-filter">
          <span>生命周期</span>
          <select data-focus-key="breakout-state-filter" name="lifecycle_state">${renderSelectOptions(LIFECYCLE_OPTIONS, state.filters.lifecycle_state)}</select>
        </label>
        <label class="breakout-filter">
          <span>最低优先级</span>
          <select data-focus-key="breakout-priority-filter" name="min_priority">
            ${renderSelectOptions([['', '不限'], ['50', '50 分以上'], ['65', '65 分以上'], ['80', '80 分以上'], ['90', '90 分以上']], state.filters.min_priority)}
          </select>
        </label>
        <label class="breakout-filter">
          <span>交易日期（美东）</span>
          <input data-focus-key="breakout-date-filter" name="date" type="date" value="${escapeHtml(state.filters.date)}">
        </label>
        <button class="breakout-filter-submit" data-focus-key="breakout-filter-submit" type="submit">应用筛选 ${renderIcon('arrow_up_right', { size: 17 })}</button>
      </form>
      <div class="breakout-session-filter" role="group" aria-label="交易时段" data-arrow-nav>
        ${SESSION_OPTIONS.map(([value, label]) => `
          <button type="button" data-session-filter="${escapeHtml(value)}" data-focus-key="breakout-session-${escapeHtml(value || 'all')}" aria-pressed="${state.filters.session === value}">${escapeHtml(label)}</button>
        `).join('')}
      </div>
    </section>
  `;
}

function renderLeadEvent(event) {
  const selected = state.selectedEventId === event.event_id;
  const change = changeTone(event.session_change_pct);
  const lifecycle = lifecycleTone(event);
  const warnings = Array.isArray(event.warnings) ? event.warnings.filter(Boolean) : [];
  const leadLabel = state.pageMode === 'events' ? '最新匹配' : '首要信号';
  return `
    <article class="breakout-lead breakout-lead--${lifecycle}" data-motion-reveal data-motion-lens data-motion-spotlight data-motion-key="breakout-lead-${escapeHtml(event.event_id)}">
      <div class="breakout-lead__identity">
        <span class="breakout-lead__eyebrow">${leadLabel} · ${escapeHtml(formatAge(event.event_age_seconds))}</span>
        <div class="breakout-lead__ticker">
          <strong class="mono">${escapeHtml(event.ticker || '—')}</strong>
          <span><b>${escapeHtml(event.name || '名称暂缺')}</b><small>${escapeHtml([event.exchange, event.sector].filter(Boolean).join(' · ') || '分类暂缺')}</small></span>
        </div>
        <div class="breakout-tag-row">
          <span class="breakout-tag breakout-tag--${lifecycle}">${escapeHtml(labelFor(LIFECYCLE_LABELS, event.lifecycle_state))}</span>
          <span class="breakout-tag">${escapeHtml(labelFor(SETUP_LABELS, event.setup_type))}</span>
          <span class="breakout-tag">${escapeHtml(labelFor(SESSION_LABELS, event.session))}</span>
        </div>
        <p>${escapeHtml(signalSummary(event))}</p>
        ${warnings.length ? `<div class="breakout-inline-warning">${renderIcon('info', { size: 16 })}<span>${escapeHtml(warnings[0])}</span></div>` : ''}
      </div>
      <div class="breakout-lead__market">
        <div class="breakout-lead__quote">
          <span><small>当前价</small><strong class="mono">${formatPrice(event.current_price ?? event.event_price)}</strong></span>
          <em class="mono breakout-change breakout-change--${change}">${formatPercent(event.session_change_pct)}</em>
        </div>
        <ol class="breakout-price-ladder" aria-label="关键价格位置">
          ${renderPriceLevel('当前价', event.current_price ?? event.event_price, event.current_price, 'current')}
          ${renderPriceLevel('突破枢轴', event.pivot_price, event.current_price, 'pivot')}
          ${renderPriceLevel('失效位置', event.invalidation_price, event.current_price, 'risk')}
        </ol>
        <div class="breakout-lead__microfacts">
          <span><small>跳空</small><strong class="mono">${formatPercent(event.gap_pct)}</strong></span>
          <span><small>同时段量能</small><strong class="mono">${formatMultiple(event.rvol_time_of_day)}</strong></span>
          <span><small>事件时间</small><strong>${escapeHtml(formatMarketTimestamp(event.event_at))}</strong></span>
        </div>
      </div>
      <div class="breakout-lead__score">
        ${renderScoreOrbit(event.alert_priority_score)}
        <div class="breakout-lead__scorebars">
          ${renderScoreBar(event.breakout_quality_score, '突破质量')}
          ${renderScoreBar(event.breakout_confirmation_score, '确认强度')}
          ${renderScoreBar(event.data_confidence_score, '数据可信度')}
          ${renderScoreBar(event.chase_risk_score, '追高风险', { risk: true })}
        </div>
        <div class="breakout-lead__actions">
          <button type="button" data-event-detail="${escapeHtml(event.event_id)}" data-event-ticker="${escapeHtml(event.ticker)}" data-focus-key="event-${escapeHtml(event.event_id)}" aria-expanded="${selected}" aria-controls="breakout-event-detail">${selected ? '收起完整证据' : '查看完整证据'} ${renderIcon('chevron_right', { size: 17 })}</button>
          <a href="#detail/${encodeURIComponent(event.ticker || '')}">打开股票研究页 ${renderIcon('arrow_up_right', { size: 16 })}</a>
        </div>
      </div>
    </article>
  `;
}

function renderEventCard(event, index) {
  const selected = state.selectedEventId === event.event_id;
  const lifecycle = lifecycleTone(event);
  const change = changeTone(event.session_change_pct);
  return `
    <article class="breakout-event-card breakout-event-card--${lifecycle} ${selected ? 'is-selected' : ''}" role="listitem" data-motion-card data-motion-key="breakout-event-${escapeHtml(event.event_id)}">
      <header>
        <span class="breakout-event-card__rank mono">${state.pageMode === 'events' ? '序' : ''}${String(index + 2).padStart(2, '0')}</span>
        <span class="breakout-event-card__identity"><strong class="mono">${escapeHtml(event.ticker || '—')}</strong><small>${escapeHtml(event.name || event.sector || '名称暂缺')}</small></span>
        <span class="breakout-tag breakout-tag--${lifecycle}">${escapeHtml(labelFor(LIFECYCLE_LABELS, event.lifecycle_state))}</span>
      </header>
      <div class="breakout-event-card__quote">
        <strong class="mono">${formatPrice(event.current_price ?? event.event_price)}</strong>
        <em class="mono breakout-change breakout-change--${change}">${formatPercent(event.session_change_pct)}</em>
      </div>
      <p>${escapeHtml(labelFor(SETUP_LABELS, event.setup_type))} · ${escapeHtml(formatAge(event.event_age_seconds))}</p>
      <dl class="breakout-event-card__levels">
        <div><dt>枢轴</dt><dd class="mono">${formatPrice(event.pivot_price)}</dd></div>
        <div><dt>失效位</dt><dd class="mono">${formatPrice(event.invalidation_price)}</dd></div>
        <div><dt>量能</dt><dd class="mono">${formatMultiple(event.rvol_time_of_day)}</dd></div>
      </dl>
      <div class="breakout-event-card__scores">
        ${renderScoreBar(event.alert_priority_score, '优先级')}
        ${renderScoreBar(event.breakout_quality_score, '突破质量')}
      </div>
      <footer>
        <button type="button" data-event-detail="${escapeHtml(event.event_id)}" data-event-ticker="${escapeHtml(event.ticker)}" data-focus-key="event-${escapeHtml(event.event_id)}" aria-expanded="${selected}" aria-controls="breakout-event-detail">${selected ? '收起证据' : '查看证据'} ${renderIcon('chevron_right', { size: 16 })}</button>
        <a href="#detail/${encodeURIComponent(event.ticker || '')}" aria-label="打开 ${escapeHtml(event.ticker || '')} 股票研究页">研究页 ${renderIcon('arrow_up_right', { size: 15 })}</a>
      </footer>
    </article>
  `;
}

function renderEmptyState() {
  const system = systemState();
  let title = '本轮没有突破事件';
  let copy = '扫描已完成，但没有标的同时满足当前发现、确认和数据质量条件。';
  let action = '';
  if (state.status?.enabled === false || state.payload?.status === 'disabled') {
    title = '突破雷达尚未启用';
    copy = '用户界面和数据契约已经接通；后台开关启用并发布完整快照后，这里会显示真实事件。';
  } else if (state.status?.database?.status === 'unavailable') {
    title = '雷达数据服务不可用';
    copy = '数据库暂时无法读取，页面不会把这种状态显示成空扫描。';
    action = '<button type="button" data-radar-retry data-focus-key="breakout-radar-retry">重新读取</button>';
  } else if (state.error) {
    title = '快照暂时无法读取';
    copy = state.error;
    action = '<button type="button" data-radar-retry data-focus-key="breakout-radar-retry">重新读取</button>';
  } else if (state.status?.status === 'degraded' && !state.status?.latest_completed_scan) {
    title = '数据源降级，尚无可靠快照';
    copy = '工作进程已保留失败记录，但不会发布不完整结果。';
  } else if (state.status?.enabled && !state.status?.latest_completed_scan) {
    title = '正在等待首次完整扫描';
    copy = '工作进程会先完成发现、日线复核、盘中确认和评分，再一次性发布结果。';
  } else if (hasActiveFilters()) {
    title = '当前条件没有匹配事件';
    copy = '清除部分条件可以回到当前完整快照；空结果不会被补成虚构分数。';
    action = '<button type="button" data-filter-reset data-focus-key="breakout-filter-reset-empty">清除筛选</button>';
  } else if (system.tone === 'warning') {
    title = '最近快照没有可展示事件';
    copy = '数据源处于降级或陈旧状态，雷达不会用不完整结果替代上次可靠判断。';
  }
  return `
    <div class="breakout-empty" role="status">
      <span class="breakout-empty__mark" aria-hidden="true">${renderIcon('radar', { size: 34 })}</span>
      <strong>${escapeHtml(title)}</strong>
      <p>${escapeHtml(copy)}</p>
      ${action}
    </div>
  `;
}

function renderReadWarning() {
  if (!state.error || !state.events.length) return '';
  return `
    <section class="breakout-read-warning" role="status" aria-label="快照读取提醒">
      ${renderIcon('info', { size: 18 })}
      <span><strong>已保留上一份完整快照</strong><small>${escapeHtml(state.error)}</small></span>
      <button type="button" data-radar-retry data-focus-key="breakout-radar-retry-inline">重新读取</button>
    </section>
  `;
}

function visibleEvents() {
  return state.pageMode === 'current'
    ? state.events.slice(0, state.visibleCount)
    : state.events;
}

function renderEventWorkspace() {
  if (state.loading && !state.payload) {
    return `
      <section class="breakout-loading" aria-label="正在载入突破快照" aria-busy="true">
        <span></span><span></span><span></span><span></span>
      </section>
    `;
  }
  const events = visibleEvents();
  if (!events.length) return renderEmptyState();
  const [lead, ...queue] = events;
  const loadedCount = events.length;
  const totalCurrent = state.pageMode === 'current' ? state.events.length : null;
  const canShowMore = state.pageMode === 'current'
    ? state.visibleCount < state.events.length
    : Boolean(state.nextCursor);
  return `
    <section class="breakout-results" aria-labelledby="breakout-results-title" aria-busy="${state.refreshing}">
      <div class="breakout-results__heading" data-motion-reveal data-motion-key="breakout-results-heading">
        <span><small>完整快照</small><h2 id="breakout-results-title">${state.pageMode === 'events' ? '匹配事件（按事件时间）' : '当前突破序列'}</h2></span>
        <span class="breakout-results__meta">
          <strong>${totalCurrent === null ? `已载入 ${loadedCount} 条` : `${totalCurrent} 条事件`}</strong>
          <small>${state.refreshing ? '正在读取新快照' : `更新时间 ${escapeHtml(formatTimestamp(state.payload?.as_of))}`}</small>
        </span>
      </div>
      ${renderLeadEvent(lead)}
      ${queue.length ? `<div class="breakout-event-grid" role="list">${queue.map(renderEventCard).join('')}</div>` : '<p class="breakout-single-note">当前快照只有这一条事件，完整证据仍可展开查看。</p>'}
      ${canShowMore ? `<button class="breakout-load-more" type="button" data-load-more data-focus-key="breakout-load-more" ${state.loadingMore ? 'disabled' : ''}>${state.loadingMore ? '正在载入…' : '显示更多事件'} ${renderIcon('chevron_down', { size: 17 })}</button>` : ''}
    </section>
  `;
}

function zoneLabel(zone) {
  if (!zone || typeof zone !== 'object') return '—';
  const low = finite(zone.low);
  const high = finite(zone.high);
  if (low === null && high === null) return '—';
  if (low !== null && high !== null && low !== high) return `${formatPrice(low)} – ${formatPrice(high)}`;
  return formatPrice(low ?? high);
}

function contributionLabel(key) {
  return SCORE_LABELS[key] || String(key || '').replaceAll('_', ' ');
}

function renderTransitions(transitions) {
  const rows = Array.isArray(transitions) ? transitions : [];
  if (!rows.length) return '<p class="breakout-detail__muted">尚无可展示的状态变化记录。</p>';
  return `
    <ol class="breakout-transition-list">
      ${rows.slice(-8).reverse().map((item) => {
        const from = labelFor(LIFECYCLE_LABELS, item.from_state, item.from_state || '起始');
        const to = labelFor(LIFECYCLE_LABELS, item.to_state, item.to_state || item.lifecycle_state || '更新');
        return `
          <li>
            <i aria-hidden="true"></i>
            <span><strong>${escapeHtml(from)} → ${escapeHtml(to)}</strong><small>${escapeHtml(item.reason || item.transition_reason || '状态更新')}</small></span>
            <time>${escapeHtml(formatMarketTimestamp(item.evidence_at || item.transitioned_at || item.event_at))}</time>
          </li>
        `;
      }).join('')}
    </ol>
  `;
}

function renderTickerHistory(history, currentId) {
  const events = Array.isArray(history?.events) ? history.events : [];
  if (!events.length) return '<p class="breakout-detail__muted">该股票暂无其他已发布事件。</p>';
  return `
    <div class="breakout-history-list">
      ${events.slice(0, 6).map((event) => `
        <button type="button" data-history-event="${escapeHtml(event.event_id)}" data-history-ticker="${escapeHtml(event.ticker)}" data-focus-key="history-${escapeHtml(event.event_id)}" ${event.event_id === currentId ? 'aria-current="true"' : ''}>
          <span><strong>${escapeHtml(labelFor(SETUP_LABELS, event.setup_type))}</strong><small>${escapeHtml(labelFor(LIFECYCLE_LABELS, event.lifecycle_state))}</small></span>
          <time>${escapeHtml(formatMarketTimestamp(event.event_at))}</time>
        </button>
      `).join('')}
    </div>
  `;
}

function renderDetailPanel() {
  if (!state.selectedEventId) return '';
  if (state.detailLoading) {
    return `
      <section id="breakout-event-detail" class="breakout-detail breakout-detail--loading" tabindex="-1" aria-label="正在载入事件证据" aria-busy="true">
        <span></span><span></span><span></span>
      </section>
    `;
  }
  if (state.detailError || !state.detail?.event) {
    return `
      <section id="breakout-event-detail" class="breakout-detail breakout-detail--error" tabindex="-1" role="alert">
        <button type="button" class="breakout-detail__close" data-detail-close aria-label="关闭事件详情">${renderIcon('close', { size: 20 })}</button>
        <strong>完整证据暂时无法读取</strong>
        <p>${escapeHtml(state.detailError || '事件详情不存在或已经不可用。')}</p>
        <button type="button" data-detail-retry data-focus-key="breakout-detail-retry">重试详情</button>
      </section>
    `;
  }
  const detail = state.detail;
  const event = detail.event;
  const structure = detail.structure || {};
  const contributions = Object.entries(event.contribution_breakdown || {})
    .filter(([, value]) => finite(value) !== null)
    .sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])));
  const weights = Object.entries(event.effective_weights || {})
    .filter(([, value]) => finite(value) !== null)
    .sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])));
  const warnings = Array.isArray(event.warnings) ? event.warnings.filter(Boolean) : [];
  const sourceStatus = Object.entries(event.source_status || {});
  const rangeShadow = state.status?.range_persistence_mode === 'shadow';
  return `
    <section id="breakout-event-detail" class="breakout-detail" tabindex="-1" aria-labelledby="breakout-detail-title" data-motion-reveal data-motion-key="breakout-detail-${escapeHtml(event.event_id)}">
      <header class="breakout-detail__header">
        <span><small>事件证据</small><h2 id="breakout-detail-title"><span class="mono">${escapeHtml(event.ticker)}</span> · ${escapeHtml(labelFor(SETUP_LABELS, event.setup_type))}</h2></span>
        <div>
          <a href="#detail/${encodeURIComponent(event.ticker || '')}">股票研究页 ${renderIcon('arrow_up_right', { size: 16 })}</a>
          <button type="button" class="breakout-detail__close" data-detail-close aria-label="关闭事件详情">${renderIcon('close', { size: 20 })}</button>
        </div>
      </header>
      <div class="breakout-detail__summary">
        <div class="breakout-detail__structure">
          <h3>价格结构</h3>
          <dl>
            <div><dt>突破枢轴</dt><dd class="mono">${formatPrice(event.pivot_price ?? structure.pivot_price)}</dd></div>
            <div><dt>支撑区域</dt><dd class="mono">${zoneLabel(event.support_zone || structure.support_zone)}</dd></div>
            <div><dt>阻力区域</dt><dd class="mono">${zoneLabel(event.resistance_zone || structure.resistance_zone)}</dd></div>
            <div><dt>失效位置</dt><dd class="mono">${formatPrice(event.invalidation_price ?? structure.invalidation_price)}</dd></div>
            <div><dt>基底周期</dt><dd>${structure.base_duration_days ? `${escapeHtml(structure.base_duration_days)} 天` : '—'}</dd></div>
            <div><dt>枢轴触碰</dt><dd>${escapeHtml(structure.pivot_touch_count ?? '—')}</dd></div>
          </dl>
        </div>
        <div class="breakout-detail__scoreboard">
          <h3>正式评分</h3>
          <div>
            ${renderScoreBar(event.intrinsic_strength_score, '内在强度')}
            ${renderScoreBar(event.base_quality_score, '基底质量')}
            ${renderScoreBar(event.breakout_confirmation_score, '突破确认')}
            ${renderScoreBar(event.liquidity_quality_score, '流动性')}
            ${renderScoreBar(event.breakout_quality_score, '突破质量')}
            ${renderScoreBar(event.data_confidence_score, '数据可信度')}
            ${renderScoreBar(event.chase_risk_score, '追高风险', { risk: true })}
            ${renderScoreBar(event.alert_priority_score, '告警优先级')}
          </div>
        </div>
        <aside class="breakout-detail__shadow">
          <span><small>研究指标</small><strong>${rangeShadow ? '影子观察' : state.status?.range_persistence_mode === 'enabled' ? '已参与评分' : '未启用'}</strong></span>
          <em class="mono">${formatScore(event.range_persistence)}</em>
          <p>区间强势持续度${rangeShadow ? '不参与当前排序，仅用于后续验证。' : state.status?.range_persistence_mode === 'enabled' ? '已经按服务端配置进入评分。' : '当前没有进入计算。'}</p>
          <dl>
            <div><dt>5日斜率</dt><dd class="mono">${formatScore(event.range_persistence_slope_5d, 2)}</dd></div>
            <div><dt>10日比例</dt><dd class="mono">${formatPercent(event.range_persistence_ratio_10d, { signed: false })}</dd></div>
          </dl>
        </aside>
      </div>
      <div class="breakout-detail__evidence-grid">
        <section>
          <h3>主要贡献</h3>
          ${contributions.length ? `
            <div class="breakout-contributions">
              ${contributions.slice(0, 8).map(([key, value]) => `<span><small>${escapeHtml(contributionLabel(key))}</small><strong class="mono">${formatScore(value, 2)}</strong></span>`).join('')}
            </div>
          ` : '<p class="breakout-detail__muted">本次没有公开贡献明细。</p>'}
          ${weights.length ? `<details><summary>查看有效权重</summary><div class="breakout-weight-list">${weights.map(([key, value]) => `<span><small>${escapeHtml(contributionLabel(key))}</small><strong class="mono">${formatPercent(Number(value) * 100, { signed: false })}</strong></span>`).join('')}</div></details>` : ''}
        </section>
        <section>
          <h3>状态变化</h3>
          ${renderTransitions(detail.transitions)}
        </section>
        <section>
          <h3>该股票近期事件</h3>
          ${renderTickerHistory(state.tickerHistory, event.event_id)}
        </section>
      </div>
      <div class="breakout-detail__audit">
        <section>
          <h3>数据来源</h3>
          <div class="breakout-source-list">
            ${sourceStatus.length ? sourceStatus.map(([key, value]) => `<span><small>${escapeHtml(contributionLabel(key))}</small><strong>${escapeHtml(value || '不可用')}</strong></span>`).join('') : '<span><small>来源</small><strong>暂无记录</strong></span>'}
          </div>
        </section>
        <section>
          <h3>风险与限制</h3>
          ${warnings.length ? `<ul>${warnings.slice(0, 8).map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}</ul>` : '<p class="breakout-detail__muted">本事件没有附加警告。</p>'}
          ${event.market_shape?.status !== 'active' ? '<p class="breakout-detail__notice">大盘形态适配当前不可用，因此市场适配分保持为空，不会补成中性值。</p>' : ''}
        </section>
      </div>
      <details class="breakout-detail__provenance">
        <summary>查看版本与计算截止时间 ${renderIcon('chevron_right', { size: 16 })}</summary>
        <dl>
          <div><dt>特征截止</dt><dd>${escapeHtml(formatTimestamp(event.provenance?.feature_cutoff_at, { seconds: true }))}</dd></div>
          <div><dt>计算截止</dt><dd>${escapeHtml(formatTimestamp(event.provenance?.calculation_cutoff_at, { seconds: true }))}</dd></div>
          <div><dt>来源快照</dt><dd class="mono">${escapeHtml(event.provenance?.source_snapshot_id || '—')}</dd></div>
          <div><dt>接口版本</dt><dd class="mono">${escapeHtml(event.versions?.api_schema || detail.versions?.api_schema || '—')}</dd></div>
          <div><dt>评分版本</dt><dd class="mono">${escapeHtml(event.versions?.scoring_version || detail.versions?.scoring_version || '—')}</dd></div>
        </dl>
      </details>
    </section>
  `;
}

function renderShell({ preserveFocus = true, fallbackFocus = null } = {}) {
  const app = document.getElementById('app');
  if (!app) return;
  const focus = preserveFocus ? captureFocus() || fallbackFocus : fallbackFocus;
  const latest = state.status?.latest_completed_scan;
  const rawEventCount = finite(latest?.event_count);
  const eventCount = rawEventCount === null ? null : Math.max(0, Math.trunc(rawEventCount));
  app.innerHTML = `
    <section class="breakout-page" aria-labelledby="breakout-title" data-motion-page="breakouts">
      <header class="breakout-intro" data-motion-reveal data-motion-key="breakout-intro">
        <div>
          <span class="breakout-kicker">Optix Pro · 全市场突破发现</span>
          <h1 id="breakout-title">突破雷达</h1>
          <p>追踪刚刚越过关键价位的股票，并把确认强度、生命周期与失效位置放在同一张研究画布上。</p>
        </div>
        <div class="breakout-intro__actions">
          <span><small>最近完整扫描</small><strong>${latest?.published_at ? escapeHtml(formatTimestamp(latest.published_at)) : '等待数据'}</strong><em>${eventCount === null || eventCount === undefined ? '事件数 —' : `${eventCount} 条事件`}</em></span>
          <button type="button" data-radar-refresh data-focus-key="breakout-radar-refresh" ${state.loading || state.refreshing ? 'disabled' : ''}>${renderIcon('refresh', { size: 18 })}<span>${state.refreshing ? '读取中' : '刷新快照'}</span></button>
        </div>
      </header>
      <div class="breakout-system-stack">
        ${renderStatusStrip()}
        ${renderReadWarning()}
      </div>
      ${renderFilters()}
      ${renderEventWorkspace()}
      ${renderDetailPanel()}
      <footer class="breakout-page__footnote">
        <span>后台工作进程负责扫描；页面每 60 秒读取一次完整快照。</span>
        <span>延迟行情，仅供研究，不构成投资建议。</span>
      </footer>
    </section>
  `;
  bindEvents();
  if (focus) requestAnimationFrame(() => restoreFocus(focus));
}

function readFilters(form) {
  const data = new FormData(form);
  const ticker = String(data.get('ticker') || '').trim().toUpperCase();
  const tickerInput = form.elements.namedItem('ticker');
  if (!isValidTicker(ticker)) {
    if (tickerInput instanceof HTMLInputElement) {
      tickerInput.setCustomValidity('股票代码需以字母开头，不能以点或连字符结尾，也不能连续输入两个点或连字符。');
      tickerInput.reportValidity();
    }
    return null;
  }
  if (tickerInput instanceof HTMLInputElement) tickerInput.setCustomValidity('');
  return {
    ...state.filters,
    ticker,
    setup_type: String(data.get('setup_type') || ''),
    lifecycle_state: String(data.get('lifecycle_state') || ''),
    min_priority: String(data.get('min_priority') || ''),
    date: String(data.get('date') || ''),
  };
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
  });
}

function bindEvents() {
  document.querySelector('[data-radar-refresh]')?.addEventListener('click', () => loadRadar({
    refresh: true,
    focusDescriptor: { key: 'breakout-radar-refresh', start: null, end: null },
  }));
  document.querySelectorAll('[data-radar-retry]').forEach((button) => {
    button.addEventListener('click', () => loadRadar({
      refresh: true,
      focusDescriptor: { key: 'breakout-radar-refresh', start: null, end: null },
    }));
  });
  document.querySelectorAll('[data-filter-reset]').forEach((button) => {
    button.addEventListener('click', () => {
      state.filters = defaultFilters();
      state.selectedEventId = '';
      state.selectedTicker = '';
      state.detail = null;
      state.tickerHistory = null;
      state.detailReturnFocusKey = '';
      loadRadar({
        refresh: true,
        focusDescriptor: { key: 'breakout-ticker-filter', start: 0, end: 0 },
      });
    });
  });
  document.getElementById('breakout-ticker-filter')?.addEventListener('input', (event) => {
    event.currentTarget.setCustomValidity('');
  });
  document.getElementById('breakout-filter-form')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const filters = readFilters(event.currentTarget);
    if (!filters) return;
    state.filters = filters;
    state.selectedEventId = '';
    state.selectedTicker = '';
    state.detailReturnFocusKey = '';
    loadRadar({
      refresh: true,
      focusDescriptor: { key: 'breakout-filter-submit', start: null, end: null },
    });
  });
  document.querySelectorAll('[data-session-filter]').forEach((button) => {
    button.addEventListener('click', () => {
      state.filters.session = button.dataset.sessionFilter || '';
      state.selectedEventId = '';
      state.selectedTicker = '';
      state.detailReturnFocusKey = '';
      loadRadar({
        refresh: true,
        focusDescriptor: {
          key: button.dataset.focusKey,
          start: null,
          end: null,
        },
      });
    });
  });
  document.querySelectorAll('[data-arrow-nav]').forEach(bindArrowNavigation);
  document.querySelectorAll('[data-event-detail]').forEach((button) => {
    button.addEventListener('click', () => toggleDetail(button.dataset.eventDetail, button.dataset.eventTicker));
  });
  document.querySelector('[data-detail-close]')?.addEventListener('click', closeDetail);
  document.querySelector('[data-detail-retry]')?.addEventListener('click', () => {
    if (state.selectedEventId && state.selectedTicker) {
      loadDetail(state.selectedEventId, state.selectedTicker, { refresh: true });
    }
  });
  document.querySelectorAll('[data-history-event]').forEach((button) => {
    button.addEventListener('click', () => loadDetail(button.dataset.historyEvent, button.dataset.historyTicker));
  });
  document.querySelector('[data-load-more]')?.addEventListener('click', loadMore);
}

function resetDataState() {
  state.status = null;
  state.payload = null;
  state.events = [];
  state.nextCursor = null;
  state.pageMode = 'current';
  state.visibleCount = PAGE_SIZE;
  state.loading = false;
  state.refreshing = false;
  state.loadingMore = false;
  state.error = '';
  state.selectedEventId = '';
  state.selectedTicker = '';
  state.detail = null;
  state.tickerHistory = null;
  state.detailLoading = false;
  state.detailError = '';
  state.detailReturnFocusKey = '';
  state.requestGeneration += 1;
  state.detailGeneration += 1;
  state.controller?.abort();
  state.detailController?.abort();
  state.controller = null;
  state.detailController = null;
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  state.refreshTimer = null;
}

function eventParams(cursor = '') {
  return {
    ...state.filters,
    limit: PAGE_SIZE,
    cursor,
  };
}

function applyRadarPayload(payload, filtered) {
  state.payload = payload;
  state.events = Array.isArray(payload?.events) ? payload.events : [];
  state.nextCursor = filtered ? payload?.next_cursor || null : null;
  state.pageMode = filtered ? 'events' : 'current';
}

function errorMessage(error) {
  const message = String(error?.message || '未知错误');
  if (message.includes('401')) return '访问凭证无效，请重新载入本页的会话凭证。';
  if (message.includes('429')) return '读取过于频繁，请等待一分钟后重试。';
  if (message.includes('503')) return '突破数据库暂时不可用，最近完整结果不会被覆盖。';
  if (message.includes('timeout')) return '读取快照超时，请稍后重试。';
  return '突破数据暂时无法连接，请稍后重试。';
}

async function loadRadar({
  append = false,
  refresh = false,
  alignmentRetry = false,
  focusDescriptor = null,
} = {}) {
  const pageGeneration = activePageGeneration;
  if (!isMounted(pageGeneration)) return;
  const requestFocus = focusDescriptor || captureFocus();
  if (append) {
    if (state.pageMode === 'current') {
      const firstNewEvent = state.events[state.visibleCount];
      state.visibleCount += PAGE_SIZE;
      renderShell({
        preserveFocus: false,
        fallbackFocus: firstNewEvent
          ? { key: `event-${firstNewEvent.event_id}`, start: null, end: null }
          : requestFocus,
      });
      return;
    }
    if (!state.nextCursor || state.loadingMore) return;
    const appendGeneration = ++state.requestGeneration;
    state.controller?.abort();
    state.controller = new AbortController();
    state.error = '';
    state.loadingMore = true;
    renderShell({ fallbackFocus: requestFocus });
    let appendedFocus = requestFocus;
    try {
      const page = await api.breakoutEvents(eventParams(state.nextCursor), {
        refresh,
        signal: state.controller.signal,
      });
      if (!isMounted(pageGeneration) || appendGeneration !== state.requestGeneration) return;
      if (payloadScanId(page) !== payloadScanId(state.payload)) {
        throw new Error('Breakout cursor returned a different completed scan');
      }
      const known = new Set(state.events.map((item) => item.event_id));
      const additions = (page.events || []).filter((item) => !known.has(item.event_id));
      state.events = [...state.events, ...additions];
      state.nextCursor = page.next_cursor || null;
      state.payload = { ...state.payload, ...page, events: state.events };
      if (additions[0]?.event_id) {
        appendedFocus = { key: `event-${additions[0].event_id}`, start: null, end: null };
      }
    } catch (error) {
      if (error?.name !== 'AbortError') {
        state.error = error?.message?.includes('different completed scan')
          ? '分页快照与当前结果不一致，已停止合并。请重新读取完整快照。'
          : errorMessage(error);
      }
    } finally {
      if (!isMounted(pageGeneration) || appendGeneration !== state.requestGeneration) return;
      state.loadingMore = false;
      renderShell({ preserveFocus: false, fallbackFocus: appendedFocus });
    }
    return;
  }

  const requestGeneration = ++state.requestGeneration;
  state.controller?.abort();
  state.controller = new AbortController();
  state.error = '';
  state.loading = !state.payload;
  state.refreshing = Boolean(state.payload);
  state.loadingMore = false;
  state.nextCursor = null;
  state.visibleCount = PAGE_SIZE;
  renderShell({ fallbackFocus: requestFocus });
  const options = { signal: state.controller.signal, refresh };
  const filtered = hasActiveFilters();
  const [statusResult, dataResult] = await Promise.allSettled([
    api.breakoutStatus(options),
    filtered ? api.breakoutEvents(eventParams(), options) : api.breakoutCurrent(options),
  ]);
  if (!isMounted(pageGeneration) || requestGeneration !== state.requestGeneration) return;

  const statusReady = statusResult.status === 'fulfilled';
  const dataReady = dataResult.status === 'fulfilled';
  if (statusReady && dataReady) {
    if (!snapshotsAligned(statusResult.value, dataResult.value)) {
      if (!alignmentRetry) {
        return loadRadar({
          refresh: true,
          alignmentRetry: true,
          focusDescriptor: requestFocus,
        });
      }
      state.error = '运行状态与事件列表来自不同扫描，页面已保留上一份完整快照。';
    } else {
      state.status = statusResult.value;
      applyRadarPayload(dataResult.value, filtered);
    }
  } else if (dataReady) {
    applyRadarPayload(dataResult.value, filtered);
    if (state.status && !snapshotsAligned(state.status, dataResult.value)) state.status = null;
    if (statusResult.reason?.name !== 'AbortError') {
      state.error = `运行状态未能更新。${errorMessage(statusResult.reason)}`;
    }
  } else {
    if (statusReady && (!state.payload || snapshotsAligned(statusResult.value, state.payload))) {
      state.status = statusResult.value;
    }
    const failure = dataResult.reason?.name !== 'AbortError'
      ? dataResult.reason
      : statusResult.status === 'rejected' && statusResult.reason?.name !== 'AbortError'
        ? statusResult.reason
        : null;
    if (failure) state.error = errorMessage(failure);
  }
  state.loading = false;
  state.refreshing = false;
  renderShell({ fallbackFocus: requestFocus });
}

function loadMore() {
  loadRadar({ append: true });
}

function toggleDetail(eventId, ticker) {
  if (!eventId) return;
  if (state.selectedEventId === eventId) {
    closeDetail();
    return;
  }
  state.detailReturnFocusKey = `event-${eventId}`;
  loadDetail(eventId, ticker);
}

async function loadDetail(eventId, ticker, { refresh = false } = {}) {
  const pageGeneration = activePageGeneration;
  const detailGeneration = ++state.detailGeneration;
  state.detailController?.abort();
  state.detailController = new AbortController();
  state.selectedEventId = String(eventId || '');
  state.selectedTicker = String(ticker || '').trim().toUpperCase();
  state.detail = null;
  state.tickerHistory = null;
  state.detailError = '';
  state.detailLoading = true;
  renderShell({ preserveFocus: false });
  requestAnimationFrame(() => document.getElementById('breakout-event-detail')?.scrollIntoView({
    block: 'start',
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
  }));
  const options = { signal: state.detailController.signal, refresh };
  const [detailResult, historyResult] = await Promise.allSettled([
    api.breakoutEvent(eventId, options),
    api.breakoutTicker(ticker, options),
  ]);
  if (!isMounted(pageGeneration) || detailGeneration !== state.detailGeneration) return;
  if (detailResult.status === 'fulfilled') state.detail = detailResult.value;
  else if (detailResult.reason?.name !== 'AbortError') state.detailError = errorMessage(detailResult.reason);
  if (historyResult.status === 'fulfilled') state.tickerHistory = historyResult.value;
  state.detailLoading = false;
  renderShell({ preserveFocus: false });
  requestAnimationFrame(() => document.getElementById('breakout-event-detail')?.focus({ preventScroll: true }));
}

function closeDetail() {
  const returnFocusKey = state.detailReturnFocusKey;
  state.detailGeneration += 1;
  state.detailController?.abort();
  state.detailController = null;
  state.selectedEventId = '';
  state.selectedTicker = '';
  state.detail = null;
  state.tickerHistory = null;
  state.detailLoading = false;
  state.detailError = '';
  state.detailReturnFocusKey = '';
  renderShell({ preserveFocus: false });
  requestAnimationFrame(() => {
    const target = returnFocusKey
      ? document.querySelector(`[data-focus-key="${CSS.escape(returnFocusKey)}"]`)
      : null;
    if (target instanceof HTMLElement) target.focus({ preventScroll: true });
    else document.getElementById('app')?.focus({ preventScroll: true });
  });
}

function startAutoRefresh(pageGeneration) {
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  state.refreshTimer = setInterval(() => {
    if (!isMounted(pageGeneration)) {
      clearInterval(state.refreshTimer);
      state.refreshTimer = null;
      return;
    }
    if (document.hidden || state.loading || state.refreshing || state.loadingMore) return;
    loadRadar({ refresh: true });
  }, AUTO_REFRESH_MS);
}

function cleanupPage(pageGeneration) {
  if (pageGeneration !== activePageGeneration) return;
  state.controller?.abort();
  state.detailController?.abort();
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  state.refreshTimer = null;
}

export async function renderBreakouts() {
  await ensureBreakoutStylesheet();
  if (window.location.hash.replace(/^#/, '').split('/')[0] !== 'breakouts') return;
  resetDataState();
  state.filters = defaultFilters();
  const pageGeneration = ++activePageGeneration;
  renderShell({ preserveFocus: false });
  window.addEventListener('hashchange', () => cleanupPage(pageGeneration), { once: true });
  startAutoRefresh(pageGeneration);
  await loadRadar();
}
