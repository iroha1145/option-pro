import { api } from '../api.js';
import { renderHeatmap } from '../components/heatmap.js';

// No fake fallbacks — real data or empty state.
const FALLBACK_SECTORS = [];
const FALLBACK_IV = [];
const FALLBACK_HEATMAP = [];
let activeMountGeneration = 0;
let activeDetailGeneration = 0;

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function navigateToDetail(ticker) {
  const symbol = String(ticker || '').trim().toUpperCase();
  if (!symbol) return;
  window.location.hash = `#detail/${encodeURIComponent(symbol)}`;
}

function formatPercent(value) {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const sign = number > 0 ? '+' : '';
  return `${sign}${number.toFixed(2)}%`;
}

function finiteValue(...values) {
  for (const value of values) {
    if (value === null || value === undefined || value === '') continue;
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return null;
}

function formatIv(value) {
  return Number.isFinite(value) ? `${value.toFixed(1)}%` : '—';
}

function isSectorsMounted(generation) {
  return generation === activeMountGeneration
    && window.location.hash.replace(/^#/, '').split('/')[0] === 'sectors'
    && Boolean(document.querySelector(`[data-sectors-mount="${generation}"]`));
}

function normalizeSectors(payload) {
  const items = Array.isArray(payload) ? payload : (payload?.sectors ?? payload?.items ?? payload?.data ?? []);
  return items.map((item) => {
    const name = item.name ?? item.sector ?? item.label ?? '板块';
    const leaders = item.leaders ?? item.tickers ?? item.symbols ?? [];
    const ticker = String(item.ticker ?? item.symbol ?? leaders?.[0] ?? '').toUpperCase();
    return {
      name,
      ticker,
      id: String(item.id ?? item.sector_id ?? ''),
      performance: finiteValue(item.performance, item.changePercent, item.change_percent, item.change),
      iv: finiteValue(item.atm_iv_percent, item.avg_atm_iv_percent, item.avgIv, item.average_iv),
      leaders: Array.isArray(leaders) ? leaders.slice(0, 4).map((leader) => String(leader).toUpperCase()) : []
    };
  }).filter((sector) => sector.name);
}

function normalizeIvRanking(payload, sectorName) {
  // Absolute ATM IV and ranking are intentionally separate. Historical
  // iv_rank/iv_percentile must never be synthesized from the current IV.
  const items = Array.isArray(payload)
    ? payload
    : (payload?.rankings ?? payload?.ivRanking ?? payload?.iv_ranking ?? payload?.items ?? payload?.data ?? []);
  return items.map((item) => {
    let iv = finiteValue(item.atm_iv_percent, item.iv_pct, item.iv_current);
    if (iv == null) {
      const fractionalIv = finiteValue(item.iv, item.impliedVolatility, item.implied_volatility);
      iv = fractionalIv == null ? null : fractionalIv * 100;
    }
    const sectorRank = finiteValue(item.sector_iv_rank, item.sectorIvRank);
    const historicalRank = finiteValue(item.iv_percentile, item.ivRank, item.iv_rank);
    return {
      ticker: String(item.ticker ?? item.symbol ?? '').toUpperCase(),
      sector: item.sector ?? item.industry ?? sectorName ?? '市场',
      ivRank: sectorRank ?? historicalRank,
      rankLabel: sectorRank != null ? '板块排名' : historicalRank != null ? '历史分位' : '排名',
      iv,
      change: finiteValue(item.changePercent, item.change_percent, item.change),
    };
  }).filter((item) => item.ticker);
}

function renderSectorCards(sectors) {
  return sectors.map((sector) => `
    <article class="sector-card">
      <div class="sector-card__heading">
        <span class="label-caps">板块</span>
        <strong>${escapeHtml(sector.name)}</strong>
      </div>
      <div class="sector-card__metrics">
        <span><small class="label-caps">表现</small><b class="mono font-data-mono ${sector.performance == null ? '' : sector.performance >= 0 ? 'up' : 'down'}" data-numeric>${formatPercent(sector.performance)}</b></span>
        <span><small class="label-caps">平均 IV</small><b class="mono font-data-mono" data-numeric>${formatIv(sector.iv)}</b></span>
      </div>
      <div class="sector-tabs" aria-label="${escapeHtml(sector.name)} 领先标的">
        ${(sector.leaders.length ? sector.leaders : [sector.ticker]).filter(Boolean).map((ticker) => `<button class="sector-pill" type="button" data-ticker="${escapeHtml(ticker)}">${escapeHtml(ticker)}</button>`).join('')}
      </div>
    </article>
  `).join('');
}

function renderIvRanking(items) {
  return items.map((item, index) => `
    <button class="iv-ranking-item" type="button" data-ticker="${escapeHtml(item.ticker)}" aria-label="打开 ${escapeHtml(item.ticker)} 详情">
      <span class="iv-ranking-item__rank mono font-data-mono" data-numeric>${String(index + 1).padStart(2, '0')}</span>
      <span class="iv-ranking-item__identity"><strong>${escapeHtml(item.ticker)}</strong><small>${escapeHtml(item.sector)}</small></span>
      <span class="iv-ranking-item__metric"><small class="label-caps">${escapeHtml(item.rankLabel)}</small><b class="mono font-data-mono" data-numeric>${Number.isFinite(item.ivRank) ? item.ivRank.toFixed(0) : '—'}</b></span>
      <span class="iv-ranking-item__metric"><small class="label-caps">ATM IV</small><b class="mono font-data-mono" data-numeric>${formatIv(item.iv)}</b></span>
    </button>
  `).join('');
}

function renderShell(generation) {
  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = `
    <section class="sectors-page" data-sectors-mount="${generation}" aria-labelledby="sectors-title">
      <header class="terminal-header">
        <div>
          <span class="label-caps">板块</span>
          <h1 id="sectors-title">板块波动率分析</h1>
          <p>观察板块热度、IV 排名和成分股强弱，快速定位正在升温的交易主题。</p>
        </div>
      </header>
      <div class="sector-carousel-wrap">
        <button type="button" class="sector-carousel-arrow sector-carousel-arrow--left" id="sector-arrow-left" aria-label="向左滚动">
          <span class="material-symbols-outlined">chevron_left</span>
        </button>
        <div class="sector-carousel" id="sector-card-grid"><div class="panel loading-card">正在加载板块数据…</div></div>
        <button type="button" class="sector-carousel-arrow sector-carousel-arrow--right" id="sector-arrow-right" aria-label="向右滚动">
          <span class="material-symbols-outlined">chevron_right</span>
        </button>
      </div>
      <div class="sectors-layout">
        <section class="sector-section-card" aria-labelledby="iv-ranking-title">
          <div class="section-card-heading"><span class="label-caps">IV 排名</span><h2 id="iv-ranking-title">波动率领先标的</h2></div>
          <div id="iv-ranking-list" class="iv-ranking-list"><div class="detail-muted">正在加载 IV 排名…</div></div>
        </section>
        <div class="sectors-right-column">
          <section class="sector-section-card" aria-labelledby="heatmap-title">
            <div class="section-card-heading"><span class="label-caps">热力图</span><h2 id="heatmap-title">市场宽度</h2></div>
            <div id="sector-heatmap"><div class="detail-muted">正在加载热力图…</div></div>
          </section>
          <section class="sector-section-card" aria-labelledby="constituents-title">
            <div class="section-card-heading"><span class="label-caps">成分股表现</span><h2 id="constituents-title">板块龙头</h2></div>
            <div id="sector-constituents" class="sector-constituents"><div class="detail-muted">正在加载成分股…</div></div>
          </section>
          <section class="sector-section-card" aria-labelledby="stats-title">
            <div class="section-card-heading"><span class="label-caps">板块统计</span><h2 id="stats-title">总览</h2></div>
            <div id="sector-stats" class="sector-stats"></div>
          </section>
        </div>
      </div>
    </section>
  `;
}

function renderConstituents(stocks) {
  if (!stocks.length) return '<div class="detail-muted">该板块成分股暂无数据</div>';
  const sorted = [...stocks].sort((a, b) => (finiteValue(b.change_percent) ?? -Infinity) - (finiteValue(a.change_percent) ?? -Infinity));
  return `<div class="constituents-grid">
    ${sorted.map(s => {
      const pct = finiteValue(s.change_percent);
      const price = finiteValue(s.price);
      const tone = pct == null ? '' : pct >= 0 ? 'up' : 'down';
      return `<button type="button" class="constituent-card" data-ticker="${escapeHtml(s.ticker)}">
        <div class="constituent-top">
          <strong class="mono">${escapeHtml(s.ticker)}</strong>
          <span class="mono ${tone}" data-numeric>${formatPercent(pct)}</span>
        </div>
        <div class="constituent-name">${escapeHtml(s.name_cn || s.name || '')}</div>
        <div class="mono constituent-price">${price == null ? '—' : `$${price.toFixed(2)}`}</div>
      </button>`;
    }).join('')}
  </div>`;
}

function renderSectorStats(sectorName, ivItems, stocks) {
  const totalStocks = stocks.length;
  const changedStocks = stocks.filter((stock) => finiteValue(stock.change_percent) != null);
  const advancing = changedStocks.filter(s => finiteValue(s.change_percent) > 0).length;
  const declining = changedStocks.filter(s => finiteValue(s.change_percent) < 0).length;
  const avgChange = changedStocks.length
    ? changedStocks.reduce((sum, s) => sum + finiteValue(s.change_percent), 0) / changedStocks.length
    : null;
  const trueIvItems = ivItems.filter((item) => Number.isFinite(item.iv));
  const avgIv = trueIvItems.length
    ? trueIvItems.reduce((sum, item) => sum + item.iv, 0) / trueIvItems.length
    : null;
  const rankedStocks = [...changedStocks].sort((a, b) => finiteValue(b.change_percent) - finiteValue(a.change_percent));
  const maxGainer = rankedStocks[0];
  const maxLoser = rankedStocks[rankedStocks.length - 1];

  const stat = (label, value, tone = '') => `<div class="sector-stat">
    <span class="label-caps">${escapeHtml(label)}</span>
    <strong class="mono ${tone}" data-numeric>${escapeHtml(value)}</strong>
  </div>`;

  const fmtPct = (n) => n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;

  return `<div class="sector-stats-grid">
    ${stat('成分股数', totalStocks)}
    ${stat('上涨 / 下跌', `${advancing} / ${declining}`)}
    ${stat('平均涨幅', fmtPct(avgChange), avgChange == null ? '' : avgChange >= 0 ? 'up' : 'down')}
    ${stat('平均 ATM IV', avgIv == null ? '—' : formatIv(avgIv))}
    ${maxGainer ? stat('最强', `${maxGainer.ticker} ${fmtPct(finiteValue(maxGainer.change_percent))}`, 'up') : ''}
    ${maxLoser ? stat('最弱', `${maxLoser.ticker} ${fmtPct(finiteValue(maxLoser.change_percent))}`, 'down') : ''}
  </div>`;
}

async function loadSectorDetail(sectorId, sectorName, watchlistGroups = [], mountGeneration = activeMountGeneration) {
  if (!isSectorsMounted(mountGeneration)) return;
  const requestGeneration = ++activeDetailGeneration;
  const root = document.querySelector(`[data-sectors-mount="${mountGeneration}"]`);
  const ivList = root?.querySelector('#iv-ranking-list');
  const heatmap = root?.querySelector('#sector-heatmap');
  const constituents = root?.querySelector('#sector-constituents');
  const stats = root?.querySelector('#sector-stats');
  const ivTitle = root?.querySelector('#iv-ranking-title');
  const heatmapTitle = root?.querySelector('#heatmap-title');
  const constituentsTitle = root?.querySelector('#constituents-title');
  if (ivTitle) ivTitle.textContent = `${sectorName} · IV 排名`;
  if (heatmapTitle) heatmapTitle.textContent = `${sectorName} · 波动率热力图`;
  if (constituentsTitle) constituentsTitle.textContent = `${sectorName} · 成分股`;
  if (ivList) ivList.innerHTML = '<div class="detail-muted">正在加载 IV 排名…</div>';
  if (heatmap) heatmap.innerHTML = '<div class="detail-muted">正在加载热力图…</div>';
  if (constituents) constituents.innerHTML = '<div class="detail-muted">正在加载成分股…</div>';

  // Find watchlist stocks for this sector (by name match)
  const group = watchlistGroups.find(g => g.name === sectorName);
  const sectorStocks = group?.stocks || [];

  const [ivResult, hmResult] = await Promise.allSettled([
    api.sectorIV(sectorId),
    api.sectorHeatmap(sectorId)
  ]);
  if (!isSectorsMounted(mountGeneration) || requestGeneration !== activeDetailGeneration) return;
  let ivItems = FALLBACK_IV;
  if (ivResult.status === 'fulfilled') {
    const normalized = normalizeIvRanking(ivResult.value, sectorName);
    if (normalized.length) ivItems = normalized;
  }
  if (ivList) {
    ivList.innerHTML = ivResult.status === 'rejected'
      ? '<div class="detail-muted" style="color:var(--color-crimson)">IV 排名加载失败 <button type="button" data-sector-detail-retry>重试</button></div>'
      : ivItems.length ? renderIvRanking(ivItems) : '<div class="detail-muted">暂无有效 IV 数据</div>';
  }

  // Backfill avg IV onto the sector card
  if (ivItems.length) {
    const validIv = ivItems.filter((item) => Number.isFinite(item.iv));
    const avgIv = validIv.length ? validIv.reduce((sum, item) => sum + item.iv, 0) / validIv.length : null;
    const cardIv = [...(root?.querySelectorAll('[data-sector-iv]') || [])]
      .find((element) => element.dataset.sectorIv === String(sectorId));
    if (cardIv) cardIv.textContent = formatIv(avgIv);
  }
  const hmPayload = hmResult.status === 'fulfilled' ? hmResult.value : FALLBACK_HEATMAP;
  if (heatmap) heatmap.innerHTML = hmResult.status === 'rejected'
    ? '<div class="detail-muted" style="color:var(--color-crimson)">热力图加载失败 <button type="button" data-sector-detail-retry>重试</button></div>'
    : renderHeatmap(hmPayload);
  if (constituents) constituents.innerHTML = renderConstituents(sectorStocks);
  if (stats) stats.innerHTML = renderSectorStats(sectorName, ivItems, sectorStocks);

  root?.querySelectorAll('[data-sector-detail-retry]').forEach((button) => {
    button.addEventListener('click', () => loadSectorDetail(sectorId, sectorName, watchlistGroups, mountGeneration));
  });
}

export async function renderSectors() {
  const mountGeneration = ++activeMountGeneration;
  activeDetailGeneration += 1;
  renderShell(mountGeneration);
  const root = document.querySelector(`[data-sectors-mount="${mountGeneration}"]`);
  const sectorGrid = root?.querySelector('#sector-card-grid');

  let sectors = FALLBACK_SECTORS;
  let sectorList = [];
  let watchlistGroups = [];
  try {
    const [sectorData, watchlistData] = await Promise.all([
      api.sectors(),
      api.watchlist().catch(() => ({ groups: [] }))
    ]);
    sectorList = sectorData?.sectors || [];
    watchlistGroups = watchlistData?.groups || [];
    const rawSectors = normalizeSectors(sectorData);
    if (rawSectors.length) sectors = rawSectors;
  } catch (e) {
    console.warn('Sectors data load error:', e);
  }
  if (!isSectorsMounted(mountGeneration) || !sectorGrid) return;

  if (!sectors.length) {
    sectorGrid.innerHTML = '<div class="panel detail-muted" style="padding:24px;color:var(--color-crimson)">板块数据加载失败 <button type="button" data-sectors-retry>重试</button></div>';
    sectorGrid.querySelector('[data-sectors-retry]')?.addEventListener('click', () => renderSectors());
    root.querySelector('#iv-ranking-list').innerHTML = '<div class="detail-muted">等待板块数据</div>';
    root.querySelector('#sector-heatmap').innerHTML = '<div class="detail-muted">等待板块数据</div>';
    root.querySelector('#sector-constituents').innerHTML = '<div class="detail-muted">等待板块数据</div>';
    return;
  }

  // Compute performance from watchlist groups (matched by sector name)
  const performanceByName = {};
  for (const g of watchlistGroups) {
    const stocks = g.stocks || [];
    if (stocks.length) {
      const valid = stocks.map((stock) => finiteValue(stock.change_percent)).filter((value) => value != null);
      if (valid.length) performanceByName[g.name] = valid.reduce((sum, value) => sum + value, 0) / valid.length;
    }
  }

  if (sectorGrid) {
    sectorGrid.innerHTML = sectors.map((sector, i) => {
      const id = sector.id || sectorList[i]?.id || '';
      const perf = performanceByName[sector.name] ?? sector.performance;
      const ivLabel = Number.isFinite(sector.iv) && sector.iv > 0 ? formatIv(sector.iv) : '点击查看';
      return `
        <article class="sector-card" data-sector-id="${escapeHtml(id)}" data-sector-name="${escapeHtml(sector.name)}" style="cursor:pointer">
          <div class="sector-card__heading">
            <span class="label-caps">板块</span>
            <strong>${escapeHtml(sector.name)}</strong>
          </div>
          <div class="sector-card__metrics">
            <span><small class="label-caps">表现</small><b class="mono font-data-mono ${perf == null ? '' : perf >= 0 ? 'up' : 'down'}" data-numeric>${formatPercent(perf)}</b></span>
            <span><small class="label-caps">平均 ATM IV</small><b class="mono font-data-mono" data-sector-iv="${escapeHtml(id)}" data-numeric>${ivLabel}</b></span>
          </div>
          <div class="sector-tabs">
            ${(sector.leaders.length ? sector.leaders : (sectorList[i]?.tickers || []).slice(0,4)).filter(Boolean).map((t) => `<button class="sector-pill" type="button" data-ticker="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join('')}
          </div>
        </article>
      `;
    }).join('');
    // Wire sector card clicks (excluding the sector-pill ticker buttons)
    sectorGrid.querySelectorAll('.sector-card').forEach((card) => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('.sector-pill')) return;
        const id = card.dataset.sectorId;
        const name = card.dataset.sectorName;
        if (id) {
          sectorGrid.querySelectorAll('.sector-card').forEach(c => c.classList.remove('is-active'));
          card.classList.add('is-active');
          loadSectorDetail(id, name, watchlistGroups, mountGeneration);
        }
      });
    });
  }

  // Load first sector's iv-ranking and heatmap by default
  if (sectorList.length) {
    sectorGrid.querySelector('.sector-card')?.classList.add('is-active');
    const first = sectors[0];
    loadSectorDetail(first.id || sectorList[0].id, first.name || sectorList[0].name, watchlistGroups, mountGeneration);
  }

  // Wire ticker pill clicks
  root.addEventListener('click', (event) => {
    const tickerTarget = event.target.closest('[data-ticker]');
    if (tickerTarget && root.contains(tickerTarget)) navigateToDetail(tickerTarget.dataset.ticker);
  });

  // Wire carousel arrows
  const leftBtn = root.querySelector('#sector-arrow-left');
  const rightBtn = root.querySelector('#sector-arrow-right');
  const updateArrows = () => {
    if (!sectorGrid) return;
    const maxScroll = sectorGrid.scrollWidth - sectorGrid.clientWidth;
    if (leftBtn) leftBtn.disabled = sectorGrid.scrollLeft <= 4;
    if (rightBtn) rightBtn.disabled = sectorGrid.scrollLeft >= maxScroll - 4;
  };
  const scrollBy = (delta) => sectorGrid?.scrollBy({ left: delta, behavior: 'smooth' });
  leftBtn?.addEventListener('click', () => scrollBy(-sectorGrid.clientWidth * 0.7));
  rightBtn?.addEventListener('click', () => scrollBy(sectorGrid.clientWidth * 0.7));
  sectorGrid?.addEventListener('scroll', updateArrows, { passive: true });
  updateArrows();
}
