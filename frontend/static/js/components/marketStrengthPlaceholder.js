const MAJOR_INDEX_NAMES = Object.freeze({
  '^GSPC': 'S&P 500',
  '^IXIC': '纳斯达克综合指数',
  '^DJI': '道琼斯工业平均指数',
  '^N225': '日经225',
  '000001.SS': '上证综合指数',
});

const MAJOR_INDEX_BADGES = Object.freeze({
  '^GSPC': 'SPX',
  '^IXIC': 'IXIC',
  '^DJI': 'DJI',
  '^N225': 'N225',
  '000001.SS': 'SSE',
});

const ADDITIONAL_MAJOR_INDICES = new Set([
  '^NDX',
  '^RUT',
  '^VIX',
  '^HSI',
  '^FTSE',
  '^GDAXI',
  '^STOXX50E',
]);

const US_MARKET_RESEARCH_INDICES = new Set([
  '^GSPC',
  '^IXIC',
  '^DJI',
  '^NDX',
  '^RUT',
  '^VIX',
]);

const DIMENSIONS = Object.freeze([
  ['指数趋势', 'index_trend_score'],
  ['市场动量', 'market_momentum_score'],
  ['市场广度', 'market_breadth_score'],
  ['量价参与', 'market_volume_score'],
  ['风险偏好', 'risk_appetite_score'],
  ['风险开关', 'risk_on_spread_score'],
]);

const RULE_LABELS = Object.freeze({
  breakout_weight_multiplier: '突破权重',
  option_heat_weight_multiplier: '期权热度权重',
  sector_strength_weight_multiplier: '板块强度权重',
  market_fit_multiplier: '市场适配系数',
  allow_new_entries: '允许新增观察',
  position_size_multiplier: '仓位参考系数',
  ordinary_breakout_fit: '普通突破适配',
  recovery_breakout_fit: '修复突破适配',
  preferred_setups: '偏好形态',
  caution_setups: '谨慎形态',
  confirmation_bar_delta: '额外确认K线',
  allow_single_bar_confirmation: '允许单根K线确认',
  eligibility: '市场准入',
  state_label: '形态名称',
});

const SETUP_LABELS = Object.freeze({
  DAILY_BASE_BREAKOUT: '日线平台突破',
  OPENING_RANGE_BREAKOUT: '开盘区间突破',
  RETEST_BREAKOUT: '回踩再突破',
  RECOVERY_BREAKOUT: '收复型突破',
  MOMENTUM_SPIKE: '动量急升',
  GAP_AND_GO: '跳空延续',
  PREMARKET_GAP: '盘前跳空',
});

const TECHNICAL_BREAKDOWN_LABELS = Object.freeze({
  price_overheated: '价格过热',
  breadth_divergence: '广度背离',
  options_sentiment: '波动情绪',
  volatility_turning: '波动转向',
  rates_pressure: '利率压力',
  credit_risk: '信用风险',
  positioning: '仓位拥挤',
  panic_release: '恐慌释放',
  technical_reclaim: '技术修复',
  breadth_repair: '广度修复',
  volatility_falling: '波动回落',
  credit_stable: '信用企稳',
  rates_easing: '利率缓和',
  sentiment_pessimism: '悲观情绪',
});

function normalizeTicker(ticker) {
  return String(ticker || '').trim().toUpperCase();
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

function finiteScore(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : null;
}

function formatScore(value, digits = 0) {
  const score = finiteScore(value);
  return score === null ? '—' : score.toFixed(digits);
}

function formatRatio(value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  const percent = Math.abs(number) <= 1 ? number * 100 : number;
  return `${Math.max(0, Math.min(100, percent)).toFixed(0)}%`;
}

function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function scoreTone(value) {
  const score = finiteScore(value);
  if (score === null) return 'muted';
  if (score >= 75) return 'strong';
  if (score >= 60) return 'positive';
  if (score >= 40) return 'neutral';
  return 'risk';
}

function statusText(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'active' || normalized === 'ready') return '数据正常';
  if (normalized === 'insufficient_data') return '数据不足';
  if (normalized === 'not_used') return '本轮未使用';
  if (normalized === 'unavailable' || normalized === 'not_available') return '暂不可用';
  if (normalized === 'degraded') return '数据降级';
  return status ? String(status) : '状态待确认';
}

