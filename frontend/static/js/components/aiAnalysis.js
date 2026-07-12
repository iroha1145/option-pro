import { api, safe } from '../api.js';
import { renderIcon } from '../icons.js';

const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function loadingLabel(label) {
  return `<span class="component-spinner" aria-hidden="true"></span> ${esc(label)}`;
}

function setButtonReady(button, icon, label) {
  button.innerHTML = `${renderIcon(icon, { size: 18 })} ${esc(label)}`;
  button.disabled = false;
  button.removeAttribute('aria-busy');
}

/** Render the alert-analysis action and its result in an existing options panel. */
export function renderAlertAnalysisButton(container, ticker, alerts, underlyingPrice, expiration, options = {}) {
  if (!container || !alerts || alerts.length === 0) return;

  const wrap = document.createElement('div');
  wrap.className = 'ai-analysis-mount ai-analysis-mount--alerts';
  wrap.innerHTML = `
    <button class="button ai-analysis-button" data-alert-analysis-button type="button">
      ${renderIcon('psychology', { size: 18 })}
      分析期权异动（${alerts.length} 条）
    </button>
    <div class="ai-analysis-result" data-alert-analysis-result hidden></div>`;

  container.appendChild(wrap);

  const button = wrap.querySelector('[data-alert-analysis-button]');
  const resultContainer = wrap.querySelector('[data-alert-analysis-result]');

  button.onclick = async () => {
    const requestContainer = container;
    button.innerHTML = loadingLabel('正在核对异动…');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');

    const result = await safe(api.analyzeAlerts({
      ticker,
      alerts,
      underlying_price: underlyingPrice,
      expiration
    }, { signal: options.signal }));

    // Do not write a late response into a detail page that has already been replaced.
    if (!requestContainer.isConnected || !wrap.isConnected) return;

    resultContainer.hidden = false;
    resultContainer.innerHTML = renderAnalysisCard(result);
    setButtonReady(button, 'psychology', '重新分析');
  };
}

export function renderAnalysisCard(data) {
  if (data?.__error || data?.error) {
    return `<section class="ai-result-card ai-result-card--error" aria-label="异动分析状态">
      <span class="label-caps">智能解读</span>
      <p>异动分析暂不可用，请稍后重试。</p>
    </section>`;
  }

  const confidenceKey = ['high', 'medium', 'low'].includes(String(data?.confidence || '').toLowerCase())
    ? String(data.confidence).toLowerCase()
    : 'medium';
  const directionKey = ['bullish', 'bearish', 'mixed', 'unknown'].includes(String(data?.direction || '').toLowerCase())
    ? String(data.direction).toLowerCase()
    : 'unknown';
  const confidenceLabels = { high: '把握较高', medium: '把握中等', low: '把握较低' };
  const directionLabels = {
    bullish: '倾向看涨',
    bearish: '倾向看跌',
    mixed: '多空交织',
    unknown: '方向无法判断',
  };
  const keyStrikes = Array.isArray(data?.key_strikes) ? data.key_strikes : [];

  return `<section class="ai-result-card" aria-label="期权异动分析结果">
    <div class="ai-result-card__header">
      <span class="label-caps">智能异动解读</span>
      ${data?._cached ? '<span class="ai-result-badge ai-result-badge--cached">缓存结果</span>' : ''}
    </div>
    <div class="ai-result-badges" aria-label="判断概览">
      <span class="ai-result-badge ai-result-badge--confidence-${confidenceKey}">${confidenceLabels[confidenceKey]}</span>
      <span class="ai-result-badge ai-result-badge--direction-${directionKey}">${directionLabels[directionKey]}</span>
    </div>
    ${data?.summary ? `<p class="ai-result-card__lead">${esc(data.summary)}</p>` : ''}
    ${data?.analysis ? `<p class="ai-result-card__body">${esc(data.analysis)}</p>` : ''}
    ${keyStrikes.length ? `<div class="ai-result-strikes" aria-label="关键行权价">
      ${keyStrikes.map((strike) => `<span><small>行权价</small><strong data-numeric>${esc(strike)}</strong></span>`).join('')}
    </div>` : ''}
    ${data?.risk_note ? `<p class="ai-result-card__risk"><strong>风险提示</strong>${esc(data.risk_note)}</p>` : ''}
  </section>`;
}

