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

function normalizeTicker(ticker) {
  return String(ticker || '').trim().toUpperCase();
}

function escapeAttribute(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

export function isMajorMarketIndex(ticker) {
  const normalized = normalizeTicker(ticker);
  return Object.hasOwn(MAJOR_INDEX_NAMES, normalized) || ADDITIONAL_MAJOR_INDICES.has(normalized);
}

export function getMajorIndexDisplayName(ticker) {
  return MAJOR_INDEX_NAMES[normalizeTicker(ticker)] || '';
}

export function getMajorIndexBadge(ticker) {
  return MAJOR_INDEX_BADGES[normalizeTicker(ticker)] || '';
}

export function renderMarketStrengthPlaceholder({ variant = 'screener', ticker = '' } = {}) {
  const isDetail = variant === 'detail';
  const idPrefix = isDetail ? 'detail-market-strength' : 'screener-market-strength';
  const normalizedTicker = normalizeTicker(ticker);
  const stableTicker = normalizedTicker.replace(/[^A-Z0-9._-]/g, '-') || 'market';
  const motionKey = isDetail ? `detail-market-strength-${stableTicker}` : 'screener-market-strength';
  const description = isDetail
    ? '技术分析计划待接入。目前仅预留研究结构，不生成强弱结论、评分或交易信号。'
    : '技术分析计划待接入。目前仅展示未来分析结构，不参与本轮扫描、排序或评分。';
  const scopeNote = isDetail
    ? '与当前K线、原始价格及复权价格口径相互独立'
    : '与现有市场环境判断和候选结果相互独立';

  return `
    <section class="market-strength-placeholder market-strength-placeholder--${isDetail ? 'detail' : 'screener'}"
      aria-labelledby="${idPrefix}-title" aria-describedby="${idPrefix}-description ${idPrefix}-scope"
      data-motion-reveal data-motion-key="${escapeAttribute(motionKey)}">
      <div class="market-strength-placeholder__intro">
        <span class="market-strength-placeholder__eyebrow">技术研究预留</span>
        <h3 id="${idPrefix}-title">大盘强势技术判断</h3>
        <p id="${idPrefix}-description">${description}</p>
      </div>
      <span class="market-strength-placeholder__status" role="status" aria-label="模块状态：待接入">
        <i aria-hidden="true"></i>
        待接入
      </span>
      <dl class="market-strength-placeholder__slots" aria-label="计划接入的分析维度">
        <div>
          <dt>趋势结构</dt>
          <dd>等待指标与规则</dd>
        </div>
        <div>
          <dt>广度动量</dt>
          <dd>等待市场广度方案</dd>
        </div>
        <div>
          <dt>风险确认</dt>
          <dd>等待确认条件</dd>
        </div>
      </dl>
      <p id="${idPrefix}-scope" class="market-strength-placeholder__scope">${scopeNote}</p>
    </section>
  `;
}
