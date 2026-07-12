import { api, safe } from '../api.js';
import { renderIcon } from '../icons.js';

const fmt = (n, d = 1) => n == null || Number.isNaN(Number(n)) ? '—' : Number(n).toFixed(d);
const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const signalLabels = {
  rsi14: '相对强弱指标（RSI 14）',
  sma20_dist: '距20日简单移动平均线（SMA）',
  sma50_dist: '距50日简单移动平均线（SMA）',
  return_20d: '20日涨跌幅',
  volume_zscore: '成交量标准分',
  macd_hist: '指数平滑异同移动平均线柱值（MACD）',
  relative_strength_spy: '相对标普500指数强弱',
  close_position: '区间收盘位置',
  obv_divergence: '能量潮背离（OBV）'
};

function normalizeScore(score) {
  if (score === null || score === undefined || score === '') return null;
  const number = Number(score);
  return Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : null;
}

function scoreTone(score, role) {
  if (role === 'risk') {
    if (score >= 70) return 'negative';
    if (score >= 50) return 'warning';
    if (score < 30) return 'positive';
    return 'accent';
  }

  if (role === 'opportunity') {
    if (score >= 70) return 'positive';
    if (score < 30) return 'muted';
  }

  return 'accent';
}

export function renderScoreGauge(title, score, label, reasons = null, role = 'neutral') {
  const normalized = normalizeScore(score);
  if (normalized === null) {
    return `<article class="tb-card tb-card--muted tb-card--missing">
      <div class="tb-card__top">
        <span class="label-caps">${esc(title)}</span>
        <strong data-numeric>—</strong>
      </div>
      <div class="tb-card__meter tb-card__meter--missing" role="status" aria-label="${esc(title)}数据不足"></div>
      <p>${esc(label || '数据不足')}</p>
    </article>`;
  }
  const rounded = Math.round(normalized);
  const raising = Array.isArray(reasons?.raising) ? reasons.raising : [];
  const suppressing = Array.isArray(reasons?.suppressing) ? reasons.suppressing : [];
  const chips = [
    ...raising.slice(0, 2).map((reason) => ({ tone: 'raise', text: reason })),
    ...(!raising.length ? suppressing.slice(0, 1).map((reason) => ({ tone: 'mute', text: reason })) : [])
  ];
  const tone = scoreTone(normalized, role);

  return `<article class="tb-card tb-card--${tone}">
    <div class="tb-card__top">
      <span class="label-caps">${esc(title)}</span>
      <strong data-numeric>${rounded}<small>/100</small></strong>
    </div>
    <progress class="tb-card__meter" max="100" value="${normalized}" aria-label="${esc(title)} ${rounded} 分">${rounded}</progress>
    <p>${esc(label || '')}</p>
    ${chips.length ? `<div class="tb-card__chips" aria-label="主要依据">
      ${chips.map((chip) => `<span class="tb-chip tb-chip--${chip.tone}">${esc(chip.text)}</span>`).join('')}
    </div>` : ''}
  </article>`;
}

export function interpretSignal(key, signal) {
  const raw = signal?.value;
  if (raw === null || raw === undefined || raw === '') return '数据不足';
  const value = Number(raw);
  if (!Number.isFinite(value)) return '数据不足';
  if (key.includes('rsi')) return value >= 70 ? '过热' : value <= 30 ? '超卖' : value >= 60 ? '偏热' : value <= 40 ? '偏弱' : '中性';
  if (key.includes('sma')) return value > 5 ? '明显过热' : value > 1 ? '轻度过热' : value < -5 ? '明显超跌' : value < -1 ? '轻度超跌' : '贴近均线';
  if (key.includes('volume')) return value > 2 ? '放量' : value < -1 ? '缩量' : '正常';
  if (key.includes('macd')) return value > 0 ? '动能改善' : value < 0 ? '动能减弱' : '中性';
  if (key.includes('relative')) return value > 1 ? '偏强' : value < -1 ? '偏弱' : '接近大盘';
  if (key.includes('iv') || key.includes('atr')) return value > 70 ? '波动高' : value < 30 ? '波动低' : '波动正常';
  if (key.includes('close_position')) return value > 70 ? '收近高位' : value < 30 ? '收近低位' : '区间中部';
  return '—';
}

function levelValues(value) {
  if (Array.isArray(value)) return value;
  return value == null || value === '' ? [] : [value];
}

