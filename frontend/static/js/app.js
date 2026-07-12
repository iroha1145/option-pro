import { mountMotion } from './components/motion.js';

const routeLoaders = {
  watchlist: (url) => import(url).then((module) => module.renderWatchlist),
  sectors: (url) => import(url).then((module) => module.renderSectors),
  earnings: (url) => import(url).then((module) => module.renderEarnings),
  screener: (url) => import(url).then((module) => module.renderScreener),
  breakouts: (url) => import(url).then((module) => module.renderBreakouts),
  detail: (url) => import(url).then((module) => module.mountDetail),
};
const routeModulePaths = {
  watchlist: './pages/watchlist.js',
  sectors: './pages/sectors.js',
  earnings: './pages/earnings.js',
  screener: './pages/screener.js',
  breakouts: './pages/breakouts.js',
  detail: './pages/detail.js',
};
const routePromises = new Map();
const routeImportAttempts = new Map();
const validRoutes = new Set(Object.keys(routeLoaders));

const routeTitles = {
  watchlist: '自选',
  sectors: '板块',
  earnings: '财报',
  screener: '选股',
  breakouts: '突破雷达',
  detail: '标的详情'
};

function parseHash() {
  const [route = 'watchlist', encodedTicker = ''] = window.location.hash.replace(/^#/, '').split('/');
  let ticker = '';
  try {
    const decoded = decodeURIComponent(encodedTicker || '').trim().toUpperCase();
    // Yahoo-style symbols used by the app only need letters, numbers and a
    // small set of punctuation. Reject malformed/injected hashes early.
    if (/^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$/.test(decoded)) ticker = decoded;
  } catch (_) {
    // A malformed percent-escape must not break the whole router.
  }
  return {
    route: validRoutes.has(route) ? route : 'watchlist',
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
  try {
    stored = sessionStorage.getItem('optixPreviousListRoute')
      || sessionStorage.getItem('ethosPreviousListRoute')
      || stored;
  } catch (_) { /* storage unavailable */ }
  const candidate = stored.replace(/^#/, '').split('/')[0];
  return validRoutes.has(candidate) && candidate !== 'detail' ? `#${candidate}` : '#watchlist';
}

function setActiveNav(route) {
  const activeRoute = getActiveSidebarRoute(route);
  document.querySelectorAll('.sidebar-link').forEach((link) => {
    const isActive = link.dataset.route === activeRoute;
    link.classList.toggle('active', isActive);
    if (isActive) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

function setSidebarOpen(isOpen) {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('sidebar-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  const isMobile = window.matchMedia('(max-width: 767px)').matches;
  document.body.classList.toggle('sidebar-open', isOpen);
  sidebar?.classList.toggle('is-open', isOpen);
  toggle?.setAttribute('aria-expanded', String(isOpen));
  toggle?.setAttribute('aria-label', isOpen ? '关闭导航' : '打开导航');
  if (backdrop) backdrop.hidden = !isOpen;
  if (isOpen && isMobile) {
    setTimeout(() => sidebar?.querySelector('.sidebar-link, input, button')?.focus(), 180);
  } else if (!isOpen && isMobile && sidebar?.contains(document.activeElement)) {
    toggle?.focus();
  }
}

function initResponsiveSidebar() {
  const toggle = document.getElementById('sidebar-toggle');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!toggle || toggle.dataset.sidebarBound === 'true') return;
  toggle.dataset.sidebarBound = 'true';
  toggle.addEventListener('click', () => setSidebarOpen(!document.body.classList.contains('sidebar-open')));
  backdrop?.addEventListener('click', () => setSidebarOpen(false));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
      event.preventDefault();
      setSidebarOpen(false);
    }
  });
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
    <section class="page-placeholder surface" aria-live="polite">
      <span class="page-kicker">${eyebrow}</span>
      <h1>${routeTitles[route]}</h1>
      <p>${copy}</p>
    </section>
  `;
}

function loadRoute(route) {
  if (!routePromises.has(route)) {
    const attempt = routeImportAttempts.get(route) || 0;
    routeImportAttempts.set(route, attempt + 1);
    routePromises.set(route, routeLoaders[route](retryableModuleUrl(routeModulePaths[route], attempt)));
  }
  return routePromises.get(route);
}

let __tickerMounted = false;
let __searchMounted = false;
let __tickerImportAttempt = 0;
let __searchImportAttempt = 0;
let __routeGeneration = 0;
let __shellInteractionsBound = false;
let __clockTimer = null;
let __disposePageMotion = null;

function retryableModuleUrl(path, attempt) {
  return attempt === 0 ? path : `${path}?retry=${attempt}`;
}

function updateWorkspaceClock() {
  const clock = document.getElementById('workspace-clock');
  if (!clock) return;
  const now = new Date();
  clock.dateTime = now.toISOString();
  clock.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  clock.title = now.toLocaleString([], { dateStyle: 'long', timeStyle: 'short' });
}

function initShellInteractions() {
  updateWorkspaceClock();
  if (!__clockTimer) __clockTimer = setInterval(updateWorkspaceClock, 30 * 1000);
  if (__shellInteractionsBound) return;
  __shellInteractionsBound = true;
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      const input = document.getElementById('global-search');
      const focusSearch = () => {
        input?.focus();
        input?.select();
      };
      if (window.matchMedia('(max-width: 767px)').matches) {
        setSidebarOpen(true);
        setTimeout(focusSearch, 180);
      } else {
        focusSearch();
      }
    }
  });
}

function initGlobalShell() {
  initShellInteractions();
  if (!__searchMounted) {
    __searchMounted = true;
    const attempt = __searchImportAttempt++;
    import(retryableModuleUrl('./components/search.js', attempt))
      .then(({ initSearch }) => initSearch())
      .catch((error) => {
        __searchMounted = false;
        console.warn('Search initialization failed.', error);
      });
  }
  if (!__tickerMounted) {
    __tickerMounted = true;
    const attempt = __tickerImportAttempt++;
    import(retryableModuleUrl('./components/indices.js', attempt))
      .then(({ mountIndexTicker }) => mountIndexTicker())
      .catch((error) => {
        __tickerMounted = false;
        console.warn('Index ticker initialization failed.', error);
      });
  }
}

export async function router(event) {
  const generation = ++__routeGeneration;
  try { __disposePageMotion?.(); } catch (_) { /* motion is optional */ }
  __disposePageMotion = null;
  initResponsiveSidebar();
  initGlobalShell();
  const route = getRouteFromHash();
  document.body.dataset.route = route;
  const rawRoute = window.location.hash.replace(/^#/, '').split('/')[0];
  if (!window.location.hash || !validRoutes.has(rawRoute)) {
    history.replaceState(null, '', '#watchlist');
  }
  if (route !== 'detail') {
    try { sessionStorage.setItem('optixPreviousListRoute', window.location.hash || '#watchlist'); } catch (_) { /* storage unavailable */ }
  }
  setActiveNav(route);
  const app = document.getElementById('app');
  app?.setAttribute('aria-busy', 'true');
  if (!routePromises.has(route)) renderShellPage(route, '加载中', '正在载入页面…');

  try {
    const render = await loadRoute(route);
    if (generation !== __routeGeneration || getRouteFromHash() !== route) return;
    if (route === 'detail') {
      const { ticker } = parseHash();
      if (!ticker) {
        window.location.hash = getPreviousListRoute();
        return;
      }
      await render(ticker, { backRoute: getPreviousListRoute() });
    } else {
      await render();
    }
  } catch (error) {
    routePromises.delete(route);
    if (generation === __routeGeneration) {
      console.error(`Route ${route} failed to load.`, error);
      renderShellPage(route, '加载失败', '页面资源暂时无法载入，请刷新后重试。');
    }
  } finally {
    if (generation === __routeGeneration) {
      app?.setAttribute('aria-busy', 'false');
      if (event?.type === 'hashchange') {
        window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
        app?.focus({ preventScroll: true });
      }
      try {
        __disposePageMotion = mountMotion(app);
      } catch (_) {
        __disposePageMotion = null;
      }
    }
  }
}

window.addEventListener('hashchange', router);
window.addEventListener('DOMContentLoaded', router);