function warningText(warning) {
  if (warning === null || warning === undefined) return '';
  if (typeof warning === 'object') {
    return String(warning.message || warning.detail || warning.code || '').trim();
  }
  return String(warning).trim();
}

function normalizedWarnings(...collections) {
  const warnings = collections
    .flatMap((collection) => Array.isArray(collection) ? collection : [])
    .map(warningText)
    .filter(Boolean);
  return [...new Set(warnings)];
}

function formatRuleValue(key, value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? '启用' : '未启用';
  if (key === 'eligibility') {
    return ({ normal: '常规', selective: '择优', caution: '谨慎', restricted: '受限', recovery_only: '仅修复型' })[value] || String(value);
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return key.includes('multiplier') ? `${value.toFixed(2)}×` : String(value);
  }
  if (Array.isArray(value)) return value.map((item) => SETUP_LABELS[item] || String(item)).join('、') || '—';
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch (_) {
      return '规则已生效';
    }
  }
  return String(value);
}

function normalizeRules(rules) {
  if (Array.isArray(rules)) {
    return rules.map((value, index) => [`规则 ${index + 1}`, formatRuleValue(`rule_${index + 1}`, value)]);
  }
  if (!rules || typeof rules !== 'object') return [];
  return Object.entries(rules).map(([key, value]) => [RULE_LABELS[key] || key.replaceAll('_', ' '), formatRuleValue(key, value)]);
}

function renderDimension(label, value) {
  const score = finiteScore(value);
  return `
    <div class="market-strength-panel__dimension market-strength-panel__dimension--${scoreTone(value)}"
      role="progressbar" aria-label="${escapeHtml(label)}" aria-valuemin="0" aria-valuemax="100"
      ${score === null ? 'aria-valuetext="暂无数据"' : `aria-valuenow="${score.toFixed(0)}"`}>
      <span><small>${escapeHtml(label)}</small><strong>${formatScore(value)}</strong></span>
      <i aria-hidden="true"><b style="--market-strength-score:${score === null ? 0 : score}%"></b></i>
    </div>
  `;
}

function renderWarnings(warnings) {
  if (!warnings.length) return '';
  return `
    <div class="market-strength-panel__warnings" role="note" aria-label="数据提醒">
      <strong>数据提醒</strong>
      <ul>${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}</ul>
    </div>
  `;
}

function renderShape(shape = {}) {
  const rules = normalizeRules(shape.rules);
  const warnings = normalizedWarnings(shape.warnings);
  const shapeStatus = String(shape.status || '').toLowerCase();
  const shapeUsable = shapeStatus === 'active' || shapeStatus === 'degraded';
  const shapeClass = shapeStatus === 'active'
    ? 'is-active'
    : shapeStatus === 'degraded'
      ? 'is-degraded'
      : 'is-unavailable';
  const stateLabel = shape.state_label || shape.state || (shapeUsable ? '形态待确认' : '形态暂不可用');
  const asOf = formatTimestamp(shape.as_of);
  return `
    <section class="market-strength-panel__shape ${shapeClass}" aria-labelledby="market-shape-title">
      <div class="market-strength-panel__shape-lead">
        <span>
          <small id="market-shape-title">市场形态</small>
          <strong>${escapeHtml(stateLabel)}</strong>
        </span>
        <em>${escapeHtml(statusText(shape.status))}</em>
      </div>
      <dl class="market-strength-panel__shape-metrics">
        <div><dt>置信度</dt><dd>${escapeHtml(formatRatio(shapeUsable ? shape.confidence : null))}</dd></div>
        <div><dt>转向风险</dt><dd>${escapeHtml(formatRatio(shapeUsable ? shape.transition_risk : null))}</dd></div>
      </dl>
      ${(rules.length || warnings.length || shape.version || asOf) ? `
        <details class="market-strength-panel__shape-details">
          <summary>形态依据与版本</summary>
          ${rules.length ? `<dl>${rules.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>` : '<p>本次没有返回可展示的形态规则。</p>'}
          ${warnings.length ? `<ul>${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join('')}</ul>` : ''}
          <p>${shape.state ? `状态 ${escapeHtml(shape.state)} · ` : ''}${asOf ? `形态时间 ${escapeHtml(asOf)}` : '形态时间待确认'}${shape.version ? ` · 版本 ${escapeHtml(shape.version)}` : ''}</p>
        </details>
      ` : ''}
    </section>
  `;
}

