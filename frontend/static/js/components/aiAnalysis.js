import { api, safe } from '../api.js';

const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

/**
 * AI alert analysis button + result card.
 */
export function renderAlertAnalysisButton(container, ticker, alerts, underlyingPrice, expiration) {
  if (!container || !alerts || alerts.length === 0) return;

  const wrap = document.createElement('div');
  wrap.style.marginTop = '14px';
  wrap.innerHTML = `
    <button id="ai-analyze-btn" class="ai-analysis-button" style="width:100%;justify-content:center;display:inline-flex;align-items:center;gap:8px;justify-self:stretch">
      <span class="material-symbols-outlined" style="font-size:18px">psychology</span>
      AI 分析异动 (${alerts.length} 条信号)
    </button>
    <div id="ai-analysis-result" style="display:none;margin-top:14px"></div>`;

  container.appendChild(wrap);

  const btn = wrap.querySelector('#ai-analyze-btn');
  const resultDiv = wrap.querySelector('#ai-analysis-result');

  btn.onclick = async () => {
    const requestContainer = container;
    btn.innerHTML = '<span style="width:14px;height:14px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;display:inline-block"></span> 正在分析...';
    btn.disabled = true;

    const result = await safe(api.analyzeAlerts({
      ticker, alerts, underlying_price: underlyingPrice, expiration
    }));

    // The user may have changed expiration/ticker while the AI request ran.
    // Detached mounts must never write into the newly mounted detail page.
    if (!requestContainer.isConnected || !wrap.isConnected) return;

    resultDiv.style.display = 'block';
    resultDiv.innerHTML = renderAnalysisCard(result);

    btn.innerHTML = `<span class="material-symbols-outlined" style="font-size:18px">psychology</span> 重新分析`;
    btn.disabled = false;
  };
}

function renderAnalysisCard(data) {
  if (data?.__error || data?.error) {
    return `<div class="ai-result-card"><span class="label-caps">AI 分析</span><p style="margin:8px 0 0">AI 分析暂时不可用</p></div>`;
  }

  const conf = data?.confidence || 'medium';
  const dir = data?.direction || 'mixed';
  const confColors = {
    high:   { bg: '#0a0a0a', fg: '#fff' },
    medium: { bg: '#444', fg: '#fff' },
    low:    { bg: '#ba1a1a', fg: '#fff' },
  };
  const dirColors = {
    bullish: { bg: '#dff5ec', fg: '#059669' },
    bearish: { bg: '#ffdad6', fg: '#ba1a1a' },
    mixed:   { bg: '#efeded', fg: '#444' },
  };
  const dirLabels = { bullish: '看涨', bearish: '看空', mixed: '多空交织' };
  const c = confColors[conf] || confColors.medium;
  const d = dirColors[dir] || dirColors.mixed;

  return `
    <div class="ai-result-card">
      <div class="ai-result-card__header">
        <span class="label-caps">AI 异动分析</span>
        ${data?._cached ? '<span class="label-caps" style="background:var(--color-container);padding:3px 7px;border-radius:999px">缓存</span>' : ''}
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <span style="padding:4px 10px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;background:${c.bg};color:${c.fg}">置信度: ${esc(conf).toUpperCase()}</span>
        <span style="padding:4px 10px;border-radius:999px;font-size:11px;font-weight:800;background:${d.bg};color:${d.fg}">${esc(dirLabels[dir] || dir)}</span>
      </div>
      ${data?.summary ? `<p style="margin:0 0 10px;font-weight:700;color:var(--color-on-surface);line-height:1.55">${esc(data.summary)}</p>` : ''}
      ${data?.analysis ? `<p style="margin:0;font-size:13px;line-height:1.65;color:var(--color-muted)">${esc(data.analysis)}</p>` : ''}
      ${data?.key_strikes?.length ? `
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px">
          ${data.key_strikes.map(s => `<span style="padding:5px 10px;background:var(--color-surface);border:1px solid var(--color-border);border-radius:6px;font-family:'JetBrains Mono';font-size:11px;font-weight:700">STRIKE ${esc(s)}</span>`).join('')}
        </div>` : ''}
      ${data?.risk_note ? `<p style="margin:12px 0 0;font-size:11px;color:var(--color-muted)">⚠ ${esc(data.risk_note)}</p>` : ''}
    </div>`;
}