function formatLocalTimestamp(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  try {
    return parsed.toLocaleString([], {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  } catch (_) {
    return '';
  }
}

function renderAiCard(data) {
  if (data?.__error || data?.error) {
    return '<div class="ai-result-card ai-result-card--error"><p>智能分析暂不可用，请稍后重试。</p></div>';
  }

  const levels = data?.key_levels || {};
  const support = levelValues(levels.support);
  const resistance = levelValues(levels.resistance);
  const eventRisks = Array.isArray(data?.event_risks) ? data.event_risks : [];

  return `<section class="ai-result-card" aria-label="信号一致性分析结果">
    <div class="ai-result-card__header">
      <span class="label-caps">智能解读</span>
      ${data?._cached ? '<span class="ai-result-badge ai-result-badge--cached">缓存结果</span>' : ''}
    </div>
    <div class="ai-result-card__summary-grid">
      <div class="ai-result-card__metric">
        <span>综合判断</span>
        <strong>${esc(data?.final_bias || data?.dominant_regime || '—')}</strong>
      </div>
      <div class="ai-result-card__metric">
        <span>判断把握</span>
        <strong data-numeric>顶部风险 ${esc(data?.top_risk_confidence ?? '—')} · 底部机会 ${esc(data?.bottom_opportunity_confidence ?? '—')}</strong>
      </div>
    </div>
    ${data?.most_important_signal ? `<p class="ai-result-card__lead"><strong>关键线索</strong>${esc(data.most_important_signal)}</p>` : ''}
    <dl class="ai-result-levels">
      <div><dt>支撑位</dt><dd data-numeric>${support.length ? support.map(esc).join('、') : '—'}</dd></div>
      <div><dt>阻力位</dt><dd data-numeric>${resistance.length ? resistance.map(esc).join('、') : '—'}</dd></div>
    </dl>
    ${eventRisks.length ? `<p class="ai-result-card__risk"><strong>事件风险</strong>${eventRisks.map(esc).join('；')}</p>` : ''}
    ${data?.summary ? `<p class="ai-result-card__body">${esc(data.summary)}</p>` : ''}
  </section>`;
}

export function renderTopBottomSignals(container, ticker, data, options = {}) {
  if (!data || data.__error) {
    container.innerHTML = '<div class="tb-empty-state" role="status">顶底信号暂不可用</div>';
    return;
  }

  const scores = data.scores || {};
  const signals = data.signals || {};
  const preferred = ['rsi14','sma20_dist','sma50_dist','return_20d','volume_zscore','macd_hist','relative_strength_spy','close_position','obv_divergence'];
  const rows = preferred.filter((key) => signals[key]).map((key) => {
    const signal = signals[key];
    return `<li><span>${esc(signalLabels[key] || signal.label || key)}</span><strong data-numeric>${fmt(signal.value, key.includes('macd') ? 4 : 1)} <span aria-hidden="true">→</span> ${interpretSignal(key, signal)}</strong></li>`;
  }).join('');
  const formattedTime = formatLocalTimestamp(data.as_of);
  const dataTime = formattedTime ? `数据时间 ${esc(formattedTime)}` : '数据时间待更新';

  container.innerHTML = `<section class="tb-shell" aria-labelledby="tb-title">
    <div class="tb-shell__header">
      <div>
        <span class="label-caps">技术信号</span>
        <h3 id="tb-title">顶底信号分析</h3>
        <p>观察窗口：5—20 个交易日（日线）</p>
      </div>
      <span class="tb-shell__time">${dataTime}${data._cached ? ' · 使用缓存' : ''}</span>
    </div>
    <div class="tb-card-grid">
      ${renderScoreGauge('趋势偏向', data.trend_bias_score, data.trend_bias_label)}
      ${renderScoreGauge('顶部风险', scores.top_score, scores.top_label, scores.top_reasons, 'risk')}
      ${renderScoreGauge('底部机会', scores.bottom_score, scores.bottom_label, scores.bottom_reasons, 'opportunity')}
      ${renderScoreGauge('回调质量', scores.dip_buy_quality, scores.dip_buy_label, { raising: scores.dip_buy_reasons || [], suppressing: [] }, 'opportunity')}
    </div>
    <details class="tb-breakdown">
      <summary>查看信号明细</summary>
      <ul>${rows || '<li class="tb-breakdown__empty">暂无可用明细</li>'}</ul>
    </details>
    <button class="ai-analysis-button tb-ai-button" data-tb-analysis-button type="button">
      ${renderIcon('psychology', { size: 18 })} 分析信号一致性
    </button>
    <div class="ai-analysis-result" data-tb-analysis-result hidden></div>
  </section>`;

  const button = container.querySelector('[data-tb-analysis-button]');
  const result = container.querySelector('[data-tb-analysis-result]');
  button.onclick = async () => {
    button.innerHTML = '<span class="component-spinner" aria-hidden="true"></span> 正在核对信号…';
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');

    const analysis = await safe(api.analyzeTopBottomSignals(ticker, { signal: options.signal }));
    if (!container.isConnected || !button.isConnected || !result.isConnected) return;

    result.hidden = false;
    result.innerHTML = renderAiCard(analysis);
    button.innerHTML = `${renderIcon('psychology', { size: 18 })} 重新分析`;
    button.disabled = false;
    button.removeAttribute('aria-busy');
  };
}
