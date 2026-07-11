function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function heatmapTone(value, mode) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return { tone: 'neutral', state: '数据不足', strength: 0, token: '--muted' };
  }

  if (mode === 'iv') {
    if (number < 40) {
      return {
        tone: 'calm',
        state: '波动较低',
        strength: clamp((40 - number) / 40, 0.12, 1),
        token: '--positive',
      };
    }
    if (number > 60) {
      return {
        tone: 'elevated',
        state: '波动较高',
        strength: clamp((number - 60) / 40, 0.12, 1),
        token: '--warning',
      };
    }
    return { tone: 'balanced', state: '波动中等', strength: 0.1, token: '--accent' };
  }

  if (number > 0) {
    return {
      tone: 'positive',
      state: '上涨',
      strength: clamp(Math.abs(number) / 5, 0.12, 1),
      token: '--positive',
    };
  }
  if (number < 0) {
    return {
      tone: 'negative',
      state: '下跌',
      strength: clamp(Math.abs(number) / 5, 0.12, 1),
      token: '--negative',
    };
  }
  return { tone: 'neutral', state: '持平', strength: 0, token: '--muted' };
}

// Kept as a small public helper for callers that need the same semantic fill.
// The returned color is resolved from the active design tokens in CSS.
export function getHeatmapTileColor(value = 0, mode = 'auto') {
  const number = Number(value);
  const resolvedMode = mode === 'iv' || (mode === 'auto' && Math.abs(number) > 10) ? 'iv' : 'perf';
  const meta = heatmapTone(number, resolvedMode);
  const surfaceShare = Math.round(88 - meta.strength * 28);
  return `color-mix(in oklch, var(--surface-soft) ${surfaceShare}%, var(${meta.token}))`;
}

export function normalizeHeatmapPayload(payload) {
  const items = Array.isArray(payload)
    ? payload
    : (payload?.data ?? payload?.heatmap ?? payload?.items ?? payload?.sectors ?? payload?.rankings ?? []);

  return items.map((item) => {
    const ticker = String(item.ticker ?? item.symbol ?? item.name ?? item.sector ?? '').trim().toUpperCase();
    let ivValue = item.atm_iv_percent ?? item.iv_pct ?? item.iv_current;
    let ivLabel = '平值隐含波动率';
    if (ivValue == null && (item.iv != null || item.impliedVolatility != null || item.implied_volatility != null)) {
      ivValue = Number(item.iv ?? item.impliedVolatility ?? item.implied_volatility) * 100;
    }
    if (ivValue == null) {
      ivValue = item.iv_percentile ?? item.ivRank ?? item.iv_rank ?? item.sector_iv_rank;
      ivLabel = item.sector_iv_rank != null ? '板块波动率排名' : '隐含波动率历史分位';
    }
    const performanceValue = item.changePercent
      ?? item.change_percentage
      ?? item.changePct
      ?? item.change_pct
      ?? item.percentChange
      ?? item.performance
      ?? item.change;
    const isIv = ivValue != null;
    const rawValue = isIv ? ivValue : performanceValue;
    const value = rawValue === null || rawValue === undefined || rawValue === '' ? NaN : Number(rawValue);
    const rawWeight = item.weight ?? item.marketCapWeight ?? item.market_cap_weight ?? item.size ?? 1;
    const weight = Number(rawWeight);
    return {
      ticker,
      label: String(item.label ?? item.companyName ?? item.company_name ?? item.name ?? ticker),
      value,
      mode: isIv ? 'iv' : 'perf',
      metricLabel: isIv ? ivLabel : '涨跌幅',
      weight: Number.isFinite(weight) && weight > 0 ? weight : 1,
    };
  }).filter((item) => item.ticker && Number.isFinite(item.value));
}

export function renderHeatmap(payload = []) {
  const items = normalizeHeatmapPayload(payload);
  if (!items.length) {
    return '<div class="heatmap-empty"><strong>暂无地图数据</strong><span>切换板块或稍后重试。</span></div>';
  }

  const maximumWeight = Math.max(...items.map((item) => item.weight), 1);
  return `
    <div class="heatmap-grid" aria-label="板块成分股波动地图">
      ${items.map((item) => {
        const meta = heatmapTone(item.value, item.mode);
        const valueLabel = item.mode === 'iv'
          ? `${item.metricLabel} ${Number(item.value).toFixed(1)}%`
          : formatPercent(item.value);
        const showLabel = item.label && item.label.toUpperCase() !== item.ticker;
        const relativeWeight = clamp(item.weight / maximumWeight, 0.35, 1);
        const tileColor = getHeatmapTileColor(item.value, item.mode);
        return `
          <button class="heatmap-tile" type="button" data-ticker="${escapeHtml(item.ticker)}"
            data-heatmap-tone="${meta.tone}"
            style="--tile-bg: ${tileColor}; --tile-weight: ${relativeWeight.toFixed(3)};"
            aria-label="打开 ${escapeHtml(item.ticker)} 详情，${escapeHtml(valueLabel)}，${escapeHtml(meta.state)}">
            <span class="heatmap-tile__heading">
              <strong class="heatmap-tile__ticker" data-numeric>${escapeHtml(item.ticker)}</strong>
              <small class="heatmap-tile__state">${escapeHtml(meta.state)}</small>
            </span>
            ${showLabel ? `<span class="heatmap-tile__label">${escapeHtml(item.label)}</span>` : ''}
            <span class="heatmap-tile__metric" data-numeric>${escapeHtml(valueLabel)}</span>
          </button>
        `;
      }).join('')}
    </div>
  `;
}
