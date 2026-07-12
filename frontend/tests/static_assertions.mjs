import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '..');
const read = (relativePath) => readFile(path.join(frontend, relativePath), 'utf8');

async function readJavaScriptTree(relativeDirectory) {
  const directory = path.join(frontend, relativeDirectory);
  const entries = await readdir(directory, { withFileTypes: true });
  const chunks = [];
  for (const entry of entries) {
    const relativePath = path.join(relativeDirectory, entry.name);
    if (entry.isDirectory()) chunks.push(await readJavaScriptTree(relativePath));
    if (entry.isFile() && entry.name.endsWith('.js')) chunks.push(await read(relativePath));
  }
  return chunks.join('\n');
}

const [
  index,
  coreV3,
  watchlistV3,
  screenerV3,
  detailV3,
  sectorsV3,
  earningsV3,
  breakoutsV3,
  app,
  motion,
  marketStrengthPlaceholder,
  api,
  watchlist,
  screener,
  detail,
  sectors,
  earnings,
  breakouts,
  chart,
  indices,
  search,
  customWatchlist,
  heatmap,
  optionChain,
  icons,
  javaScriptBundle,
] = await Promise.all([
  read('index.html'),
  read('static/css/optix-core-v3.css'),
  read('static/css/optix-watchlist-v3.css'),
  read('static/css/optix-screener-v3.css'),
  read('static/css/optix-detail-v3.css'),
  read('static/css/optix-sectors-v3.css'),
  read('static/css/optix-earnings-v3.css'),
  read('static/css/optix-breakouts-v3.css'),
  read('static/js/app.js'),
  read('static/js/components/motion.js'),
  read('static/js/components/marketStrengthPlaceholder.js'),
  read('static/js/api.js'),
  read('static/js/pages/watchlist.js'),
  read('static/js/pages/screener.js'),
  read('static/js/pages/detail.js'),
  read('static/js/pages/sectors.js'),
  read('static/js/pages/earnings.js'),
  read('static/js/pages/breakouts.js'),
  read('static/js/components/chart.js'),
  read('static/js/components/indices.js'),
  read('static/js/components/search.js'),
  read('static/js/components/customWatchlist.js'),
  read('static/js/components/heatmap.js'),
  read('static/js/components/optionChain.js'),
  read('static/icons.svg'),
  readJavaScriptTree('static/js'),
]);

await read('static/favicon.svg');