/** Render earnings-correlation analysis in a supplied page container. */
export function renderEarningsCorrelation(container) {
  const wrap = document.createElement('div');
  wrap.className = 'ai-correlation-mount';
  wrap.innerHTML = `
    <section class="panel ai-correlation-panel" aria-labelledby="ai-correlation-title">
      <div class="ai-correlation-panel__header">
        <div class="ai-correlation-panel__heading">
          <span class="label-caps">财报关联</span>
          <h3 id="ai-correlation-title">财报相关性分析</h3>
        </div>
        <button class="button ai-analysis-button" data-earnings-analysis-button type="button">
          ${renderIcon('auto_awesome', { size: 18 })}
          生成分析
        </button>
      </div>
      <div class="ai-correlation-panel__result" data-earnings-analysis-result></div>
    </section>`;

  container.appendChild(wrap);

  const button = wrap.querySelector('[data-earnings-analysis-button]');
  const resultContainer = wrap.querySelector('[data-earnings-analysis-result]');

  button.onclick = async () => {
    button.innerHTML = loadingLabel('正在分析…');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');

    const result = await safe(api.earningsCorrelation());
    if (!container.isConnected || !wrap.isConnected) return;

    resultContainer.innerHTML = renderEarningsCorrelationCard(result);
    setButtonReady(button, 'auto_awesome', '重新分析');
  };
}

function renderEarningsCorrelationCard(data) {
  if (data?.__error || data?.error) {
    return '<div class="ai-result-card ai-result-card--error"><p>财报关联分析暂不可用，请稍后重试。</p></div>';
  }

  const analysis = data?.analysis ?? data;
  const summary = typeof analysis === 'string' ? analysis : (analysis?.summary ?? analysis?.text ?? '');
  const sectorImpact = analysis?.sector_impact || analysis?.impacts || [];

  return `<section class="ai-result-card" aria-label="财报相关性分析结果">
    ${summary ? `<p class="ai-result-card__lead">${esc(summary)}</p>` : ''}
    ${Array.isArray(sectorImpact) && sectorImpact.length ? `<div class="ai-sector-impact-list">
      ${sectorImpact.map((item) => {
        const direction = String(item.direction || '').toLowerCase();
        const directionKey = ['bullish', 'bearish'].includes(direction) ? direction : 'neutral';
        const directionLabel = directionKey === 'bullish' ? '倾向看涨' : directionKey === 'bearish' ? '倾向看跌' : '方向中性';
        const affected = Array.isArray(item.affected) ? item.affected : [];

        return `<article class="ai-sector-impact ai-sector-impact--${directionKey}">
          <div class="ai-sector-impact__header">
            <strong>${esc(item.sector || item.name || '相关板块')}</strong>
            ${item.direction ? `<span>${directionLabel}</span>` : ''}
          </div>
          ${item.reason ? `<p>${esc(item.reason)}</p>` : ''}
          ${affected.length ? `<div class="ai-sector-impact__tickers" aria-label="相关标的">
            ${affected.map((affectedItem) => `<span data-numeric>${esc(affectedItem?.ticker || affectedItem)}</span>`).join('')}
          </div>` : ''}
        </article>`;
      }).join('')}
    </div>` : ''}
  </section>`;
}

/** Return the earnings-correlation panel shell for pages that bind it separately. */
export function renderEarningsCorrelationAI(label = '') {
  return `<section class="panel ai-correlation-panel" aria-label="财报相关性分析">
    <div class="ai-correlation-panel__header">
      <div class="ai-correlation-panel__heading">
        <span class="label-caps">财报关联</span>
        <h3>${esc(label || '财报相关性分析')}</h3>
      </div>
      <button class="button ai-analysis-button" data-earnings-correlation-button type="button">
        ${renderIcon('auto_awesome', { size: 18 })} 生成分析
      </button>
    </div>
    <div class="ai-correlation-panel__result" data-earnings-correlation-results></div>
  </section>`;
}
