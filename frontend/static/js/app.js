import { renderWatchlist } from './pages/watchlist.js';
import { mountDetail } from './pages/detail.js';
import { renderSectors } from './pages/sectors.js';
import { renderEarnings } from './pages/earnings.js';
import { renderScreener } from './pages/screener.js';
import { initSearch } from './components/search.js';
import { mountIndexTicker } from './components/indices.js';

const routes = {
  watchlist: renderWatchlist,
  sectors: renderSectors,
  earnings: renderEarnings,
  screener: renderScreener,
  detail: renderDetailRoute
};

const routeTitles = {
  watchlist: '总览',
  sectors: '板块',
  earnings: '财报',
  screener: '选股'
};

function parseHash() {
  const [route = 'watchlist', encodedTicker = ''] = window.location.hash.replace(/^#/, '').split('/');
  let ticker = '';
  try {
    const decoded = decodeURIComponent(encodedTicker || '').trim().toUpperCase();
    // Yahoo-style symbols used by the app only need letters, numbers and a
    // small set of punctuation. Reject malformed/injected hashes early.
    if (/^[A-Z0-9.^_=-]{1,32}$/.test(decoded)) ticker = decoded;
  } catch (_) {
    // A malformed percent-escape must not break the whole router.
  }
  return {
    route: routes[route] ? route : 'watchlist',
    ticker
  };
}

function getRouteFromHash() {
  return parseHash().route;
}

function getActiveSidebarRoute(route) {
  if (route !== 'detail') return route;
  const previous = getPreviousListRoute();
  return previous.replace('#', '');
}

export function getPreviousListRoute() {
  let stored = '#watchlist';
  try { stored = sessionStorage.getItem('ethosPreviousListRoute') || stored; } catch (_) { /* storage unavailable */ }
  const candidate = stored.replace(/^#/, '').split('/')[0];
  return routes[candidate] && candidate !== 'detail' ? `#${candidate}` : '#watchlist';
}

function setActiveNav(route) {
  const activeRoute = getActiveSidebarRoute(route);
  document.querySelectorAll('.sidebar-link').forEach((link) => {
    link.classList.toggle('active', link.dataset.route === activeRoute);
  });
}

function setSidebarOpen(isOpen) {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('sidebar-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  document.body.classList.toggle('sidebar-open', isOpen);
  sidebar?.classList.toggle('is-open', isOpen);
  toggle?.setAttribute('aria-expanded', String(isOpen));
  toggle?.setAttribute('aria-label', isOpen ? '关闭导航' : '打开导航');
  if (backdrop) backdrop.hidden = !isOpen;
}

function initResponsiveSidebar() {
  const toggle = document.getElementById('sidebar-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!toggle || toggle.dataset.sidebarBound === 'true') return;
  toggle.dataset.sidebarBound = 'true';
  toggle.addEventListener('click', () => setSidebarOpen(!document.body.classList.contains('sidebar-open')));
  backdrop?.addEventListener('click', () => setSidebarOpen(false));
  document.querySelectorAll('.sidebar-link, .sidebar-logo').forEach((link) => {
    link.addEventListener('click', () => setSidebarOpen(false));
  });
  window.addEventListener('resize', () => {
    if (window.matchMedia('(min-width: 768px)').matches) setSidebarOpen(false);
  });
}

function renderShellPage(route, eyebrow, copy) {
  const app = document.getElementById('app');
  if (!app) return;

  app.innerHTML = `
    <section class="page-placeholder glass-panel">
      <span class="mono font-data-mono" style="color: var(--color-muted); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;">${eyebrow}</span>
      <h1>${routeTitles[route]}</h1>
      <p>${copy}</p>
    </section>
  `;
}

function renderDetailRoute() {
  const { ticker } = parseHash();
  if (!ticker) {
    window.location.hash = getPreviousListRoute();
    return;
  }
  mountDetail(ticker, { backRoute: getPreviousListRoute() });
}

let __tickerMounted = false;
export function router() {
  initSearch();
  initResponsiveSidebar();
  if (!__tickerMounted) { __tickerMounted = true; mountIndexTicker().catch(() => {}); }
  const route = getRouteFromHash();
  const rawRoute = window.location.hash.replace(/^#/, '').split('/')[0];
  if (!window.location.hash || !routes[rawRoute]) {
    history.replaceState(null, '', '#watchlist');
  }
  if (route !== 'detail') {
    try { sessionStorage.setItem('ethosPreviousListRoute', window.location.hash || '#watchlist'); } catch (_) { /* storage unavailable */ }
  }
  setActiveNav(route);
  routes[route]();
}

window.addEventListener('hashchange', router);
window.addEventListener('DOMContentLoaded', router);