/**
 * Render earnings correlation analysis on the earnings page.
 */
export function renderEarningsCorrelation(container) {
  const wrap = document.createElement('div');
  wrap.style.marginTop = '24px';
  wrap.innerHTML = `
    <div class="panel" style="padding:20px">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px">
        <div>
          <span class="label-caps">AI 财报关联</span>
          <h3 style="margin:6px 0 0;font-size:18px;font-weight:800">财报相关性分析</h3>
        </div>
        <button id="ai-earnings-btn" class="ai-analysis-button">
          <span class="material-symbols-outlined" style="font-size:16px;margin-right:6px;vertical-align:middle">auto_awesome</span>
          生成分析
        </button>
      </div>
      <div id="ai-earnings-result"></div>
    </div>`;

  container.appendChild(wrap);

  const btn = wrap.querySelector('#ai-earnings-btn');
  const resultDiv = wrap.querySelector('#ai-earnings-result');

  btn.onclick = async () => {
    btn.innerHTML = '<span style="width:14px;height:14px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;display:inline-block"></span> 分析中…';
    btn.disabled = true;
    const result = await safe(api.earningsCorrelation());
    if (!container.isConnected || !wrap.isConnected) return;
    resultDiv.innerHTML = renderEarningsCorrelationCard(result);
    btn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px;margin-right:6px;vertical-align:middle">auto_awesome</span> 重新分析';
    btn.disabled = false;
  };
}

function renderEarningsCorrelationCard(data) {
  if (data?.__error || data?.error) {
    return `<div class="ai-result-card"><p style="margin:0">AI 财报关联分析暂时不可用</p></div>`;
  }
  const ana = data?.analysis ?? data;
  const summary = typeof ana === 'string' ? ana : (ana?.summary ?? ana?.text ?? '');
  const sectorImpact = ana?.sector_impact || ana?.impacts || [];
  return `
    <div class="ai-result-card">
      ${summary ? `<p style="margin:0 0 12px;line-height:1.65;color:var(--color-on-surface)">${esc(summary)}</p>` : ''}
      ${Array.isArray(sectorImpact) && sectorImpact.length ? `
        <div style="display:grid;gap:10px;margin-top:12px">
          ${sectorImpact.map(s => `
            <div style="padding:10px;border:1px solid var(--color-border);border-radius:6px;background:var(--color-surface)">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><strong>${esc(s.sector || s.name || '板块')}</strong>${s.direction ? `<span class="label-caps" style="color:${s.direction==='bullish'?'var(--color-emerald)':s.direction==='bearish'?'var(--color-crimson)':'var(--color-muted)'}">${s.direction==='bullish'?'看涨':s.direction==='bearish'?'看空':'中性'}</span>` : ''}</div>
              ${s.reason ? `<p style="margin:0;font-size:13px;color:var(--color-muted);line-height:1.55">${esc(s.reason)}</p>` : ''}
              ${Array.isArray(s.affected) && s.affected.length ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">${s.affected.map(i => `<span class="mono" style="padding:3px 7px;background:#fff;border:1px solid var(--color-border);border-radius:4px;font-size:11px;font-weight:700">${esc(i.ticker || i)}</span>`).join('')}</div>` : ''}
            </div>`).join('')}
        </div>` : ''}
    </div>`;
}

/**
 * Returns HTML for earnings correlation panel (used by earnings.js renderShell).
 * Pairs with bindEarningsCorrelation() that wires the button.
 */
export function renderEarningsCorrelationAI(label = '') {
  return `<div class="panel" style="padding:20px;margin-top:16px">
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px">
      <div>
        <span class="label-caps">AI 财报关联</span>
        <h3 style="margin:6px 0 0;font-size:18px;font-weight:800">${esc(label || '财报相关性分析')}</h3>
      </div>
      <button class="ai-analysis-button" data-earnings-correlation-button type="button">生成分析</button>
    </div>
    <div data-earnings-correlation-results></div>
  </div>`;
}