function normalizeReasons(reasons) {
  if (Array.isArray(reasons)) return reasons.map(warningText).filter(Boolean);
  if (!reasons || typeof reasons !== 'object') return [];
  const raising = Array.isArray(reasons.raising)
    ? reasons.raising.map(warningText).filter(Boolean).map((reason) => `抬升：${reason}`)
    : [];
  const suppressing = Array.isArray(reasons.suppressing)
    ? reasons.suppressing.map(warningText).filter(Boolean).map((reason) => `抑制：${reason}`)
    : [];
  return [...raising, ...suppressing];
}

function strongestBreakdown(breakdown) {
  if (!breakdown || typeof breakdown !== 'object') return [];
  return Object.entries(breakdown)
    .map(([key, value]) => [key, finiteScore(value)])
    .filter(([, value]) => value !== null)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3);
}

function renderTechnicalScoreCard({ title, score, label, breakdown, reasons, tone }) {
  const evidence = normalizeReasons(reasons).slice(0, 4);
  const strongest = strongestBreakdown(breakdown);
  return `
    <article class="market-strength-panel__technical-card market-strength-panel__technical-card--${tone}">
      <header>
        <span><small>${escapeHtml(title)}</small><strong>${formatScore(score)}</strong></span>
        <em>${escapeHtml(label || '判断待确认')}</em>
      </header>
      ${strongest.length ? `
        <dl>${strongest.map(([key, value]) => `
          <div>
            <dt>${escapeHtml(TECHNICAL_BREAKDOWN_LABELS[key] || key.replaceAll('_', ' '))}</dt>
            <dd>${formatScore(value)}</dd>
          </div>
        `).join('')}</dl>
      ` : ''}
      ${evidence.length ? `
        <ul aria-label="${escapeHtml(title)}主要依据">${evidence.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>
      ` : '<p>本次没有返回可展示的主要依据。</p>'}
    </article>
  `;
}

function renderTechnicalSignals({ payload, loading, error, retryable }) {
  if (loading) {
    return `
      <section class="market-strength-panel__technical" aria-labelledby="market-technical-title" aria-busy="true">
        <div class="market-strength-panel__section-heading">
          <span><small>广度动量</small><strong id="market-technical-title">全市场见顶风险与见底修复</strong></span>
        </div>
        <div class="market-strength-panel__message market-strength-panel__message--loading" role="status">
          <i aria-hidden="true"></i>
          <span><strong>正在读取技术判断</strong><small>两套数据独立加载，不影响市场环境分项</small></span>
        </div>
      </section>
    `;
  }
  if (error) {
    return `
      <section class="market-strength-panel__technical" aria-labelledby="market-technical-title">
        <div class="market-strength-panel__section-heading">
          <span><small>广度动量</small><strong id="market-technical-title">全市场见顶风险与见底修复</strong></span>
        </div>
        <div class="market-strength-panel__message market-strength-panel__message--error" role="alert">
          <span><strong>技术判断暂时无法读取</strong><small>${escapeHtml(error)}</small></span>
          ${retryable ? '<button type="button" data-market-signals-retry>重试</button>' : ''}
        </div>
      </section>
    `;
  }

  const scores = payload?.scores && typeof payload.scores === 'object' ? payload.scores : {};
  const topScore = finiteScore(scores.top_score);
  const bottomScore = finiteScore(scores.bottom_score);
  const quality = finiteScore(scores.data_quality);
  const asOf = formatTimestamp(payload?.as_of);
  if (topScore === null && bottomScore === null) {
    return `
      <section class="market-strength-panel__technical" aria-labelledby="market-technical-title">
        <div class="market-strength-panel__section-heading">
          <span><small>广度动量</small><strong id="market-technical-title">全市场见顶风险与见底修复</strong></span>
          <em>数据不足</em>
        </div>
        <div class="market-strength-panel__message market-strength-panel__message--insufficient" role="status">
          <span><strong>暂不生成技术反转判断</strong><small>有效指标不足，页面不会用默认分数填补。</small></span>
        </div>
      </section>
    `;
  }

  return `
    <section class="market-strength-panel__technical" aria-labelledby="market-technical-title">
      <div class="market-strength-panel__section-heading">
        <span><small>广度动量</small><strong id="market-technical-title">全市场见顶风险与见底修复</strong></span>
        <em>数据质量 ${formatScore(quality)}%</em>
      </div>
      <div class="market-strength-panel__technical-grid">
        ${renderTechnicalScoreCard({
          title: '技术见顶风险',
          score: scores.top_score,
          label: scores.top_label,
          breakdown: scores.top_breakdown,
          reasons: scores.top_reasons,
          tone: 'top',
        })}
        ${renderTechnicalScoreCard({
          title: '技术见底修复',
          score: scores.bottom_score,
          label: scores.bottom_label,
          breakdown: scores.bottom_breakdown,
          reasons: scores.bottom_reasons,
          tone: 'bottom',
        })}
      </div>
      <p class="market-strength-panel__technical-meta">${asOf ? `技术判断时间 ${escapeHtml(asOf)}` : '技术判断时间待确认'} · 分数表示条件聚合强度，不代表反转已经发生。</p>
    </section>
  `;
}

function technicalSignalLabel(value) {
  const labels = {
    bullish: '偏强',
    bearish: '偏弱',
    neutral: '中性',
    overbought: '偏热',
    oversold: '偏冷',
    above: '价格在上方',
    below: '价格在下方',
    spike: '显著放量',
    high: '温和放量',
    normal: '常态',
  };
  return labels[String(value || '').toLowerCase()] || String(value || '待确认');
}

function technicalSignalValue(key, value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (key === 'volume') return `${number.toFixed(2)}×`;
  if (key === 'macd') return number.toFixed(4);
  return number.toFixed(key === 'rsi' ? 1 : 2);
}

function renderIndexTechnical({ payload, loading, error, retryable, ticker }) {
  const title = getMajorIndexDisplayName(ticker) || normalizeTicker(ticker) || '当前指数';
  if (loading) {
    return `
      <section class="market-strength-panel__index-technical" aria-labelledby="index-technical-title" aria-busy="true">
        <div class="market-strength-panel__section-heading">
          <span><small>趋势结构</small><strong id="index-technical-title">${escapeHtml(title)}自身技术面</strong></span>
        </div>
        <div class="market-strength-panel__message market-strength-panel__message--loading" role="status">
          <i aria-hidden="true"></i>
          <span><strong>正在读取指数技术面</strong><small>计算相对强弱指标、均线、动量与成交量</small></span>
        </div>
      </section>
    `;
  }
  if (error) {
    return `
      <section class="market-strength-panel__index-technical" aria-labelledby="index-technical-title">
        <div class="market-strength-panel__section-heading">
          <span><small>趋势结构</small><strong id="index-technical-title">${escapeHtml(title)}自身技术面</strong></span>
        </div>
        <div class="market-strength-panel__message market-strength-panel__message--error" role="alert">
          <span><strong>指数技术面暂时无法读取</strong><small>${escapeHtml(error)}</small></span>
          ${retryable ? '<button type="button" data-index-signals-retry>重试</button>' : ''}
        </div>
      </section>
    `;
  }

  const score = finiteScore(payload?.score);
  const signals = payload?.signals && typeof payload.signals === 'object' ? payload.signals : {};
  const entries = ['rsi', 'macd', 'ema20', 'sma50', 'volume']
    .map((key) => [key, signals[key]])
    .filter(([, item]) => item && typeof item === 'object');
  if (score === null && !entries.length) {
    return `
      <section class="market-strength-panel__index-technical" aria-labelledby="index-technical-title">
        <div class="market-strength-panel__section-heading">
          <span><small>趋势结构</small><strong id="index-technical-title">${escapeHtml(title)}自身技术面</strong></span>
          <em>数据不足</em>
        </div>
        <div class="market-strength-panel__message market-strength-panel__message--insufficient" role="status">
          <span><strong>暂不生成指数技术判断</strong><small>有效价格历史不足，缺失值保持为空。</small></span>
        </div>
      </section>
    `;
  }

  return `
    <section class="market-strength-panel__index-technical" aria-labelledby="index-technical-title">
      <div class="market-strength-panel__section-heading">
        <span><small>趋势结构</small><strong id="index-technical-title">${escapeHtml(title)}自身技术面</strong></span>
        <em>${escapeHtml(statusText(payload?.source_status || 'active'))}</em>
      </div>
      <div class="market-strength-panel__index-summary market-strength-panel__index-summary--${scoreTone(payload?.score)}">
        <span><small>技术倾向</small><strong>${formatScore(payload?.score)}</strong></span>
        <em>${escapeHtml(technicalSignalLabel(payload?.overall))}</em>
      </div>
      <dl class="market-strength-panel__index-signals">
        ${entries.map(([key, item]) => `
          <div>
            <dt>${escapeHtml(item.label || key.toUpperCase())}</dt>
            <dd><strong>${escapeHtml(technicalSignalValue(key, item.value))}</strong><small>${escapeHtml(technicalSignalLabel(item.signal))}</small></dd>
          </div>
        `).join('')}
      </dl>
      <p class="market-strength-panel__technical-meta">指数自身技术分只使用该指数价格历史，不代替全市场广度与风险判断。</p>
    </section>
  `;
}

export function isMajorMarketIndex(ticker) {
  const normalized = normalizeTicker(ticker);
  return Object.hasOwn(MAJOR_INDEX_NAMES, normalized) || ADDITIONAL_MAJOR_INDICES.has(normalized);
}

export function supportsUsMarketResearch(ticker) {
  return US_MARKET_RESEARCH_INDICES.has(normalizeTicker(ticker));
}

export function getMajorIndexDisplayName(ticker) {
  return MAJOR_INDEX_NAMES[normalizeTicker(ticker)] || '';
}

export function getMajorIndexBadge(ticker) {
  return MAJOR_INDEX_BADGES[normalizeTicker(ticker)] || '';
}

export function renderMarketStrengthPanel({
  variant = 'screener',
  ticker = '',
  marketRegime = null,
  marketShape = null,
  asOf = '',
  loading = false,
  updating = false,
  error = '',
  marketSignals = null,
  technicalLoading = false,
  technicalError = '',
  indexSignals = null,
  indexLoading = false,
  indexError = '',
  retryable = variant === 'detail',
} = {}) {
  const isDetail = variant === 'detail';
  const normalizedTicker = normalizeTicker(ticker);
  const stableTicker = normalizedTicker.replace(/[^A-Z0-9._-]/g, '-') || 'market';
  const idPrefix = isDetail ? `detail-market-strength-${stableTicker}` : 'screener-market-strength';
  const regime = marketRegime && typeof marketRegime === 'object' ? marketRegime : {};
  const shape = marketShape && typeof marketShape === 'object'
    ? marketShape
    : (regime.market_shape && typeof regime.market_shape === 'object' ? regime.market_shape : {});
  const score = finiteScore(regime.score);
  const regimeStatus = String(regime.status || '').toLowerCase();
  const hasRegimeData = score !== null || DIMENSIONS.some(([, key]) => finiteScore(regime[key]) !== null);
  const failed = Boolean(error);
  const regimeIncomplete = regimeStatus === 'degraded'
    || (score === null && regimeStatus !== 'active');
  const insufficient = !failed && !loading && (
    regimeStatus === 'insufficient_data'
    || regimeStatus === 'unavailable'
    || regimeStatus === 'not_available'
    || regimeIncomplete
    || !hasRegimeData
  );
  const technicalHasData = finiteScore(marketSignals?.scores?.top_score) !== null
    || finiteScore(marketSignals?.scores?.bottom_score) !== null;
  const indexHasData = finiteScore(indexSignals?.score) !== null
    || Boolean(indexSignals?.signals && Object.keys(indexSignals.signals).length);
  const anySourceReady = hasRegimeData || (isDetail && (technicalHasData || indexHasData));
  const anySourceLoading = loading || (isDetail && (technicalLoading || indexLoading));
  const technicalUnavailable = isDetail && !technicalLoading && !technicalHasData;
  const indexUnavailable = isDetail && !indexLoading && !indexHasData;
  const allSourcesFailed = failed && (!isDetail || (Boolean(technicalError) && Boolean(indexError)));
  const partial = anySourceReady && (
    insufficient
    || failed
    || (isDetail && (technicalUnavailable || indexUnavailable || anySourceLoading))
  );
  const viewState = allSourcesFailed
    ? 'error'
    : partial
      ? 'partial'
      : anySourceLoading && !anySourceReady
        ? 'loading'
        : !anySourceReady
          ? 'insufficient'
          : 'ready';
  const statusLabel = updating
    ? '更新中'
    : partial
      ? '部分可用'
      : allSourcesFailed
        ? '读取失败'
        : anySourceLoading && !anySourceReady
          ? '读取中'
          : !anySourceReady
            ? '数据不足'
            : statusText(regime.status || 'active');
  const warnings = normalizedWarnings(regime.warnings);
  const timestamp = formatTimestamp(asOf || regime.as_of);
  const description = isDetail
    ? '汇总系统基准组合的趋势、广度、量价与风险环境，与当前指数价格图分开计算。'
    : '来自当前选股扫描的市场环境，分项口径与候选计算保持一致。';

  let body = '';
  if (loading) {
    body = `
      <div class="market-strength-panel__message market-strength-panel__message--loading" role="status">
        <i aria-hidden="true"></i>
        <span><strong>正在读取市场强度</strong><small>等待趋势、广度、量价与形态数据</small></span>
      </div>
    `;
  } else if (failed) {
    body = `
      <div class="market-strength-panel__message market-strength-panel__message--error" role="alert">
        <span><strong>市场强度暂时无法读取</strong><small>${escapeHtml(error || '数据服务没有响应，请稍后重试。')}</small></span>
        ${retryable ? '<button type="button" data-market-strength-retry>重试</button>' : ''}
      </div>
    `;
  } else {
    body = `
      <div class="market-strength-panel__overview market-strength-panel__overview--${scoreTone(regime.score)}">
        <div class="market-strength-panel__score">
          <span>综合强度</span>
          <strong>${formatScore(regime.score)}</strong>
          <small>${escapeHtml(regime.label || (insufficient ? '数据不足' : '判断待确认'))}</small>
        </div>
        ${renderShape(shape)}
      </div>
      <div class="market-strength-panel__dimensions" aria-label="市场强度分项">
        ${DIMENSIONS.map(([label, key]) => renderDimension(label, regime[key])).join('')}
      </div>
      ${insufficient ? `
        <div class="market-strength-panel__message market-strength-panel__message--insufficient" role="status">
          <span><strong>暂不生成市场强弱结论</strong><small>核心行情或形态证据不足，缺失值保持为空，不按零分或中性值处理。</small></span>
        </div>
      ` : ''}
      ${renderWarnings(warnings)}
    `;
  }

  return `
    <section class="market-strength-panel market-strength-panel--${isDetail ? 'detail' : 'screener'} is-${viewState}"
      aria-labelledby="${idPrefix}-title" aria-describedby="${idPrefix}-description ${idPrefix}-disclaimer"
      aria-busy="${loading || updating}" data-market-strength-state="${viewState}"
      data-motion-reveal data-motion-key="${idPrefix}">
      <div class="market-strength-panel__header">
        <div class="market-strength-panel__intro">
          <span class="market-strength-panel__eyebrow">市场环境研究</span>
          <h3 id="${idPrefix}-title">大盘强势技术判断</h3>
          <p id="${idPrefix}-description">${description}</p>
        </div>
        <span class="market-strength-panel__status" role="status">
          <i aria-hidden="true"></i>${escapeHtml(statusLabel)}
        </span>
      </div>
      ${isDetail ? `
        <div class="market-strength-panel__sources">
          ${renderIndexTechnical({
            payload: indexSignals,
            loading: indexLoading,
            error: indexError,
            retryable,
            ticker,
          })}
          ${renderTechnicalSignals({
            payload: marketSignals,
            loading: technicalLoading,
            error: technicalError,
            retryable,
          })}
          <section class="market-strength-panel__risk" aria-labelledby="market-risk-title">
            <div class="market-strength-panel__section-heading">
              <span><small>风险确认</small><strong id="market-risk-title">环境强度与六态形态</strong></span>
              <em>${escapeHtml(statusText(regime.status))}</em>
            </div>
            ${body}
          </section>
        </div>
      ` : body}
      <footer class="market-strength-panel__footer">
        <span>${timestamp ? `数据时间 ${escapeHtml(timestamp)}` : loading ? '正在确认数据时间' : '数据时间待确认'}</span>
        <strong id="${idPrefix}-disclaimer">仅作市场环境研究，不构成买卖信号。</strong>
      </footer>
    </section>
  `;
}

// Keep the historical export name for any cached route module while rendering
// the production data states instead of the retired placeholder.
export const renderMarketStrengthPlaceholder = renderMarketStrengthPanel;