const v3Styles = [coreV3, watchlistV3, screenerV3, detailV3, sectorsV3, earningsV3, breakoutsV3].join('\n');
const activeBundle = [index, v3Styles, javaScriptBundle, icons].join('\n');
const oldStyles = /(?:^|[/'"])(?:styles\.css|optix-next\.css|optix-research-next\.css|optix-sectors-next\.css|optix-earnings-next\.css)(?:[?'"#]|$)/m;

// Active production code must be self-contained and must not revive retired styles.
assert.doesNotMatch(activeBundle, /fonts\.(?:googleapis|gstatic)\.com|use\.typekit\.net|use\.fontawesome\.com/i, 'external font hosts must stay removed');
assert.doesNotMatch(activeBundle, /material-symbols|Material Symbols/i, 'font-backed icons must stay removed');
assert.doesNotMatch(v3Styles, /@import\s+(?:url\()?\s*['"]?https?:\/\//i, 'v3 styles must not import remote assets');
assert.doesNotMatch(activeBundle, oldStyles, 'active files must not reference retired stylesheets');
assert.match(index, /font-src 'self'/, 'the content security policy must keep fonts local');

const linkedStyles = [...index.matchAll(/<link\s+rel="stylesheet"\s+href="([^"]+)"/g)]
  .map((match) => match[1].split('?')[0]);
assert.deepEqual(linkedStyles, [
  './static/css/optix-deck.css',
], 'the deck shell loads a single self-contained stylesheet');

// Deck shell scripts: pre-paint theme init, then data layer, then renderers.
assert.match(index, /<script src="\.\/static\/js\/theme-init\.js[^"]*"><\/script>[\s\S]*<link rel="stylesheet"/, 'theme must initialize before first paint');
assert.match(index, /deck-api\.js[\s\S]*deck-app\.js/, 'the data layer must load before the renderers');
assert.doesNotMatch(index, /<script>(?!<)/, 'the shell must not carry inline scripts under CSP script-src self');

// Core Optix Pro shell.
assert.match(index, /<title>Optix Pro · 个人投资研究<\/title>/, 'the product name must stay unified');
assert.match(index, /rel="icon" href="\.\/static\/favicon\.svg"/, 'the local favicon must be linked');
assert.match(index, /class="deck-header"[\s\S]*class="tape"/, 'the deck shell pairs navigation with the index tape');
assert.match(index, /id="search-toggle"[\s\S]*id="palette-input"/, 'global search opens the command palette');
assert.match(index, /id="clock-bj"[\s\S]*id="market-phase"/, 'the shell must expose workspace time and market phase');
assert.match(index, /id="index-tape"/, 'the shell must retain the market index strip');
assert.match(index, /id="tape-updated"/, 'index freshness must remain visible next to the tape');
assert.match(index, /id="theme-toggle"/, 'the night/day toggle must live in the header');
assert.match(index, /id="drawer"[^>]*role="dialog"[^>]*aria-modal="true"/, 'research drawers need dialog semantics');
assert.match(indices, /document\.getElementById\('index-ticker-updated'\)/, 'index updates need an independent timestamp target');
assert.match(indices, /updated\.dateTime = refreshedAt\.toISOString\(\)/, 'ticker freshness needs a machine-readable timestamp');
assert.match(indices, /<a class="ticker-item" href="#detail\/\$\{encodeURIComponent\(symbol\)\}"/, 'each market index must use a native detail link');
assert.doesNotMatch(indices, /bindClicks|location\.hash = `#detail/, 'market index links must not depend on scripted navigation');
assert.match(indices, /'S&P 500': '\^GSPC'[\s\S]*NASDAQ: '\^IXIC'[\s\S]*DOW: '\^DJI'[\s\S]*'日经 225': '\^N225'[\s\S]*'上证综指': '000001\.SS'/, 'the five headline indices need safe detail ticker mappings');
assert.match(coreV3, /\.ticker-bar\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/, 'the ticker needs a stable timestamp column');
assert.match(coreV3, /\.ticker-updated\s*\{[^}]*align-items:\s*center;[^}]*border-left:/, 'the ticker timestamp needs centered alignment and a clear divider');
assert.match(coreV3, /\.ticker-track\s*\{[^}]*--ticker-items-per-view:\s*5;[^}]*scroll-snap-type:\s*x mandatory;/, 'wide screens should show five complete snapping index items');
assert.match(coreV3, /\.ticker-item\s*\{[^}]*flex:\s*0 0 calc\(100% \/ var\(--ticker-items-per-view\)\);[^}]*scroll-snap-stop:\s*always;/, 'index items must occupy an exact viewport fraction without partial clipping');
assert.match(coreV3, /@media \(max-width: 1180px\)[\s\S]*--ticker-items-per-view:\s*3;/, 'narrow desktops should show three complete index items');
assert.match(coreV3, /@media \(max-width: 860px\)[\s\S]*--ticker-items-per-view:\s*2;/, 'tablets should show two complete index items');
assert.match(coreV3, /@media \(max-width: 640px\)[\s\S]*--ticker-items-per-view:\s*1;/, 'phones should show one complete index item');
assert.match(coreV3, /\.ticker-item:focus-visible/, 'market index links need a visible keyboard focus state');
assert.match(index, /<main id="view"[^>]+tabindex="-1"/, 'route changes need a focusable main region');
assert.match(index, /class="dock"/, 'mobile navigation must remain directly available');
assert.match(index, /data-route="watchlist"[\s\S]*data-route="screener"[\s\S]*data-route="sectors"/, 'the primary navigation should keep screening near the front');
assert.match(index, /href="#breakouts"[^>]+data-route="breakouts"[\s\S]*突破雷达/, 'desktop navigation must expose Breakout Radar');
assert.match(index, /class="dock"[\s\S]*href="#breakouts"[^>]+data-route="breakouts"[\s\S]*>雷达</, 'mobile navigation must expose the radar route');
assert.match(app, /breakouts:[\s\S]*renderBreakouts[\s\S]*\.\/pages\/breakouts\.js/, 'the app router must lazy-load Breakout Radar');
assert.match(coreV3, /@media \(max-width: 860px\)[\s\S]*\.sidebar-nav\s*\{[^}]*grid-template-columns:\s*repeat\(5, minmax\(0, 1fr\)\)/, 'tablet navigation must fit all five primary routes');
assert.match(coreV3, /@media \(max-width: 640px\)[\s\S]*\.mobile-dock\s*\{[^}]*grid-template-columns:\s*repeat\(5, 1fr\)/, 'mobile dock must fit all five primary routes');

for (const token of [
  'canvas', 'surface', 'surface-soft', 'ink', 'ink-soft', 'muted', 'line',
  'accent', 'accent-soft', 'positive', 'negative', 'warning',
  'radius-sm', 'radius-md', 'radius-lg', 'shadow-soft', 'font-display', 'font-sans', 'font-compact', 'font-data',
  'glass-fill', 'glass-fill-strong', 'glass-fill-accent', 'glass-stroke',
  'glass-edge', 'glass-shadow', 'glass-shadow-float', 'glass-blur', 'glass-blur-float', 'glass-blur-mobile',
]) {
  assert.match(coreV3, new RegExp(`--${token}:`), `missing v3 design token: ${token}`);
}
assert.match(coreV3, /body\.optix-app/, 'core styles must be anchored to the v3 shell');
assert.match(coreV3, /--font-sans:\s*-apple-system,[^;]*"Segoe UI Variable Text"[^;]*Roboto/, 'the default UI stack should resolve to SF Pro on Apple and native text fonts elsewhere');
assert.match(coreV3, /--font-compact:\s*-apple-system,[^;]*"Segoe UI Variable Text"[^;]*Roboto/, 'small UI text needs a separate compact system stack');
assert.doesNotMatch(v3Styles, /@font-face\s*\{[^}]*font-family:\s*["']?SF (?:Pro|Compact)/i, 'restricted Apple font files must not be embedded in the web bundle');
assert.match(coreV3, /\.market-status-chip\s*\{[^}]*font-family:\s*var\(--font-compact\);[^}]*font-weight:\s*520;/, 'compact market status text should remain readable');
assert.match(coreV3, /:focus-visible/, 'the shell needs visible keyboard focus');
assert.match(coreV3, /@media \(pointer: coarse\)[\s\S]*min-height: 44px/, 'touch controls need reliable targets');
assert.match(coreV3, /@media \(prefers-reduced-motion: reduce\)/, 'the shell must respect reduced motion');
assert.match(coreV3, /@media \(max-width: 640px\)[\s\S]*\.mobile-dock/, 'the mobile shell must reveal the bottom dock');

// Selective glass: solid by default, enhanced only where supported, and safe
// for reduced-transparency, high-contrast, and forced-color environments.
assert.match(coreV3, /--glass-fill:\s*var\(--surface\)/, 'glass surfaces need an opaque default fill');
assert.match(coreV3, /--glass-blur:\s*0px/, 'glass blur must default to a no-blur fallback');
assert.match(coreV3, /@supports \(\(-webkit-backdrop-filter: blur\(1px\)\) or \(backdrop-filter: blur\(1px\)\)\)/, 'glass must be gated by feature support');
assert.match(coreV3, /@media \(prefers-reduced-transparency: reduce\)/, 'glass must respect reduced transparency');
assert.match(coreV3, /@media \(prefers-contrast: more\)/, 'glass must provide a higher-contrast fallback');
assert.match(coreV3, /@media \(forced-colors: active\)/, 'glass must provide a forced-colors fallback');
assert.match(coreV3, /@media \(max-width: 640px\)[\s\S]*--glass-blur:\s*0px[\s\S]*\.mobile-dock[\s\S]*backdrop-filter: blur\(var\(--glass-blur-mobile\)\)/, 'phones should reserve blur for the mobile dock');
assert.match(coreV3, /@supports \(\(-webkit-backdrop-filter: blur\(1px\)\) or \(backdrop-filter: blur\(1px\)\)\)[\s\S]*\.app-header__frame\s*\{[^}]*backdrop-filter: blur\(28px\)/, 'the supported shell should use a restrained smoked-glass chamber');
assert.match(screenerV3, /\.screening-page \.screening-controls\s*\{[^}]*backdrop-filter:/, 'the screener filter lens should carry the primary glass treatment');
assert.doesNotMatch(screenerV3, /\.screening-page \.screening-candidate-summary\s*\{[^}]*backdrop-filter:/, 'candidate rows must remain crisp data surfaces');
assert.doesNotMatch(watchlistV3, /\.focus-panel\s*\{[^}]*backdrop-filter:/, 'the focused quote must stay a crisp data surface');
assert.doesNotMatch(watchlistV3, /\.watchlist-context-row\s*\{[^}]*backdrop-filter:/, 'watchlist context must not add a third persistent blur layer');
assert.doesNotMatch(watchlistV3, /\.stock-card\s*\{[^}]*backdrop-filter:\s*blur/, 'watchlist data cards must remain crisp surfaces');
assert.doesNotMatch(detailV3, /\.instrument-page \.instrument-quote\s*\{[^}]*backdrop-filter:/, 'the detail hero must remain a crisp price surface');
assert.doesNotMatch(detailV3, /\.market-chart canvas\s*\{[^}]*backdrop-filter:/, 'chart canvases must never be blurred');
assert.doesNotMatch(sectorsV3, /\.sector-page__header\s*\{[^}]*backdrop-filter:/, 'the sector page header must remain a crisp surface');
assert.doesNotMatch(sectorsV3, /\.heatmap-tile\s*\{[^}]*backdrop-filter:/, 'heatmap data tiles must never be blurred');
assert.doesNotMatch(earningsV3, /\.earnings-page__header\s*\{[^}]*backdrop-filter:/, 'the earnings page header must remain a crisp surface');
assert.doesNotMatch(earningsV3, /\.earnings-event\s*\{[^}]*backdrop-filter:\s*blur/, 'earnings data cards must remain crisp surfaces');

// Purposeful motion: one-shot optical sweeps and viewport reveals, never on charts.
assert.match(app, /import \{ mountMotion \} from '\.\/components\/motion\.js'/, 'the router must mount the shared motion controller');
assert.match(app, /__disposePageMotion\?\.\(\)/, 'route changes must dispose the previous motion lifecycle');
assert.match(app, /__disposePageMotion = mountMotion\(app\)/, 'motion must mount only after the active route renders');
assert.match(motion, /MAX_STAGGERED_ITEMS = 8/, 'large lists must cap decorative stagger work');
assert.match(motion, /new IntersectionObserver/, 'reveals should be scheduled only when content enters the viewport');
assert.match(motion, /new MutationObserver/, 'late data cards must join the route motion lifecycle');
assert.match(motion, /seenItems = new Set\(\)[\s\S]*stableMotionKey/, 'stable keys must prevent repeated card entrances');
assert.match(motion, /navigator\.connection \|\| navigator\.mozConnection[\s\S]*saveData/, 'data-saving mode must disable decorative motion');
assert.match(motion, /prefers-reduced-motion: reduce/, 'the controller must respect reduced-motion settings');
assert.match(motion, /CHART_SELECTOR = '[^']*canvas[^']*data-motion-static/, 'chart surfaces must be excluded from route effects');
assert.match(motion, /window\.requestAnimationFrame\(flushSpotlight\)/, 'pointer spotlights must be frame-throttled');
assert.match(coreV3, /\[data-motion-lens\]\.is-lens-sweeping::after[\s\S]*optix-lens-sweep/, 'the optical lens should be a one-shot transform effect');
assert.match(coreV3, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\[data-motion-lens\]::after\s*\{[^}]*display:\s*none;/, 'reduced motion must remove the lens sweep entirely');

for (const route of ['watchlist', 'sectors', 'earnings', 'screener', 'detail']) {
  assert.match(app, new RegExp(`${route}:\\s*\\(url\\)\\s*=>\\s*import\\(url\\)`), `${route} must use a lazy route loader`);
  assert.match(app, new RegExp(`${route}: '\\.\\/pages\\/${route}\\.js'`), `${route} module path must stay explicit`);
}
assert.doesNotMatch(app, /from '\.\/pages\//, 'page modules must not be eagerly imported');
assert.match(app, /\?retry=\$\{attempt\}/, 'failed module imports need a fresh retry URL');
assert.match(app, /routePromises\.delete\(route\)/, 'a failed route import must be retryable');
assert.match(app, /setAttribute\('aria-current', 'page'\)/, 'active navigation needs an accessible current-page state');
assert.match(app, /event\.metaKey \|\| event\.ctrlKey/, 'global search needs its keyboard shortcut');
assert.match(app, /clock\.dateTime = now\.toISOString\(\)/, 'the visible clock needs machine-readable time');
assert.match(app, /event\?\.type === 'hashchange'[\s\S]*focus\(\{ preventScroll: true \}\)/, 'route changes must move focus into main content');
assert.match(app, /event\?\.type === 'hashchange'[\s\S]*scrollTo\(\{ top: 0, left: 0, behavior: 'auto' \}\)/, 'a new route must start at the top of its own page');

for (const iconName of [
  'dashboard', 'layers', 'event_note', 'filter_alt', 'search', 'radar',
  'show_chart', 'candlestick_chart', 'chevron_left', 'chevron_right',
  'arrow_up_right', 'refresh', 'sliders', 'close', 'edit', 'check',
]) {
  assert.match(icons, new RegExp(`id="icon-${iconName}"`), `missing local SVG icon: ${iconName}`);
}

// Watchlist: honest freshness, safe snapshots, and direct responsive quote cards.
assert.match(watchlist, /class="watchlist-page"/, 'watchlist needs its v3 page root');
assert.match(watchlistV3, /^\.watchlist-page\s*\{/m, 'watchlist styles must start from their page root');
// v3 页面样式不再由壳加载;v3 模块作为归档保留,断言继续守护其内部约定。
assert.match(watchlist, /WATCHLIST_REFRESH_MS = 5 \* 60 \* 1000/, 'watchlist refresh must stay aligned to five minutes');
assert.match(watchlist, /payload\?\.as_of \|\| payload\?\.fetched_at/, 'watchlist must display backend freshness');
assert.match(watchlist, /WATCHLIST_SNAPSHOT_KEY = 'optix\.watchlist\.snapshot\.v2'/, 'only the versioned real-data snapshot may persist');
assert.match(watchlist, /WATCHLIST_SNAPSHOT_MAX_AGE_MS = 7 \* 24 \* 60 \* 60 \* 1000/, 'expired prices must not remain indefinitely');
assert.match(watchlist, /incomingByTicker[\s\S]*previousByTicker/, 'partial updates must preserve temporarily missing symbols');
assert.match(watchlist, /containsPreviousPrices: usedPreviousPrices/, 'merged snapshots must record reused prices');
assert.match(watchlist, /含上次价格/, 'reused prices must be labelled honestly');
assert.match(watchlist, /class="stock-card quote-row/, 'watchlist items must use the v3 quote-row structure');
assert.match(watchlist, /class="stock-card__link" href="#detail\/\$\{encodeURIComponent\(stock\.ticker\)\}"/, 'the whole watchlist card must use a native detail link');
assert.doesNotMatch(watchlist, /stock-card__open|data-open-ticker|data-focus-ticker/, 'watchlist cards must not retain duplicate open or focus buttons');
assert.match(watchlist, /data-view="cards"[^>]*role="list"/, 'card layout must be the only default watchlist presentation');
assert.doesNotMatch(watchlist, /data-watchlist-view|optix\.watchlist\.view/, 'the retired list-grid switch must not override the card layout');
assert.match(watchlist, /class="terminal-earnings-card" href="#detail\/[\s\S]*<time datetime=/, 'upcoming earnings need full-date clickable cards');
assert.match(watchlist, /id="watchlist-spotlight"/, 'watchlist needs a focused-stock summary');
assert.match(watchlist, /id="watchlist-spotlight"[^>]*data-motion-lens/, 'the focused quote should carry the page signature sweep');
assert.doesNotMatch(watchlist, /class="stock-card[^>]*data-motion-lens/, 'ordinary watchlist cards must not all run lens sweeps');
assert.match(watchlist, /data-watchlist-filter="up"/, 'quote direction filters must remain interactive');
assert.match(watchlist, /for="watchlist-add-input"/, 'custom ticker entry needs a visible label');
assert.match(watchlist, /WATCHLIST_PAGE_SIZE = 24/, 'large watchlists must render in batches');
assert.doesNotMatch(watchlist, /[?&](?:demo|mock)=/, 'production watchlist code must not expose fake-data switches');
assert.match(watchlistV3, /\.watchlist-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/, 'watchlist cards need a calm two-column desktop grid');
assert.match(watchlistV3, /@media \(max-width: 680px\)[\s\S]*\.watchlist-grid\s*\{[^}]*grid-template-columns:\s*1fr;/, 'mobile watchlist cards must collapse to one column');
assert.match(watchlistV3, /\.stock-card__link:focus-visible/, 'whole-card links need a visible keyboard focus');
assert.match(watchlistV3, /\.watchlist-heading > p\s*\{[^}]*font-family:\s*var\(--font-compact\);[^}]*font-weight:\s*520;/, 'watchlist summary text should use the readable compact role');
assert.match(watchlistV3, /\.watchlist-freshness,\s*\.watchlist-delay-note\s*\{[^}]*font-weight:\s*560;/, 'watchlist freshness metadata should not fall back to a thin regular weight');
assert.match(watchlistV3, /\.terminal-earnings-date time\s*\{[^}]*font-family:\s*var\(--font-compact\);[^}]*font-weight:\s*560;/, 'upcoming earnings dates need a readable compact weight');
assert.match(watchlist, /sparkline-baseline[\s\S]*sparkline-endpoint[\s\S]*>7日<\/text>/, 'watchlist mini charts need a baseline, endpoint, and explicit period');
assert.match(watchlistV3, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.stock-card/, 'watchlist card motion must respect user preferences');
assert.doesNotMatch(watchlistV3, /\.terminal-earnings-date\s+(?:small|time)\s*\{[^}]*text-overflow:\s*ellipsis/, 'earnings dates must not be ellipsized');
assert.match(api, /api\.watchlist|watchlist\(options = \{\}\)/, 'the frontend API must expose watchlist data');
assert.match(customWatchlist, /return TICKER_PATTERN\.test\(ticker\) \? ticker : ''/, 'invalid custom tickers must be rejected before requesting data');

// Screener: low-density decision cards with evidence disclosed on demand.
assert.match(screener, /optix-screener-v3\.css/, 'screener must load its v3 stylesheet');
assert.match(screener, /class="screening-page"/, 'screener needs its isolated page root');
assert.match(screener, /renderControls\(sectors\)[\s\S]*renderMarketContext\(payload\)[\s\S]*renderResults\(payload\)/, 'screener reading order must be settings, context, then candidates');
assert.match(screener, /screening-primary-reason/, 'each default candidate row needs one primary reason');
assert.match(screener, /data-result-toggle/, 'candidate evidence must remain progressively disclosed');
assert.match(screener, /class="screening-candidate-link" href="\$\{detailHref\}"/, 'candidate cards must use native detail links');
assert.doesNotMatch(screener, /data-result-open|navigateToDetail|screening-open-research/, 'candidate cards must not retain duplicate scripted detail buttons');
assert.match(screener, /class="screening-candidate-detail" role="region" aria-labelledby="\$\{toggleId\}"/, 'expanded evidence needs a labelled region');
assert.match(screener, /querySelector\('\.screening-results-surface'\)\?\.addEventListener\('click'/, 'candidate expansion should use one delegated listener');
assert.match(screener, /screening-context-details/, 'market dimensions and data sources must stay optional');
assert.match(screener, /id="strength-retry"/, 'failed scans need a retry action');
assert.match(screener, /本轮没有候选/, 'empty scans need a useful empty state');
assert.match(screener, /sourceStatusLabel/, 'data-source degradation must be visible');
assert.match(screener, /id="strength-min-price"[^>]*type="number"[^>]*min="0"/, 'minimum price needs a non-negative production filter');
assert.match(screener, /id="strength-min-liquidity"[^>]*type="number"[^>]*min="0"/, 'minimum average dollar volume needs a non-negative production filter');
assert.match(screener, /function optionalFinite[\s\S]*value === null \|\| value === undefined \|\| value === ''/, 'missing range persistence values must stay missing rather than becoming zero');
assert.match(screener, /range_persistence_normalized_score \?\? feature\.range_persistence/, 'range persistence should prefer the normalized server score');
assert.match(screener, /row\.range_persistence_score_delta \?\? shadow\.score_delta/, 'the frontend must consume the scanner row score delta');
assert.match(screener, /payload\.range_persistence_version[\s\S]*payload\.params\?\.range_persistence_version/, 'the frontend must preserve the root range persistence version');
assert.match(screener, /shadow\.effective_weight[\s\S]*shadow\.contribution_cap[\s\S]*有效权重[\s\S]*权重上限/, 'expanded range evidence must expose the applied weight and service cap');
assert.match(screener, /影子观察 · 不参与排序[\s\S]*不参与本轮扫描、排序或评分/, 'shadow range persistence must clearly state that production ranking is unchanged');
assert.match(screener, /区间持续性当前没有进入计算[\s\S]*服务端没有返回有效区间持续性[\s\S]*已按服务端配置进入趋势评分/, 'disabled, insufficient and enabled range modes need distinct explanations');
assert.match(screener, /renderRangePersistenceSummary\(rangePersistence\)[\s\S]*renderRangePersistenceDetail\(rangePersistence\)/, 'candidate cards need a compact range state and expanded evidence');
assert.match(screener, /\['ArrowLeft', 'ArrowRight', 'Home', 'End'\]/, 'horizontal filters need keyboard navigation');
assert.doesNotMatch(screener, /\$\{sector\.count\}/, 'raw sector counts must not be inserted into HTML');
assert.match(screenerV3, /^\.screening-page\s*\{/m, 'screener styles must start from their page root');
assert.match(screenerV3, /\.screening-page \.screening-candidate-summary/, 'candidate row styles must stay scoped');
assert.match(screener, /screening-candidate--lead[\s\S]*本轮首选[\s\S]*screening-lead-dimensions/, 'the first candidate needs an editorial lead treatment with decision dimensions');
assert.match(screenerV3, /\.screening-page \.screening-results-surface\s*\{[^}]*grid-template-columns:\s*repeat\(12, minmax\(0, 1fr\)\)/, 'candidate cards need a twelve-column editorial grid');
assert.match(screenerV3, /\.screening-page \.screening-candidate\s*\{[^}]*grid-column:\s*span 6;/, 'secondary candidates should form a readable two-column queue');
assert.match(screenerV3, /\.screening-page \.screening-candidate--lead,[\s\S]*\.screening-candidate\.is-expanded\s*\{[^}]*grid-column:\s*1 \/ -1;/, 'the lead or expanded candidate must span the canvas');
assert.match(screenerV3, /\.screening-page \.screening-result-state\s*\{[^}]*grid-column:\s*1 \/ -1;/, 'candidate empty and error states must span the grid');
assert.match(screenerV3, /\.screening-page \.screening-candidate:has\(\.screening-candidate-link:focus-visible\)/, 'candidate cards need a visible whole-card focus state');
assert.match(screenerV3, /@container screening \(max-width: 680px\)/, 'mobile candidates need their own composition');
assert.match(screenerV3, /@container screening \(max-width: 680px\)[\s\S]*\.screening-liquidity-grid\s*\{[^}]*grid-template-columns:\s*1fr;/, 'price and liquidity filters must stack on phones');
assert.match(screenerV3, /@container screening \(max-width: 680px\)[\s\S]*\.screening-range-detail dl\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/, 'range evidence must remain readable on phones');
assert.match(screenerV3, /@container screening \(max-width: 680px\)[\s\S]*\.screening-results-heading\s*\{[^}]*grid-template-columns:\s*1fr;/, 'long production filter summaries must not squeeze the mobile result heading');

// Breakout Radar: completed snapshots, truthful system states, and on-demand evidence.
assert.match(breakouts, /optix-breakouts-v3\.css/, 'Breakout Radar must load its isolated v3 stylesheet');
assert.match(breakouts, /class="breakout-page"/, 'Breakout Radar needs its own page root');
assert.match(breakouts, /api\.breakoutStatus\(options\)[\s\S]*(?:api\.breakoutEvents|api\.breakoutCurrent)/, 'radar initialization must pair health with a completed snapshot');
assert.match(api, /breakoutStatus\(options = \{\}\)[\s\S]*\/breakouts\/status/, 'the frontend API must expose radar status');
assert.match(api, /breakoutCurrent\(options = \{\}\)[\s\S]*\/breakouts\/current/, 'the frontend API must expose the current snapshot');
assert.match(api, /breakoutEvents\(params = \{\}, options = \{\}\)[\s\S]*setup_type[\s\S]*lifecycle_state[\s\S]*min_priority[\s\S]*cursor/, 'all server-side radar filters and cursor pagination must be wired');
assert.match(api, /breakoutEvent\(eventId, options = \{\}\)/, 'event evidence must load on demand');
assert.match(api, /breakoutTicker\(ticker, options = \{\}\)/, 'ticker lifecycle history must load on demand');
assert.match(breakouts, /enabled === false[\s\S]*突破雷达尚未启用/, 'disabled must be distinct from an empty scan');
assert.match(breakouts, /status\?\.status === 'stale'[\s\S]*正在使用陈旧快照/, 'stale snapshots must remain visible with a warning');
assert.match(breakouts, /hasActiveFilters\(\)[\s\S]*当前条件没有匹配事件/, 'filtered empty results need a distinct explanation');
assert.match(breakouts, /交易日期（美东）/, 'the date filter must match the backend New York trading date');
assert.match(breakouts, /value === null \|\| value === undefined \|\| value === ''/, 'missing radar numbers must remain missing rather than becoming zero');
assert.match(breakouts, /影子观察，不参与排序/, 'shadow Range Persistence must never be described as production ranking');
assert.match(breakouts, /data-event-detail[\s\S]*aria-expanded=[\s\S]*aria-controls="breakout-event-detail"/, 'event evidence controls need disclosure semantics');
assert.match(breakouts, /\['ArrowLeft', 'ArrowRight', 'Home', 'End'\]/, 'session filters need keyboard arrow navigation');
assert.match(breakouts, /state\.controller\?\.abort\(\)[\s\S]*state\.detailController\?\.abort\(\)/, 'leaving the radar must cancel page and detail requests');
assert.doesNotMatch(breakouts, /重新扫描|立即扫描|开始扫描/, 'the read-only UI must not pretend to trigger the worker');
assert.match(breakouts, /function isValidTicker[\s\S]*!ticker\.endsWith\('\.'\)[\s\S]*!ticker\.includes\('\.\.'\)/, 'ticker filters must mirror backend normalization instead of silently broadening the query');
assert.match(breakouts, /if \(!isValidTicker\(ticker\)\)[\s\S]*setCustomValidity[\s\S]*return null/, 'invalid ticker filters need an actionable validation error');
assert.match(breakouts, /function snapshotsAligned[\s\S]*statusScanId\(status\) === payloadScanId\(payload\)/, 'status and events must be accepted only from the same completed scan');
assert.match(breakouts, /if \(!snapshotsAligned\(statusResult\.value, dataResult\.value\)\)[\s\S]*alignmentRetry/, 'a cross-publication race must retry before replacing the visible snapshot');
assert.match(breakouts, /class="breakout-read-warning"[\s\S]*已保留上一份完整快照/, 'refresh failures must remain visible while retained events are on screen');
assert.match(breakouts, /最新匹配[\s\S]*匹配事件（按事件时间）/, 'filtered results must describe the API chronological order instead of claiming a priority rank');
assert.match(breakouts, /escapeHtml\(structure\.pivot_touch_count \?\? '—'\)/, 'untyped structure values must be escaped before entering innerHTML');
assert.match(breakouts, /event\.provenance\?\.source_snapshot_id/, 'historical evidence must show its own source snapshot rather than the current list scan');
assert.match(breakouts, /ensureBreakoutStylesheet\(\)[\s\S]*window\.location\.hash[\s\S]*!== 'breakouts'/, 'a delayed stylesheet must not remount the radar after navigation');
assert.match(breakoutsV3, /^\.breakout-page\s*\{/m, 'radar styles must stay scoped to the page root');
assert.match(breakoutsV3, /\.breakout-lead\s*\{[^}]*grid-template-columns:/, 'the lead signal needs an editorial desktop composition');
assert.match(breakoutsV3, /\.breakout-event-grid\s*\{[^}]*grid-template-columns:\s*repeat\(12, minmax\(0, 1fr\)\)/, 'event queue needs a twelve-column canvas');
assert.match(breakoutsV3, /@container breakout \(max-width: 720px\)[\s\S]*\.breakout-event-card\s*\{[^}]*grid-column:\s*1 \/ -1;/, 'phone event cards must recompose to one column');
assert.match(breakoutsV3, /@container breakout \(max-width: 430px\)[\s\S]*\.breakout-filter-grid\s*\{[^}]*grid-template-columns:\s*1fr;/, 'phone filters must remain complete in one column');
assert.match(breakoutsV3, /--breakout-accent-aa:[\s\S]*\.breakout-session-filter button\[aria-pressed="true"\][\s\S]*color:\s*var\(--breakout-accent-aa\)/, 'small accent controls need an AA-contrast foreground');
assert.match(breakoutsV3, /\.breakout-detail summary\s*\{[^}]*min-height:\s*2\.75rem;/, 'evidence disclosure controls need a full touch target');
assert.match(breakoutsV3, /\.breakout-read-warning\s*\{[^}]*grid-template-columns:/, 'retained-snapshot warnings need a responsive visible treatment');
assert.match(breakoutsV3, /@media \(prefers-reduced-motion: reduce\)/, 'radar motion must respect user preferences');
assert.match(breakoutsV3, /@media \(forced-colors: active\)/, 'radar states must remain visible in forced colors');

// Market-strength research uses production data and keeps each source honest.
const marketStrengthModule = await import(pathToFileURL(path.join(frontend, 'static/js/components/marketStrengthPlaceholder.js')).href);
for (const ticker of ['^GSPC', '^IXIC', '^DJI', '^N225', '000001.SS']) {
  assert.equal(marketStrengthModule.isMajorMarketIndex(ticker), true, `${ticker} must be recognized as a headline index`);
}
assert.equal(marketStrengthModule.isMajorMarketIndex('AAPL'), false, 'ordinary stocks must not receive index-only market research');
for (const ticker of ['^GSPC', '^IXIC', '^DJI', '^NDX', '^RUT', '^VIX']) {
  assert.equal(marketStrengthModule.supportsUsMarketResearch(ticker), true, `${ticker} may use the US market research panel`);
}
for (const ticker of ['^N225', '000001.SS', '^HSI', '^FTSE', '^GDAXI', '^STOXX50E']) {
  assert.equal(marketStrengthModule.supportsUsMarketResearch(ticker), false, `${ticker} must not inherit US market breadth and risk context`);
}
assert.equal(marketStrengthModule.getMajorIndexDisplayName('^IXIC'), '纳斯达克综合指数', 'index detail titles need a safe display name');
assert.equal(marketStrengthModule.getMajorIndexBadge('^GSPC'), 'SPX', 'index details need a compact non-company badge');
const degradedMarketPanel = marketStrengthModule.renderMarketStrengthPanel({
  variant: 'detail',
  ticker: '^GSPC',
  marketRegime: { status: 'degraded', score: null, index_trend_score: 80 },
  marketSignals: { scores: { top_score: 10, bottom_score: 5 } },
  indexSignals: { score: 60, signals: { rsi: { value: 50, signal: 'neutral' } } },
});
assert.match(degradedMarketPanel, /data-market-strength-state="partial"/, 'degraded market evidence must remain visibly partial');
assert.match(degradedMarketPanel, /暂不生成市场强弱结论/, 'degraded market evidence must explain that no conclusion is produced');
const degradedShapePanel = marketStrengthModule.renderMarketStrengthPanel({
  marketRegime: {
    status: 'degraded',
    score: 68,
    market_shape: {
      status: 'degraded',
      state: 'BULL_TREND',
      state_label: '多头趋势',
      confidence: 0.62,
      transition_risk: 0.31,
      warnings: ['可选风险数据不完整'],
    },
  },
});
assert.match(degradedShapePanel, /market-strength-panel__shape is-degraded/, 'a degraded but usable market shape needs a distinct visible state');
assert.match(degradedShapePanel, /置信度<\/dt><dd>62%/, 'degraded market-shape confidence must remain visible');
assert.match(degradedShapePanel, /转向风险<\/dt><dd>31%/, 'degraded market-shape transition risk must remain visible');
assert.match(degradedShapePanel, /数据降级/, 'degraded market-shape evidence must retain its warning label');
assert.doesNotMatch(marketStrengthPlaceholder, /技术分析计划待接入|目前仅预留|等待指标与规则/, 'the live panel must not retain placeholder copy');
assert.match(marketStrengthPlaceholder, /index_trend_score[\s\S]*market_momentum_score[\s\S]*market_breadth_score[\s\S]*market_volume_score[\s\S]*risk_appetite_score[\s\S]*risk_on_spread_score/, 'all six market-regime dimensions must be rendered from production fields');
for (const field of ['state_label', 'confidence', 'transition_risk', 'as_of', 'rules', 'warnings', 'version']) {
  assert.match(marketStrengthPlaceholder, new RegExp(`shape\\.${field}`), `market shape ${field} must be visible`);
}
assert.match(marketStrengthPlaceholder, /仅作市场环境研究，不构成买卖信号/, 'market research needs a non-trading-signal disclaimer');
assert.match(marketStrengthPlaceholder, /data-market-strength-state="\$\{viewState\}"/, 'the panel must expose its resolved data state');
for (const stateName of ['loading', 'error', 'insufficient']) {
  assert.match(marketStrengthPlaceholder, new RegExp(`market-strength-panel__message--${stateName}`), `${stateName} needs a distinct visible state`);
}
assert.match(marketStrengthPlaceholder, /技术见顶风险[\s\S]*scores\.top_breakdown[\s\S]*scores\.top_reasons[\s\S]*技术见底修复[\s\S]*scores\.bottom_breakdown[\s\S]*scores\.bottom_reasons/, 'market top/bottom scores need their breakdowns and evidence');
assert.match(marketStrengthPlaceholder, /自身技术面[\s\S]*\['rsi', 'macd', 'ema20', 'sma50', 'volume'\]/, 'index detail research must expose the legacy per-index technical indicators');
for (const role of ['趋势结构', '广度动量', '风险确认']) {
  assert.match(marketStrengthPlaceholder, new RegExp(`<small>${role}<\\/small>`), `${role} needs a clear source role`);
}
assert.match(api, /signalsMarket\(options = \{\}\)[\s\S]*\/signals\/market/, 'the API client must expose full-market technical signals');
assert.match(api, /indexTechnicalSignals\(ticker, options = \{\}\)[\s\S]*\/stocks\/\$\{enc\(ticker\)\}\/signals/, 'index details must use the caret-compatible stock signal endpoint');
assert.match(api, /strengthMarket\(options = \{\}\)[\s\S]*\/strength\/market/, 'the API client must expose market-regime strength');
assert.match(api, /earningsImpact\(ticker, options = \{\}\)[\s\S]*return fetchJson\([\s\S]*\/ai\/earnings-impact\//, 'earnings impact soft errors must remain retryable without a browser-memory cache');
assert.match(screener, /renderMarketContext\(payload\)[\s\S]*renderMarketStrengthPanel\(\{[\s\S]*marketRegime: payload\.market_regime/, 'the screener panel must reuse the current scan market regime');
assert.doesNotMatch(screener, /signalsMarket|indexTechnicalSignals|strengthMarket/, 'the screener must not issue duplicate market research requests');
assert.match(screener, /state\.scanController\?\.abort\(\)[\s\S]*api\.strengthScan\([\s\S]*signal: scanController\.signal/, 'screener scans must cancel when replaced or when leaving the route');
assert.match(detail, /supportsUsMarketResearch\(ticker\) \? `[\s\S]*renderMarketStrengthPanel\(\{ variant: 'detail'/, 'only supported US index details should render live market research');
assert.match(detail, /Promise\.allSettled\(\[[\s\S]*loadIndexSignalsSource\(state\)[\s\S]*loadMarketSignalsSource\(state\)[\s\S]*loadMarketStrengthSource\(state\)/, 'the three index research sources must load in parallel and fail independently');
assert.match(detail, /state\.abortController\.abort\(\)/, 'leaving index detail must cancel all three research requests');
assert.match(detail, /getMajorIndexDisplayName\(stock\.ticker\)/, 'known index detail headers need a readable market name');
assert.match(detail, /indexInstrument \? '最新点位' : '最新价格'/, 'index details must describe levels as points rather than stock prices');
assert.match(detail, /formatPrice\(price, \{ currency: !indexInstrument \}\)/, 'index headline levels must not carry a dollar sign');
assert.match(detail, /\['52周高点', formatPrice\(stock\.year_high, \{ currency: false \}\)\][\s\S]*\['52周低点'/, 'index summaries need point-relevant range fields instead of valuation fields');
assert.match(detail, /state && !indexInstrument \? `<img/, 'index details must not request or present a company logo');
assert.doesNotMatch(screenerV3, /\.screening-page \.market-strength-panel\s*\{[^}]*backdrop-filter:/, 'the screener panel must stay on a crisp data surface');
assert.doesNotMatch(detailV3, /\.instrument-page \.market-strength-panel\s*\{[^}]*backdrop-filter:/, 'the index panel must stay on a crisp data surface');
assert.match(screenerV3, /@container screening \(max-width: 680px\)[\s\S]*\.market-strength-panel__overview\s*\{[^}]*grid-template-columns:\s*1fr;/, 'the screener market overview must recompose on phones');
assert.match(detailV3, /@container instrument-detail \(max-width: 720px\)[\s\S]*\.market-strength-panel__sources\s*\{[^}]*grid-template-columns:\s*1fr;/, 'the three index research sources must stack on phones');

// Detail and chart: explicit price adjustment plus visible time and session semantics.
assert.match(detail, /optix-detail-v3\.css/, 'detail must load its v3 stylesheet');
assert.match(detail, /class="instrument-page"/, 'detail needs its isolated instrument root');
assert.match(detail, /aria-label="价格复权方式"/, 'the chart adjustment control needs an accessible name');
assert.match(detail, /\['raw', '原始'\][\s\S]*\['adjusted', '复权'\]/, 'users need original and adjusted price choices');
assert.match(detail, /CHART_ADJUSTMENT_KEY/, 'the selected adjustment mode must persist');
assert.match(detail, /data-adjustment="\$\{value\}"[\s\S]*aria-pressed/, 'adjustment buttons need explicit selected state');
assert.match(detail, /\['raw', 'adjusted'\]\.includes\(data\?\.price_adjustment\)[\s\S]*state\.adjustment[\s\S]*priceAdjustment === 'adjusted' \? '复权价格' : '原始价格'/, 'chart context must state the active adjustment basis and fall back to the requested mode');
assert.match(detail, /include_extended_hours \? '含盘前 \/ 盘后' : '不含盘前 \/ 盘后'/, 'chart context must state extended-hours coverage');
assert.match(detail, /title="交易所时区：\$\{esc\(exchangeTimezone\)\}"/, 'chart context must show the exchange timezone');
assert.match(detail, /data\?\.last_bar_at[\s\S]*（交易所）[\s\S]*（本地）/, 'chart context must expose the latest bar in exchange and local time');
assert.match(detail, /data\.as_of/, 'chart context must expose data freshness');
assert.match(detail, /state\.chartController\?\.abort\(\)/, 'a newer chart request must abort the older request');
assert.match(detail, /api\.chart\(ticker, range, \{[\s\S]*signal: chartController\.signal,[\s\S]*adjustment: state\.adjustment/, 'chart requests need cancellation and adjustment semantics');
assert.match(detail, /data-detail-panel="signals"/, 'technical signals should load only when expanded');
assert.match(detail, /data-detail-panel="options"/, 'option research should load only when expanded');
assert.match(detail, /value === null \|\| value === undefined \|\| value === ''/, 'missing quotes must stay missing instead of becoming zero');
assert.match(detail, /标的现价缺失，本次不生成依赖现价的异动分析/, 'option analysis must stop when the underlying quote is missing');
assert.match(detail, /c:\$\{encodeURIComponent\(ticker\.toUpperCase\(\)\)\}/, 'special symbols need the same encoded chart cache key as the API client');
assert.match(detail, /chainTitle\.textContent = firstFinite\(newChain\.underlying_price\)/, 'expiration changes must refresh the visible option-chain range label');
assert.match(detail, /updateDisclosureState\(state, '\[data-options-state\]', newChain\._stale \? '陈旧数据' : '已更新'\)/, 'expiration changes must refresh option freshness state');
assert.match(api, /adjustment === 'adjusted' \? 'adjusted' : 'raw'/, 'chart API must normalize adjustment values');
assert.match(api, /c:\$\{enc\(ticker\)\}:\$\{range\}:\$\{adjustment\}/, 'raw and adjusted charts need separate browser cache entries');
assert.match(chart, /payload\.exchange_timezone \|\| options\.exchangeTimezone \|\| 'America\/New_York'/, 'chart rendering must use exchange timezone metadata');
assert.match(chart, /交易所时间 · \$\{formatFullTime/, 'chart tooltip must label exchange time');
assert.match(chart, /本地时间 · \$\{formatFullTime/, 'chart tooltip must also label local time');
assert.match(chart, /bars\.some\(\(bar\) => bar\.extended\)/, 'chart legend must identify extended-hours bars');
assert.match(chart, /canvas\.setAttribute\([\s\S]*'aria-label'/, 'the chart needs keyboard instructions and an accessible name');
assert.match(detailV3, /^\.instrument-page\s*\{/m, 'detail styles must start from the instrument root');
assert.match(detail, /class="instrument-chart-panel" data-motion-static/, 'the chart panel must explicitly opt out of decorative motion');
assert.match(detail, /class="instrument-chart-stage" data-motion-static/, 'the chart stage must explicitly opt out of decorative motion');
assert.match(detailV3, /\.instrument-page \.instrument-quote\s*\{[^}]*grid-template-columns:\s*minmax\(0, 5fr\) minmax\(20rem, 7fr\)/, 'the quote hero should give the live price the dominant column');
assert.match(detailV3, /\.instrument-page \.instrument-chart-stage\s*\{[^}]*background:\s*var\(--detail-surface\)/, 'the chart stage must stay on an opaque data surface');
assert.match(detailV3, /@container instrument-detail \(max-width: 720px\)[\s\S]*min-height: 44px/, 'mobile chart controls need reliable touch targets');

// Sector explorer: independent data paths, semantic map states, and mobile switching.
assert.match(sectors, /optix-sectors-v3\.css/, 'sectors must load its v3 stylesheet');
assert.match(sectors, /class="sector-page"/, 'sectors needs its isolated page root');
assert.match(sectors, /const sectorData = await api\.sectors\(\)/, 'sector rankings must render independently of watchlist data');
assert.doesNotMatch(sectors, /Promise\.all\(\[\s*api\.sectors/, 'watchlist must not block the sector ranking');
assert.match(sectors, /data-sector-select/, 'sector selection needs native buttons');
assert.match(sectors, /setAttribute\('aria-pressed', active \? 'true' : 'false'\)/, 'selected sectors need an accessible pressed state');
assert.match(sectors, /data-sector-mobile-view="inspector"/, 'mobile users need a direct route to the inspector');
assert.match(sectors, /Promise\.allSettled\(\[[\s\S]*api\.sectorIV[\s\S]*api\.sectorHeatmap/, 'sector detail sources must fail independently');
assert.match(sectors, /scrollIntoView\(\{ behavior: 'auto'/, 'the active sector must remain visible after updates');
assert.match(heatmap, /data-heatmap-tone=/, 'heatmap tiles need a semantic written state alongside color');
assert.match(heatmap, /heatmap-tile__state/, 'heatmap tiles must render that written state');
assert.match(sectorsV3, /^\.sector-page\s*\{/m, 'sector styles must start from their page root');
assert.match(sectors, /class="sector-map-stage"[^>]*data-motion-lens[^>]*data-motion-spotlight/, 'the heatmap stage should carry the sector signature interaction');
assert.match(sectorsV3, /\.sector-workspace\s*\{[^}]*grid-template-columns:\s*repeat\(12, minmax\(0, 1fr\)\)/, 'sector map and inspector need a twelve-column workspace');
assert.match(sectorsV3, /@media \(min-width: 48rem\)[\s\S]*\.sector-map-stage\s*\{[^}]*grid-column:\s*1 \/ span 7;/, 'the heatmap should lead the desktop workspace');
assert.match(sectorsV3, /@media \(min-width: 48rem\)[\s\S]*\.sector-inspector\s*\{[^}]*grid-column:\s*8 \/ -1;/, 'the inspector should form the supporting desktop rail');
assert.match(sectorsV3, /\.sector-page\[data-mobile-view="inspector"\] \.sector-map-stage/, 'mobile view switching must replace rather than stack content');
assert.match(sectorsV3, /@media \(prefers-reduced-motion: reduce\)/, 'sector interactions must respect reduced motion');

// Earnings calendar: a navigable seven-day view with optional impact research.
assert.match(earnings, /optix-earnings-v3\.css/, 'earnings must load its v3 stylesheet');
assert.match(earnings, /class="earnings-page"/, 'earnings needs its isolated page root');
assert.match(earnings, /role="tooltip"/, 'earnings hover details need tooltip semantics');
assert.doesNotMatch(earnings, /aria-describedby="earnings-tooltip"/, 'the pointer tooltip must not duplicate the card accessible name');
assert.match(earnings, /\['ArrowLeft', 'ArrowRight', 'Home', 'End'\]/, 'earnings dates need arrow-key navigation');
assert.match(earnings, /role="button" tabindex="0" data-event-row=/, 'each earnings event must expose one whole-card action');
assert.match(earnings, /aria-controls="earnings-impact-panel" aria-expanded=/, 'earnings cards need explicit impact-panel state');
assert.match(earnings, /class="earnings-event__identity"[\s\S]*<h3>\$\{escapeHtml\(item\.name\)\}<\/h3>/, 'earnings cards must show the full company name');
assert.match(earnings, /\['Enter', ' '\]\.includes\(event\.key\)/, 'earnings cards need Enter and Space keyboard activation');
assert.match(earnings, /data-impact-close/, 'impact research must remain optional and dismissible');
assert.match(earnings, /returnTarget\?\.focus\(\)/, 'closing impact research must restore keyboard focus');
assert.match(earnings, /const impactError = earningsImpactErrorMessage\(result\);[\s\S]*if \(impactError\) throw new Error\(impactError\);/, 'AI soft failures must enter the visible impact error state instead of announcing success');
assert.match(earnings, /未来时间范围内暂无已确认财报/, 'the calendar needs a truthful empty state');
assert.match(earningsV3, /^\.earnings-page\s*\{/m, 'earnings styles must start from their page root');
assert.match(earningsV3, /\.earnings-week-nav button[\s\S]*min-height: 2\.75rem/, 'calendar navigation needs reliable touch targets');
assert.match(earningsV3, /@media \(max-width: 47\.999rem\)[\s\S]*\.earnings-week-nav\s*\{[^}]*box-shadow:[^}]*0 8px 20px/, 'mobile calendar navigation needs a light outer shadow without the desktop halo');
assert.match(earnings, /earnings-day__node/, 'the seven-day strip needs visible timeline nodes');
assert.match(earnings, /requestAnimationFrame\(placeByPoint\)/, 'the pointer tooltip must be frame-throttled');
assert.match(earnings, /translate3d\(\$\{Math\.max\(8, left\)\}px, \$\{Math\.max\(8, top\)\}px, 0\)/, 'the pointer tooltip must use compositor-friendly placement');
assert.match(earningsV3, /\.earnings-events__list\s*\{[^}]*grid-template-columns:\s*repeat\(12, minmax\(0, 1fr\)\)/, 'earnings events need a twelve-column editorial grid');
assert.match(earningsV3, /\.earnings-section-heading time,\s*\.earnings-events__heading > strong\s*\{[^}]*font-family:\s*var\(--font-compact\);[^}]*font-weight:\s*560;/, 'earnings range metadata needs the readable compact role');
assert.match(earningsV3, /\.earnings-day__date\s*\{[^}]*font-family:\s*var\(--font-compact\);[^}]*font-weight:\s*650;/, 'earnings dates need an explicit cross-platform weight');
assert.match(earningsV3, /\.earnings-day small\s*\{[^}]*font-family:\s*var\(--font-compact\);[^}]*font-weight:\s*540;/, 'earnings previews should remain legible at caption size');
assert.match(earningsV3, /\.earnings-event:nth-child\(4n \+ 1\),[\s\S]*grid-column:\s*span 7;/, 'earnings cards should alternate their visual weight');
assert.match(earningsV3, /@media \(max-width: 47\.999rem\)[\s\S]*\.earnings-event,[\s\S]*grid-column:\s*1 \/ -1;/, 'mobile earnings cards must collapse to one column');
assert.match(earningsV3, /\.earnings-empty-state\s*\{[^}]*grid-column:\s*1 \/ -1;/, 'earnings empty and error states must span the card grid');
assert.match(earningsV3, /\.earnings-event:focus-visible/, 'earnings cards need a visible keyboard focus');
assert.match(earningsV3, /@media \(prefers-reduced-motion: reduce\)/, 'earnings interactions must respect reduced motion');

// Shared interactive components retain local icons and accessible controls.
assert.match(search, /addEventListener\('focus'.*ensureUniverse/, 'search data must load on first focus');
assert.doesNotMatch(search, /let universePromise = loadSearchUniverse\(\)/, 'search data must not load during initialization');
assert.match(optionChain, /import \{ renderIcon \} from '\.\.\/icons\.js'/, 'option alerts must use local SVG icons');

console.log('frontend static assertions (deck shell + archived v3 modules): ok');
