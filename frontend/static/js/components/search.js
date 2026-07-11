import { api } from '../api.js';

const SEARCH_UNIVERSE_TTL_MS = 5 * 60 * 1000;

const FALLBACK_SYMBOLS = [
  { ticker: 'AAPL', company: 'Apple Inc.', sector: 'TECH' },
  { ticker: 'MSFT', company: 'Microsoft Corp.', sector: 'TECH' },
  { ticker: 'NVDA', company: 'NVIDIA Corp.', sector: 'SEMIS' },
  { ticker: 'TSLA', company: 'Tesla Inc.', sector: 'AUTO' },
  { ticker: 'AMD', company: 'Advanced Micro Devices', sector: 'SEMIS' },
  { ticker: 'META', company: 'Meta Platforms', sector: 'TECH' },
  { ticker: 'AMZN', company: 'Amazon.com Inc.', sector: 'TECH' },
  { ticker: 'GOOGL', company: 'Alphabet Inc.', sector: 'TECH' },
  { ticker: 'JPM', company: 'JPMorgan Chase', sector: 'BANKS' },
  { ticker: 'SPY', company: 'SPDR S&P 500 ETF', sector: 'ETF' },
  { ticker: 'QQQ', company: 'Invesco QQQ Trust', sector: 'ETF' }
];

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function normalizeSearchItem(item = {}) {
  const ticker = String(item.ticker ?? item.symbol ?? item.code ?? '').toUpperCase();
  if (!ticker) return null;
  return {
    ticker,
    company: String(item.company ?? item.companyName ?? item.company_name ?? item.name ?? '上市公司'),
    sector: String(item.sector ?? item.industry ?? item.assetClass ?? 'EQUITY').toUpperCase()
  };
}

function uniqueByTicker(items) {
  const map = new Map();
  items.forEach((item) => {
    const normalized = normalizeSearchItem(item);
    if (normalized && !map.has(normalized.ticker)) map.set(normalized.ticker, normalized);
  });
  return [...map.values()];
}

async function loadSearchUniverse() {
  try {
    const payload = await api.watchlist();
    let watchlist = [];
    if (payload?.groups) {
      for (const g of payload.groups) watchlist.push(...(g.stocks || []));
    } else {
      watchlist = Array.isArray(payload) ? payload : (payload?.watchlist ?? payload?.items ?? payload?.data ?? payload?.stocks ?? []);
    }
    const universe = uniqueByTicker([...watchlist, ...FALLBACK_SYMBOLS]);
    return universe.length ? universe : FALLBACK_SYMBOLS;
  } catch (error) {
    console.warn('Search universe load failed; using fallback symbols.', error);
    return FALLBACK_SYMBOLS;
  }
}

function filterUniverse(universe, query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  return universe
    .filter((item) => item.ticker.toLowerCase().includes(needle) || item.company.toLowerCase().includes(needle) || item.sector.toLowerCase().includes(needle))
    .slice(0, 8);
}

export function navigateSearchResult(ticker) {
  const symbol = String(ticker || '').trim().toUpperCase();
  if (!symbol) return;
  window.location.hash = `#detail/${encodeURIComponent(symbol)}`;
}

export function bindSearchNavigation(root = document) {
  root.querySelectorAll('[data-search-ticker]').forEach((result) => {
    result.addEventListener('click', () => navigateSearchResult(result.dataset.searchTicker));
  });
}

export function initSearch(root = document) {
  const input = root.getElementById?.('global-search') ?? root.querySelector?.('#global-search');
  const dropdown = root.getElementById?.('global-search-results') ?? root.querySelector?.('#global-search-results');
  if (!input || !dropdown || input.dataset.searchBound === 'true') return;

  input.dataset.searchBound = 'true';

  let universePromise = null;
  let universeRequestedAt = 0;
  const ensureUniverse = () => {
    const expired = universeRequestedAt > 0 && Date.now() - universeRequestedAt >= SEARCH_UNIVERSE_TTL_MS;
    if (!universePromise || expired) {
      universeRequestedAt = Date.now();
      universePromise = loadSearchUniverse();
    }
    return universePromise;
  };

  const closeDropdown = () => {
    dropdown.hidden = true;
    dropdown.innerHTML = '';
    input.setAttribute('aria-expanded', 'false');
  };

  const renderResults = (results, query) => {
    if (!query.trim()) return closeDropdown();
    dropdown.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    if (!results.length) {
      dropdown.innerHTML = `<button class="search-result-item" type="button" data-search-ticker="${escapeHtml(query)}" role="option"><span><strong class="mono font-data-mono" data-numeric>${escapeHtml(query.toUpperCase())}</strong><small>打开自定义代码</small></span><em>↵</em></button>`;
    } else {
      dropdown.innerHTML = results.map((item) => `
        <button class="search-result-item" type="button" data-search-ticker="${escapeHtml(item.ticker)}" role="option">
          <span><strong class="mono font-data-mono" data-numeric>${escapeHtml(item.ticker)}</strong><small>${escapeHtml(item.company)}</small></span>
          <em class="label-caps">${escapeHtml(item.sector)}</em>
        </button>
      `).join('');
    }
    bindSearchNavigation(dropdown);
  };

  input.addEventListener('focus', () => { ensureUniverse(); });

  input.addEventListener('input', async () => {
    const query = input.value;
    const universe = await ensureUniverse();
    if (query !== input.value) return;
    renderResults(filterUniverse(universe, query), query);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeDropdown();
    if (event.key === 'Enter' && input.value.trim()) navigateSearchResult(input.value.trim());
  });

  document.addEventListener('click', (event) => {
    if (!event.target.closest('.sidebar-search')) closeDropdown();
  });

  window.addEventListener('hashchange', () => {
    input.value = '';
    closeDropdown();
  });
}
