import { renderIcon } from '../icons.js';

const money = (n) => n == null || Number.isNaN(Number(n)) ? '—' : Number(n).toFixed(2);
const int = (n) => n == null || Number.isNaN(Number(n)) ? '—' : Number(n).toLocaleString();
const pct = (n) => n == null || Number.isNaN(Number(n)) ? '—' : (Number(n) * 100).toFixed(1) + '%';
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Pick the first finite number across aliases. Zero is a valid volume or OI value.
const firstValid = (...values) => {
  for (const value of values) {
    if (value == null) continue;
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return null;
};

// A zero IV normally represents an unavailable quote, so it stays distinct from volume and OI.
const firstValidNonZero = (...values) => {
  for (const value of values) {
    if (value == null) continue;
    const number = Number(value);
    if (Number.isFinite(number) && number !== 0) return number;
  }
  return null;
};

const optionVolume = (option = {}) => firstValid(option.volume, option.vol);
const optionOI = (option = {}) => firstValid(option.open_interest, option.oi, option.openInterest);
const optionIV = (option = {}) => firstValidNonZero(option.implied_volatility, option.iv);

function groupOptionChain(chain = {}) {
  const groups = {};
  const suppliedGroups = chain.grouped_by_strike && typeof chain.grouped_by_strike === 'object'
    ? chain.grouped_by_strike
    : {};

  Object.entries(suppliedGroups).forEach(([rawStrike, group]) => {
    const strike = Number(rawStrike);
    if (!Number.isFinite(strike) || !group || typeof group !== 'object') return;
    groups[String(strike)] = { call: group.call || null, put: group.put || null };
  });

  const addFallback = (contracts, side) => {
    if (!Array.isArray(contracts)) return;
    contracts.forEach((contract) => {
      const strike = Number(contract?.strike);
      if (!Number.isFinite(strike)) return;
      const key = String(strike);
      if (!groups[key]) groups[key] = { call: null, put: null };
      if (!groups[key][side]) groups[key][side] = contract;
    });
  };

  addFallback(chain.calls, 'call');
  addFallback(chain.puts, 'put');
  return groups;
}

function alertMeta(alert = {}) {
  const direction = alert.inferred_direction || alert.signal || 'unknown';
  if (direction === 'bullish') return { icon: 'trending_up', tone: 'bullish', label: '方向推断偏多' };
  if (direction === 'bearish') return { icon: 'trending_down', tone: 'bearish', label: '方向推断偏空' };
  return { icon: 'help', tone: 'neutral', label: '方向尚不明确' };
}

/** Render unusual activity alerts above the chain. */
export function renderAlerts(alerts = []) {
  if (!alerts.length) return '';

  return `<section class="option-alert-shell" aria-labelledby="option-alert-title">
    <div class="option-alert-head">
      <div>
        <span class="label-caps">异动提醒</span>
        <h3 id="option-alert-title">期权成交异动</h3>
      </div>
      <span>方向来自成交特征推断，不代表确定走势</span>
    </div>
    <div class="option-alert-grid">
      ${alerts.map((alert) => {
        const meta = alertMeta(alert);
        const type = String(alert.type || '').toLowerCase() === 'call' ? '看涨' : '看跌';
        const expiration = alert.expiration
          ? `${String(alert.expiration).slice(5).replace('-', '/')} 到期`
          : '到期日待确认';
        const daysToExpiration = alert.dte == null ? '剩余天数待确认' : `距到期 ${esc(alert.dte)} 天`;
        const premium = firstValid(alert.premium_flow);
        const volume = optionVolume(alert);
        const openInterest = optionOI(alert);

        return `<article class="option-alert-card option-alert-card--${meta.tone}">
          <div class="option-alert-card__icon" aria-hidden="true">
            ${renderIcon(meta.icon, { size: 18 })}
          </div>
          <div class="option-alert-card__body">
            <div class="option-alert-card__top">
              <strong data-numeric>${type} ${money(alert.strike)}</strong>
              <span>${meta.label}</span>
            </div>
            <p>${esc(expiration)} · ${daysToExpiration}</p>
            <dl class="option-alert-card__metrics">
              <div><dt>成交量</dt><dd data-numeric>${int(volume)}</dd></div>
              <div><dt>持仓量</dt><dd data-numeric>${int(openInterest)}</dd></div>
              ${premium == null ? '' : `<div class="option-alert-card__premium"><dt>权利金</dt><dd data-numeric>$${int(premium)}</dd></div>`}
            </dl>
            ${(alert.reasons || []).length ? `<div class="option-alert-card__reasons" aria-label="触发依据">
              ${(alert.reasons || []).slice(0, 3).map((reason) => `<span>${esc(reason)}</span>`).join('')}
            </div>` : ''}
          </div>
        </article>`;
      }).join('')}
    </div>
  </section>`;
}

function deltaTone(delta) {
  if (delta == null || !Number.isFinite(Number(delta))) return 'is-missing';
  const absolute = Math.abs(Number(delta));
  if (absolute >= 0.5) return 'is-strong';
  if (absolute >= 0.2) return 'is-medium';
  return 'is-quiet';
}

export function renderOptionChain(chain) {
  if (!chain || chain.__error) {
    return '<div class="option-chain-empty" role="status">期权链暂无数据</div>';
  }

  const underlyingValue = Number(chain.underlying_price);
  const underlying = Number.isFinite(underlyingValue) ? underlyingValue : 0;
  const groups = groupOptionChain(chain);
  let allStrikes = Object.keys(groups)
    .map(Number)
    .filter(Number.isFinite)
    .sort((a, b) => a - b);

  // Keep the chain readable while preserving the nearest ten strikes on both sides of ATM.
  if (underlying > 0 && allStrikes.length > 20) {
    const atmIndex = allStrikes.reduce((best, strike, index) =>
      Math.abs(strike - underlying) < Math.abs(allStrikes[best] - underlying) ? index : best, 0);
    const lowerBound = Math.max(0, atmIndex - 10);
    const upperBound = Math.min(allStrikes.length, atmIndex + 11);
    allStrikes = allStrikes.slice(lowerBound, upperBound);
  }

  const fmtDelta = (delta) => delta == null || Number.isNaN(Number(delta)) ? '—' : Number(delta).toFixed(2);
  const fmtGamma = (gamma) => gamma == null || Number.isNaN(Number(gamma)) ? '—' : Number(gamma).toFixed(3);

  const rows = allStrikes.map((strike) => {
    const group = groups[String(strike)] || {};
    const call = group.call || {};
    const put = group.put || {};
    const isAtm = underlying > 0 && Math.abs(strike - underlying) < Math.max(1, underlying * 0.008);
    const callVolume = optionVolume(call);
    const putVolume = optionVolume(put);
    const callOI = optionOI(call);
    const putOI = optionOI(put);
    const callIV = optionIV(call);
    const putIV = optionIV(put);

    return `<tr class="option-chain-row${isAtm ? ' is-atm' : ''}">
      <td class="option-chain-cell option-chain-cell--primary" data-numeric>${callVolume == null ? '—' : int(callVolume)}</td>
      <td class="option-chain-cell" data-numeric>${callOI == null ? '—' : int(callOI)}</td>
      <td class="option-chain-cell option-chain-cell--delta ${deltaTone(call.delta)}" data-numeric>${fmtDelta(call.delta)}</td>
      <td class="option-chain-cell" data-numeric>${fmtGamma(call.gamma)}</td>
      <td class="option-chain-cell" data-numeric>${callIV == null ? '—' : pct(callIV)}</td>
      <th class="option-chain-strike" scope="row" data-numeric>
        ${money(strike)}${isAtm ? '<span class="option-chain-atm-label">平值</span>' : ''}
      </th>
      <td class="option-chain-cell" data-numeric>${putIV == null ? '—' : pct(putIV)}</td>
      <td class="option-chain-cell" data-numeric>${fmtGamma(put.gamma)}</td>
      <td class="option-chain-cell option-chain-cell--delta ${deltaTone(put.delta)}" data-numeric>${fmtDelta(put.delta)}</td>
      <td class="option-chain-cell" data-numeric>${putOI == null ? '—' : int(putOI)}</td>
      <td class="option-chain-cell option-chain-cell--primary" data-numeric>${putVolume == null ? '—' : int(putVolume)}</td>
    </tr>`;
  }).join('');

  return `<div class="option-chain-table-wrap">
    <table class="option-chain-table">
      <caption class="option-chain-caption">期权链：看涨与看跌合约关键指标</caption>
      <thead>
        <tr class="option-chain-groups">
          <th colspan="5" scope="colgroup">看涨期权</th>
          <th class="option-chain-groups__strike" scope="col">行权价</th>
          <th colspan="5" scope="colgroup">看跌期权</th>
        </tr>
        <tr class="option-chain-columns">
          <th scope="col">成交量</th>
          <th scope="col">持仓量</th>
          <th scope="col"><abbr title="德尔塔（Delta）">Δ</abbr></th>
          <th scope="col"><abbr title="伽马（Gamma）">Γ</abbr></th>
          <th scope="col"><abbr title="隐含波动率（Implied Volatility）">隐波</abbr></th>
          <th scope="col">行权价</th>
          <th scope="col"><abbr title="隐含波动率（Implied Volatility）">隐波</abbr></th>
          <th scope="col"><abbr title="伽马（Gamma）">Γ</abbr></th>
          <th scope="col"><abbr title="德尔塔（Delta）">Δ</abbr></th>
          <th scope="col">持仓量</th>
          <th scope="col">成交量</th>
        </tr>
      </thead>
      <tbody>${rows || '<tr><td class="option-chain-empty option-chain-empty--row" colspan="11">暂无可显示的期权合约</td></tr>'}</tbody>
    </table>
    <p class="option-chain-method" role="note">
      希腊值按布莱克—斯科尔斯模型（Black-Scholes）估算，固定无风险利率 5%，未计股息；方向提示缺少成交方向数据，仅作推断。
    </p>
  </div>`;
}

export function renderExpirationSelect(expirations = [], selected = '') {
  return `<select id="expiration-select" class="option-expiration-select" aria-label="期权到期日">
    ${expirations.map((expiration) => `<option value="${esc(expiration)}" ${expiration === selected ? 'selected' : ''}>${esc(expiration)}</option>`).join('')}
  </select>`;
}
