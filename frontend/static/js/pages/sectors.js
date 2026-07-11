import { api } from '../api.js';
import { renderHeatmap } from '../components/heatmap.js';

const SECTORS_STYLESHEET_ID = 'optix-sectors-v3-styles';
const EMPTY_IV = [];
const EMPTY_HEATMAP = [];

let activeMountGeneration = 0;
let activeDetailGeneration = 0;
let stylesheetPromise = null;
let pageState = createPageState();

function createPageState() {
  return {
    sectors: [],
    watchlistGroups: [],
    watchlistReady: false,
    watchlistFailed: false,
    activeSectorId: '',
    activeSectorName: '',
    inspectorTab: 'constituents',
  };
}

function ensureSectorsStylesheet() {
  const existing = document.getElementById(SECTORS_STYLESHEET_ID);
  if (existing?.sheet) return Promise.resolve();
  if (stylesheetPromise) return stylesheetPromise;

  stylesheetPromise = new Promise((resolve, reject) => {
    const link = existing || document.createElement('link');
    link.id = SECTORS_STYLESHEET_ID;
    link.rel = 'stylesheet';
    link.href = new URL('../../css/optix-sectors-v3.css', import.meta.url).href;
    link.addEventListener('load', resolve, { once: true });
    link.addEventListener('error', () => {
      link.remove();
      reject(new Error('板块页样式未能载入'));
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

function navigateToDetail(ticker) {
  const symbol = String(ticker || '').trim().toUpperCase();
  if (symbol) window.location.hash = `#detail/${encodeURIComponent(symbol)}`;
}

function finiteValue(...values) {
  for (const value of values) {
    if (value === null || value === undefined || value === '') continue;
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return null;
}

function formatPercent(value) {
  const number = finiteValue(value);
  return number == null ? '—' : `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function formatPrice(value) {
  const number = finiteValue(value);
  return number == null
    ? '—'
    : `$${number.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatIv(value) {
  const number = finiteValue(value);
  return number == null ? '—' : `${number.toFixed(1)}%`;
}

function toneClass(value) {
  const number = finiteValue(value);
  if (number == null || number === 0) return 'is-neutral';
  return number > 0 ? 'is-positive' : 'is-negative';
}

function isSectorsMounted(generation) {
  return generation === activeMountGeneration
    && window.location.hash.replace(/^#/, '').split('/')[0] === 'sectors'
    && Boolean(document.querySelector(`[data-sectors-mount="${generation}"]`));
}

function normalizeSectors(payload) {
  const items = Array.isArray(payload) ? payload : (payload?.sectors ?? payload?.items ?? payload?.data ?? []);
  return items.map((item, index) => {
    const name = String(item.name ?? item.sector ?? item.label ?? `板块 ${index + 1}`).trim();
    const leaders = item.leaders ?? item.tickers ?? item.symbols ?? [];
    const normalizedLeaders = Array.isArray(leaders)
      ? leaders.map((leader) => String(leader || '').trim().toUpperCase()).filter(Boolean).slice(0, 4)
      : [];
    return {
      id: String(item.id ?? item.sector_id ?? item.slug ?? '').trim(),
      name,
      ticker: String(item.ticker ?? item.symbol ?? normalizedLeaders[0] ?? '').trim().toUpperCase(),
      performance: finiteValue(item.performance, item.changePercent, item.change_percent, item.change),
      iv: finiteValue(item.atm_iv_percent, item.avg_atm_iv_percent, item.avgIv, item.average_iv),
      leaders: normalizedLeaders,
      originalIndex: index,
    };
  }).filter((sector) => sector.name && sector.id);
}

function normalizeIvRanking(payload, sectorName) {
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
      ticker: String(item.ticker ?? item.symbol ?? '').trim().toUpperCase(),
      name: String(item.name_cn ?? item.name ?? item.company ?? '').trim(),
      sector: String(item.sector ?? item.industry ?? sectorName ?? '市场'),
      ivRank: sectorRank ?? historicalRank,
      rankLabel: sectorRank != null ? '板块排名' : historicalRank != null ? '历史分位' : '排名',
      iv,
    };
  }).filter((item) => item.ticker);
}

function sortSectorsForRanking(sectors) {
  return [...sectors].sort((a, b) => {
    const aPerformance = finiteValue(a.performance);
    const bPerformance = finiteValue(b.performance);
    if (aPerformance == null && bPerformance == null) return a.originalIndex - b.originalIndex;
    if (aPerformance == null) return 1;
    if (bPerformance == null) return -1;
    return bPerformance - aPerformance || a.originalIndex - b.originalIndex;
  });
}

function renderRankSkeletons() {
  return Array.from({ length: 5 }, () => `
    <div class="sector-skeleton sector-skeleton--rank" aria-hidden="true">
      <span></span><span></span>
    </div>
  `).join('');
}

function renderListSkeletons(count = 4) {
  return `<div class="sector-list-skeleton" aria-hidden="true">
    ${Array.from({ length: count }, () => '<span></span>').join('')}
  </div>`;
}

function renderMapSkeleton() {
  return `<div class="sector-map-skeleton" aria-hidden="true">
    <span></span><span></span><span></span><span></span><span></span>
  </div>`;
}

function renderShell(generation) {
  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = `
    <section class="sector-page" data-sectors-mount="${generation}" data-mobile-view="map" data-motion-page="sectors" data-motion-key="sectors" aria-labelledby="sectors-title">
      <header class="sector-page__header" data-motion-reveal data-motion-lens data-motion-key="sectors-header">
        <div>
          <span class="sector-kicker">市场概览</span>
          <h1 id="sectors-title">板块</h1>
          <p>先看资金方向，再检查板块内部的波动和领先标的。</p>
        </div>
        <div id="sector-page-status" class="sector-status" role="status" aria-live="polite" data-tone="loading">
          <span class="sector-status__dot" aria-hidden="true"></span>
          <span>正在读取市场</span>
        </div>
      </header>

      <section class="sector-overview" data-motion-reveal data-motion-key="sectors-overview" aria-labelledby="sector-overview-title">
        <div class="sector-overview__statement">
          <span class="sector-kicker">今日宽度</span>
          <h2 id="sector-overview-title">正在计算板块强弱</h2>
          <p id="sector-overview-copy">数据到达后，这里会说明领涨方向与整体市场宽度。</p>
        </div>
        <dl class="sector-overview__facts" aria-label="板块市场摘要">
          <div><dt>上涨</dt><dd id="sector-overview-up" data-numeric>—</dd></div>
          <div><dt>下跌</dt><dd id="sector-overview-down" data-numeric>—</dd></div>
          <div><dt>平均表现</dt><dd id="sector-overview-average" data-numeric>—</dd></div>
        </dl>
      </section>

      <section class="sector-explorer" data-motion-reveal data-motion-key="sectors-ranking" aria-labelledby="sector-ranking-title">
        <header class="sector-section-heading">
          <div>
            <span class="sector-kicker">选择观察方向</span>
            <h2 id="sector-ranking-title">板块地图与排行</h2>
          </div>
          <span id="sector-count" class="sector-count" data-numeric>—</span>
        </header>
        <div id="sector-card-grid" class="sector-ranking" role="group" aria-label="按日内表现排列的板块" aria-busy="true">
          ${renderRankSkeletons()}
        </div>
      </section>

      <div class="sector-mobile-switch" role="group" aria-label="板块内容视图">
        <button type="button" data-sector-mobile-view="map" aria-pressed="true">成分股地图</button>
        <button type="button" data-sector-mobile-view="inspector" aria-pressed="false">板块详情</button>
      </div>

      <div class="sector-workspace" data-motion-reveal data-motion-key="sectors-workspace">
        <section class="sector-map-stage" data-motion-lens data-motion-spotlight data-motion-key="sector-map" aria-labelledby="heatmap-title">
          <header class="sector-map-header">
            <div>
              <span class="sector-kicker">成分股地图</span>
              <h2 id="heatmap-title">等待选择板块</h2>
              <p>面积表示相对权重，颜色和文字共同说明波动水平。</p>
            </div>
            <strong id="sector-map-performance" class="sector-map-performance is-neutral" data-numeric>—</strong>
          </header>
          <div class="sector-map-legend" aria-label="波动水平说明">
            <span><i data-tone="calm" aria-hidden="true"></i>较低</span>
            <span><i data-tone="balanced" aria-hidden="true"></i>中等</span>
            <span><i data-tone="elevated" aria-hidden="true"></i>较高</span>
          </div>
          <div id="sector-heatmap" class="sector-map-body" aria-live="polite" aria-busy="true">
            ${renderMapSkeleton()}
          </div>
        </section>

        <aside class="sector-inspector" data-motion-reveal data-motion-key="sector-inspector" aria-labelledby="sector-inspector-title">
          <header class="sector-inspector__header">
            <span class="sector-kicker">选中板块</span>
            <h2 id="sector-inspector-title">等待选择</h2>
            <p id="sector-inspector-subtitle">选择板块后查看市场宽度与领先标的。</p>
          </header>
          <div id="sector-stats" class="sector-stat-list" aria-live="polite">
            ${renderListSkeletons(4)}
          </div>
          <div class="sector-inspector-tabs" role="tablist" aria-label="板块详情">
            <button id="sector-tab-constituents" type="button" role="tab" aria-selected="true" aria-controls="sector-panel-constituents" tabindex="0" data-sector-tab="constituents">成分股</button>
            <button id="sector-tab-iv" type="button" role="tab" aria-selected="false" aria-controls="sector-panel-iv" tabindex="-1" data-sector-tab="iv">波动率</button>
          </div>
          <section id="sector-panel-constituents" class="sector-inspector-panel" role="tabpanel" aria-labelledby="sector-tab-constituents">
            <div id="sector-constituents">${renderListSkeletons()}</div>
          </section>
          <section id="sector-panel-iv" class="sector-inspector-panel" role="tabpanel" aria-labelledby="sector-tab-iv" hidden>
            <div id="iv-ranking-list">${renderListSkeletons()}</div>
          </section>
        </aside>
      </div>
    </section>
  `;
}

function renderMarketOverview(root, sectors) {
  const values = sectors.map((sector) => finiteValue(sector.performance)).filter((value) => value != null);
  const advancing = values.filter((value) => value > 0).length;
  const declining = values.filter((value) => value < 0).length;
  const average = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  const ranked = sortSectorsForRanking(sectors);
  const leader = ranked.find((sector) => finiteValue(sector.performance) != null);
  const laggard = [...ranked].reverse().find((sector) => finiteValue(sector.performance) != null);

  const title = root?.querySelector('#sector-overview-title');
  const copy = root?.querySelector('#sector-overview-copy');
  const up = root?.querySelector('#sector-overview-up');
  const down = root?.querySelector('#sector-overview-down');
  const averageNode = root?.querySelector('#sector-overview-average');
  if (title) {
    title.textContent = leader
      ? `${advancing} 个板块上涨，${leader.name}暂时领先`
      : '板块方向尚未形成';
  }
  if (copy) {
    copy.textContent = leader && laggard && leader.id !== laggard.id
      ? `${leader.name} ${formatPercent(leader.performance)}；${laggard.name} ${formatPercent(laggard.performance)}。选择下方板块查看内部结构。`
      : '部分表现数据仍在同步，已到达的板块可以先查看。';
  }
  if (up) up.textContent = `${advancing} 个`;
  if (down) down.textContent = `${declining} 个`;
  if (averageNode) {
    averageNode.textContent = formatPercent(average);
    averageNode.className = toneClass(average);
  }
}

function renderSectorRankings(root, sectors, activeSectorId = '') {
  const list = root?.querySelector('#sector-card-grid');
  const count = root?.querySelector('#sector-count');
  if (!list) return;
  const ranked = sortSectorsForRanking(sectors);
  list.setAttribute('aria-busy', 'false');
  if (count) count.textContent = `${ranked.length} 个板块`;
  list.innerHTML = ranked.map((sector, index) => {
    const leaders = sector.leaders.length ? sector.leaders.join(' · ') : (sector.ticker || '成分股待同步');
    const performance = formatPercent(sector.performance);
    return `
      <button class="sector-rank-button" type="button" data-sector-select data-motion-card data-motion-spotlight data-motion-key="sector-${escapeHtml(sector.id)}"
        data-sector-id="${escapeHtml(sector.id)}" data-sector-name="${escapeHtml(sector.name)}"
        aria-pressed="false" aria-label="查看 ${escapeHtml(sector.name)}，日内表现 ${escapeHtml(performance)}" tabindex="-1">
        <span class="sector-rank-button__number" data-numeric>${String(index + 1).padStart(2, '0')}</span>
        <span class="sector-rank-button__identity">
          <strong>${escapeHtml(sector.name)}</strong>
          <small>${escapeHtml(leaders)}</small>
        </span>
        <span class="sector-rank-button__metric ${toneClass(sector.performance)}" data-numeric>${performance}</span>
      </button>
    `;
  }).join('');
  const activeButton = setActiveSectorButton(root, activeSectorId || ranked[0]?.id || '');
  if (activeSectorId && activeButton) {
    activeButton.scrollIntoView({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
  }
}

function setActiveSectorButton(root, sectorId, focus = false) {
  let activeButton = null;
  root?.querySelectorAll('[data-sector-select]').forEach((button) => {
    const active = button.dataset.sectorId === String(sectorId);
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.tabIndex = active ? 0 : -1;
    if (active) activeButton = button;
  });
  if (focus && activeButton) {
    activeButton.focus({ preventScroll: true });
    activeButton.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  }
  return activeButton;
}

function setMobileSectorView(root, view) {
  const nextView = view === 'inspector' ? 'inspector' : 'map';
  root.dataset.mobileView = nextView;
  root.querySelectorAll('[data-sector-mobile-view]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.sectorMobileView === nextView));
  });
}

function renderConstituents(stocks, { pending = false, unavailable = false } = {}) {
  if (pending) return '<div class="sector-inline-state">正在同步成分股行情…</div>';
  if (unavailable) {
    return `<div class="sector-inline-state sector-inline-state--warning">
      <strong>成分股行情暂时不可用</strong>
      <span>板块地图与波动率仍可查看。</span>
      <button type="button" data-sector-watchlist-retry>重新读取行情</button>
    </div>`;
  }
  if (!stocks.length) return '<div class="sector-inline-state">该板块暂无成分股行情。</div>';
  const sorted = [...stocks].sort((a, b) => (
    (finiteValue(b.change_percent, b.changePercent, b.change) ?? -Infinity)
    - (finiteValue(a.change_percent, a.changePercent, a.change) ?? -Infinity)
  ));
  return `<div class="sector-constituent-list">
    ${sorted.map((stock, index) => {
      const ticker = String(stock.ticker ?? stock.symbol ?? '').trim().toUpperCase();
      const change = finiteValue(stock.change_percent, stock.changePercent, stock.change);
      return `
        <button type="button" class="sector-constituent-row" data-ticker="${escapeHtml(ticker)}" data-motion-card data-motion-key="constituent-${escapeHtml(ticker)}" aria-label="打开 ${escapeHtml(ticker)} 详情">
          <span class="sector-list-rank" data-numeric>${String(index + 1).padStart(2, '0')}</span>
          <span class="sector-list-identity"><strong>${escapeHtml(ticker)}</strong><small>${escapeHtml(stock.name_cn ?? stock.name ?? stock.company ?? '上市公司')}</small></span>
          <span class="sector-list-price" data-numeric>${formatPrice(stock.price)}</span>
          <span class="sector-list-change ${toneClass(change)}" data-numeric>${formatPercent(change)}</span>
          <span class="sector-list-arrow" aria-hidden="true">↗</span>
        </button>
      `;
    }).join('')}
  </div>`;
}

function renderIvRanking(items) {
  if (!items.length) return '<div class="sector-inline-state">暂无有效波动率数据。</div>';
  return `<div class="sector-iv-list">
    ${items.map((item, index) => `
      <button class="sector-iv-row" type="button" data-ticker="${escapeHtml(item.ticker)}" data-motion-card data-motion-key="iv-${escapeHtml(item.ticker)}" aria-label="打开 ${escapeHtml(item.ticker)} 详情">
        <span class="sector-list-rank" data-numeric>${String(index + 1).padStart(2, '0')}</span>
        <span class="sector-list-identity"><strong>${escapeHtml(item.ticker)}</strong><small>${escapeHtml(item.name || item.sector)}</small></span>
        <span class="sector-iv-metric"><small>${escapeHtml(item.rankLabel)}</small><b data-numeric>${Number.isFinite(item.ivRank) ? item.ivRank.toFixed(0) : '—'}</b></span>
        <span class="sector-iv-metric"><small>平值隐含波动率</small><b data-numeric>${formatIv(item.iv)}</b></span>
        <span class="sector-list-arrow" aria-hidden="true">↗</span>
      </button>
    `).join('')}
  </div>`;
}

function renderSectorStats(sector, ivItems, stocks) {
  const changedStocks = stocks.filter((stock) => finiteValue(stock.change_percent, stock.changePercent, stock.change) != null);
  const advancing = changedStocks.filter((stock) => finiteValue(stock.change_percent, stock.changePercent, stock.change) > 0).length;
  const declining = changedStocks.filter((stock) => finiteValue(stock.change_percent, stock.changePercent, stock.change) < 0).length;
  const averageChange = changedStocks.length
    ? changedStocks.reduce((sum, stock) => sum + finiteValue(stock.change_percent, stock.changePercent, stock.change), 0) / changedStocks.length
    : finiteValue(sector?.performance);
  const validIv = ivItems.filter((item) => Number.isFinite(item.iv));
  const averageIv = validIv.length
    ? validIv.reduce((sum, item) => sum + item.iv, 0) / validIv.length
    : finiteValue(sector?.iv);
  const coverage = stocks.length || ivItems.length;

  const stat = (label, value, tone = 'is-neutral') => `
    <div class="sector-stat-row">
      <span>${escapeHtml(label)}</span>
      <strong class="${tone}" data-numeric>${escapeHtml(value)}</strong>
    </div>`;

  return `
    ${stat('平均表现', formatPercent(averageChange), toneClass(averageChange))}
    ${stat('上涨 / 下跌', changedStocks.length ? `${advancing} / ${declining}` : '—')}
    ${stat('平均平值隐含波动率', formatIv(averageIv))}
    ${stat('数据覆盖', coverage ? `${coverage} 只标的` : '等待行情')}
  `;
}

function findSectorGroup(groups, sectorId, sectorName) {
  const normalizedName = String(sectorName || '').trim().toLowerCase();
  return groups.find((group) => {
    const groupId = String(group.id ?? group.sector_id ?? '').trim();
    const groupName = String(group.name ?? group.sector ?? '').trim().toLowerCase();
    return (groupId && groupId === String(sectorId)) || (groupName && groupName === normalizedName);
  });
}

function updateStatus(root, message, tone = 'ready') {
  const status = root?.querySelector('#sector-page-status');
  if (!status) return;
  status.dataset.tone = tone;
  const label = status.querySelector('span:last-child');
  if (label) label.textContent = message;
}

function setInspectorTab(root, tab, { focus = false } = {}) {
  const nextTab = tab === 'iv' ? 'iv' : 'constituents';
  pageState.inspectorTab = nextTab;
  const tabs = [...(root?.querySelectorAll('[data-sector-tab]') || [])];
  tabs.forEach((button) => {
    const active = button.dataset.sectorTab === nextTab;
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    button.tabIndex = active ? 0 : -1;
    if (focus && active) button.focus();
  });
  const constituentsPanel = root?.querySelector('#sector-panel-constituents');
  const ivPanel = root?.querySelector('#sector-panel-iv');
  if (constituentsPanel) constituentsPanel.hidden = nextTab !== 'constituents';
  if (ivPanel) ivPanel.hidden = nextTab !== 'iv';
}

function setDetailLoading(root, sectorName) {
  const inspectorTitle = root?.querySelector('#sector-inspector-title');
  const inspectorSubtitle = root?.querySelector('#sector-inspector-subtitle');
  const heatmapTitle = root?.querySelector('#heatmap-title');
  const mapPerformance = root?.querySelector('#sector-map-performance');
  const map = root?.querySelector('#sector-heatmap');
  const constituents = root?.querySelector('#sector-constituents');
  const ivList = root?.querySelector('#iv-ranking-list');
  const stats = root?.querySelector('#sector-stats');
  if (inspectorTitle) inspectorTitle.textContent = sectorName;
  if (inspectorSubtitle) inspectorSubtitle.textContent = '正在读取成分股、波动率与市场宽度。';
  if (heatmapTitle) heatmapTitle.textContent = `${sectorName}成分股`;
  if (mapPerformance) {
    mapPerformance.textContent = '—';
    mapPerformance.className = 'sector-map-performance is-neutral';
  }
  if (map) {
    map.setAttribute('aria-busy', 'true');
    map.innerHTML = renderMapSkeleton();
  }
  if (constituents) constituents.innerHTML = renderListSkeletons();
  if (ivList) ivList.innerHTML = renderListSkeletons();
  if (stats) stats.innerHTML = renderListSkeletons(4);
  updateStatus(root, `正在读取${sectorName}`, 'loading');
}

function renderDetailError(message) {
  return `<div class="sector-inline-state sector-inline-state--error">
    <strong>${escapeHtml(message)}</strong>
    <span>其他已到达的数据仍会保留。</span>
    <button type="button" data-sector-detail-retry>重新读取</button>
  </div>`;
}

async function loadSectorDetail(sectorId, sectorName, watchlistGroups = [], mountGeneration = activeMountGeneration) {
  if (!isSectorsMounted(mountGeneration)) return;
  const requestGeneration = ++activeDetailGeneration;
  const root = document.querySelector(`[data-sectors-mount="${mountGeneration}"]`);
  if (!root) return;
  setDetailLoading(root, sectorName);

  const sector = pageState.sectors.find((item) => item.id === String(sectorId)) || { id: sectorId, name: sectorName };
  const group = findSectorGroup(watchlistGroups, sectorId, sectorName);
  const sectorStocks = Array.isArray(group?.stocks) ? group.stocks : [];
  const [ivResult, heatmapResult] = await Promise.allSettled([
    api.sectorIV(sectorId),
    api.sectorHeatmap(sectorId),
  ]);
  if (!isSectorsMounted(mountGeneration) || requestGeneration !== activeDetailGeneration) return;

  let ivItems = EMPTY_IV;
  if (ivResult.status === 'fulfilled') ivItems = normalizeIvRanking(ivResult.value, sectorName);
  const validIv = ivItems.filter((item) => Number.isFinite(item.iv));
  const averageIv = validIv.length ? validIv.reduce((sum, item) => sum + item.iv, 0) / validIv.length : null;
  if (averageIv != null) sector.iv = averageIv;

  const map = root.querySelector('#sector-heatmap');
  const constituents = root.querySelector('#sector-constituents');
  const ivList = root.querySelector('#iv-ranking-list');
  const stats = root.querySelector('#sector-stats');
  const inspectorSubtitle = root.querySelector('#sector-inspector-subtitle');
  const mapPerformance = root.querySelector('#sector-map-performance');

  if (map) {
    map.setAttribute('aria-busy', 'false');
    map.innerHTML = heatmapResult.status === 'fulfilled'
      ? renderHeatmap(heatmapResult.value ?? EMPTY_HEATMAP)
      : renderDetailError('成分股地图暂时无法读取');
    map.querySelectorAll('.heatmap-tile[data-ticker]').forEach((tile) => {
      tile.setAttribute('data-motion-card', '');
      tile.setAttribute('data-motion-spotlight', '');
      tile.setAttribute('data-motion-key', `heatmap-${sectorId}-${tile.dataset.ticker || 'item'}`);
    });
  }
  if (constituents) {
    constituents.innerHTML = renderConstituents(sectorStocks, {
      pending: !pageState.watchlistReady,
      unavailable: pageState.watchlistFailed,
    });
  }
  if (ivList) {
    ivList.innerHTML = ivResult.status === 'fulfilled'
      ? renderIvRanking(ivItems)
      : renderDetailError('波动率排行暂时无法读取');
  }
  if (stats) stats.innerHTML = renderSectorStats(sector, ivItems, sectorStocks);
  if (inspectorSubtitle) {
    const count = sectorStocks.length || ivItems.length;
    inspectorSubtitle.textContent = count ? `${count} 只标的进入当前观察范围。` : '数据仍在同步，已有内容会先保留。';
  }
  if (mapPerformance) {
    mapPerformance.textContent = formatPercent(sector.performance);
    mapPerformance.className = `sector-map-performance ${toneClass(sector.performance)}`;
  }

  const partialFailure = ivResult.status === 'rejected' || heatmapResult.status === 'rejected' || pageState.watchlistFailed;
  updateStatus(root, partialFailure ? `${sectorName} · 部分数据不可用` : `${sectorName} · 已更新`, partialFailure ? 'warning' : 'ready');
  setInspectorTab(root, pageState.inspectorTab);
}

function selectSector(root, sectorId, sectorName, mountGeneration, { focus = false } = {}) {
  if (!sectorId || !isSectorsMounted(mountGeneration)) return;
  pageState.activeSectorId = String(sectorId);
  pageState.activeSectorName = String(sectorName || '板块');
  setActiveSectorButton(root, pageState.activeSectorId, focus);
  loadSectorDetail(pageState.activeSectorId, pageState.activeSectorName, pageState.watchlistGroups, mountGeneration);
}

function enrichSectorPerformance(sectors, groups) {
  const performanceByName = new Map();
  groups.forEach((group) => {
    const values = (group.stocks || [])
      .map((stock) => finiteValue(stock.change_percent, stock.changePercent, stock.change))
      .filter((value) => value != null);
    if (!values.length) return;
    const name = String(group.name ?? group.sector ?? '').trim().toLowerCase();
    performanceByName.set(name, values.reduce((sum, value) => sum + value, 0) / values.length);
  });
  return sectors.map((sector) => {
    const enriched = performanceByName.get(sector.name.toLowerCase());
    return enriched == null ? sector : { ...sector, performance: enriched };
  });
}

function applyWatchlistResult(root, result, mountGeneration) {
  if (!isSectorsMounted(mountGeneration)) return;
  pageState.watchlistReady = true;
  pageState.watchlistFailed = Boolean(result.error);
  pageState.watchlistGroups = Array.isArray(result.data?.groups) ? result.data.groups : [];
  if (!result.error) {
    pageState.sectors = enrichSectorPerformance(pageState.sectors, pageState.watchlistGroups);
    renderMarketOverview(root, pageState.sectors);
    renderSectorRankings(root, pageState.sectors, pageState.activeSectorId);
  }
  selectSector(root, pageState.activeSectorId, pageState.activeSectorName, mountGeneration);
}

async function retryWatchlist(root, mountGeneration) {
  if (!isSectorsMounted(mountGeneration)) return;
  pageState.watchlistReady = false;
  pageState.watchlistFailed = false;
  const constituents = root.querySelector('#sector-constituents');
  if (constituents) constituents.innerHTML = renderConstituents([], { pending: true });
  const result = await api.watchlist().then(
    (data) => ({ data, error: null }),
    (error) => ({ data: { groups: [] }, error }),
  );
  applyWatchlistResult(root, result, mountGeneration);
}

function bindPageEvents(root, mountGeneration) {
  root.addEventListener('click', (event) => {
    const mobileView = event.target.closest('[data-sector-mobile-view]');
    if (mobileView && root.contains(mobileView)) {
      setMobileSectorView(root, mobileView.dataset.sectorMobileView);
      return;
    }
    const sectorButton = event.target.closest('[data-sector-select]');
    if (sectorButton && root.contains(sectorButton)) {
      selectSector(root, sectorButton.dataset.sectorId, sectorButton.dataset.sectorName, mountGeneration);
      return;
    }
    const tickerTarget = event.target.closest('[data-ticker]');
    if (tickerTarget && root.contains(tickerTarget)) {
      navigateToDetail(tickerTarget.dataset.ticker);
      return;
    }
    const tab = event.target.closest('[data-sector-tab]');
    if (tab && root.contains(tab)) {
      setInspectorTab(root, tab.dataset.sectorTab);
      return;
    }
    if (event.target.closest('[data-sector-watchlist-retry]')) {
      retryWatchlist(root, mountGeneration);
      return;
    }
    if (event.target.closest('[data-sector-detail-retry]')) {
      selectSector(root, pageState.activeSectorId, pageState.activeSectorName, mountGeneration);
      return;
    }
    if (event.target.closest('[data-sectors-retry]')) renderSectors();
  });

  root.addEventListener('keydown', (event) => {
    const sectorButton = event.target.closest('[data-sector-select]');
    if (sectorButton && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
      const buttons = [...root.querySelectorAll('[data-sector-select]')];
      const index = buttons.indexOf(sectorButton);
      if (index < 0 || !buttons.length) return;
      event.preventDefault();
      const previous = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
      const nextIndex = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : (index + (previous ? -1 : 1) + buttons.length) % buttons.length;
      const next = buttons[nextIndex];
      selectSector(root, next.dataset.sectorId, next.dataset.sectorName, mountGeneration, { focus: true });
      return;
    }

    const tab = event.target.closest('[data-sector-tab]');
    if (tab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const nextTab = event.key === 'ArrowLeft' || event.key === 'Home' ? 'constituents' : 'iv';
      setInspectorTab(root, nextTab, { focus: true });
    }
  });
}

function renderPageFailure(root) {
  const ranking = root.querySelector('#sector-card-grid');
  const map = root.querySelector('#sector-heatmap');
  const constituents = root.querySelector('#sector-constituents');
  const ivList = root.querySelector('#iv-ranking-list');
  const stats = root.querySelector('#sector-stats');
  const message = `<div class="sector-empty-state">
    <strong>板块数据暂时不可用</strong>
    <span>没有改动你的自选内容，重新读取即可。</span>
    <button type="button" data-sectors-retry>重新读取板块</button>
  </div>`;
  if (ranking) {
    ranking.setAttribute('aria-busy', 'false');
    ranking.innerHTML = message;
  }
  if (map) {
    map.setAttribute('aria-busy', 'false');
    map.innerHTML = message;
  }
  if (constituents) constituents.innerHTML = '<div class="sector-inline-state">等待板块数据。</div>';
  if (ivList) ivList.innerHTML = '<div class="sector-inline-state">等待板块数据。</div>';
  if (stats) stats.innerHTML = '';
  updateStatus(root, '板块数据读取失败', 'error');
}

export async function renderSectors() {
  const mountGeneration = ++activeMountGeneration;
  activeDetailGeneration += 1;
  pageState = createPageState();
  try {
    await ensureSectorsStylesheet();
  } catch (error) {
    console.warn('Sectors stylesheet load error:', error);
  }
  if (mountGeneration !== activeMountGeneration || window.location.hash.replace(/^#/, '').split('/')[0] !== 'sectors') return;
  renderShell(mountGeneration);
  const root = document.querySelector(`[data-sectors-mount="${mountGeneration}"]`);
  if (!root) return;
  bindPageEvents(root, mountGeneration);

  const watchlistPromise = api.watchlist().then(
    (data) => ({ data, error: null }),
    (error) => ({ data: { groups: [] }, error }),
  );

  let sectors = [];
  try {
    const sectorData = await api.sectors();
    sectors = normalizeSectors(sectorData);
  } catch (error) {
    console.warn('Sectors data load error:', error);
  }
  if (!isSectorsMounted(mountGeneration)) return;
  if (!sectors.length) {
    renderPageFailure(root);
    return;
  }

  pageState.sectors = sectors;
  renderMarketOverview(root, pageState.sectors);
  renderSectorRankings(root, pageState.sectors);
  const first = sortSectorsForRanking(pageState.sectors)[0];
  selectSector(root, first.id, first.name, mountGeneration);

  const watchlistResult = await watchlistPromise;
  applyWatchlistResult(root, watchlistResult, mountGeneration);
}
