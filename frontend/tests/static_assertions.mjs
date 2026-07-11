import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
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
  app,
  api,
  watchlist,
  screener,
  detail,
  sectors,
  earnings,
  chart,
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
  read('static/js/app.js'),
  read('static/js/api.js'),
  read('static/js/pages/watchlist.js'),
  read('static/js/pages/screener.js'),
  read('static/js/pages/detail.js'),
  read('static/js/pages/sectors.js'),
  read('static/js/pages/earnings.js'),
  read('static/js/components/chart.js'),
  read('static/js/components/search.js'),
  read('static/js/components/customWatchlist.js'),
  read('static/js/components/heatmap.js'),
  read('static/js/components/optionChain.js'),
  read('static/icons.svg'),
  readJavaScriptTree('static/js'),
]);

await read('static/favicon.svg');

const v3Styles = [coreV3, watchlistV3, screenerV3, detailV3, sectorsV3, earningsV3].join('\n');
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
  './static/css/optix-core-v3.css',
  './static/css/optix-watchlist-v3.css',
], 'the shell should load only the core and initial-route v3 styles');

// Core Optix Pro shell.
assert.match(index, /<title>Optix Pro · 个人投资研究<\/title>/, 'the product name must stay unified');
assert.match(index, /rel="icon" href="\.\/static\/favicon\.svg"/, 'the local favicon must be linked');
assert.match(index, /<body class="optix-app">/, 'the v3 shell needs its root class');
assert.match(index, /class="app-header"/, 'the shell needs its top navigation');
assert.match(index, /id="global-search"[^>]+aria-controls="global-search-results"/, 'global search needs accessible listbox wiring');
assert.match(index, /id="workspace-clock"/, 'the shell must expose local workspace time');
assert.match(index, /id="index-ticker-track"/, 'the shell must retain the market index strip');
assert.match(index, /<main id="app"[^>]+tabindex="-1"/, 'route changes need a focusable main region');
assert.match(index, /class="mobile-dock"/, 'mobile navigation must remain directly available');
assert.match(index, /data-route="watchlist"[\s\S]*data-route="screener"[\s\S]*data-route="sectors"/, 'the primary navigation should keep screening near the front');

for (const token of [
  'canvas', 'surface', 'surface-soft', 'ink', 'ink-soft', 'muted', 'line',
  'accent', 'accent-soft', 'positive', 'negative', 'warning',
  'radius-sm', 'radius-md', 'radius-lg', 'shadow-soft', 'font-sans', 'font-data',
  'glass-fill', 'glass-fill-strong', 'glass-fill-accent', 'glass-stroke',
  'glass-edge', 'glass-shadow', 'glass-shadow-float', 'glass-blur', 'glass-blur-float', 'glass-blur-mobile',
]) {
  assert.match(coreV3, new RegExp(`--${token}:`), `missing v3 design token: ${token}`);
}
assert.match(coreV3, /body\.optix-app/, 'core styles must be anchored to the v3 shell');
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
assert.match(screenerV3, /\.screening-page \.screening-controls\s*\{[^}]*backdrop-filter:/, 'the screener filter lens should carry the primary glass treatment');
assert.doesNotMatch(screenerV3, /\.screening-page \.screening-candidate-summary\s*\{[^}]*backdrop-filter:/, 'candidate rows must remain crisp data surfaces');
assert.match(watchlistV3, /\.focus-panel\s*\{[^}]*backdrop-filter:/, 'the focused quote may carry the watchlist glass treatment');
assert.doesNotMatch(watchlistV3, /\.watchlist-context-row\s*\{[^}]*backdrop-filter:/, 'watchlist context must not add a third persistent blur layer');
assert.doesNotMatch(detailV3, /\.instrument-page \.instrument-quote\s*\{[^}]*backdrop-filter:/, 'the quote header must remain a crisp surface');
assert.doesNotMatch(detailV3, /\.market-chart canvas\s*\{[^}]*backdrop-filter:/, 'chart canvases must never be blurred');
assert.doesNotMatch(sectorsV3, /\.sector-page__header\s*\{[^}]*backdrop-filter:/, 'the sector page header must remain a crisp surface');
assert.doesNotMatch(sectorsV3, /\.heatmap-tile\s*\{[^}]*backdrop-filter:/, 'heatmap data tiles must never be blurred');
assert.doesNotMatch(earningsV3, /\.earnings-page__header\s*\{[^}]*backdrop-filter:/, 'the earnings page header must remain a crisp surface');

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

for (const iconName of [
  'dashboard', 'layers', 'event_note', 'filter_alt', 'search', 'radar',
  'show_chart', 'candlestick_chart', 'chevron_left', 'chevron_right',
  'arrow_up_right', 'refresh', 'sliders', 'close', 'edit', 'check',
]) {
  assert.match(icons, new RegExp(`id="icon-${iconName}"`), `missing local SVG icon: ${iconName}`);
}

// Watchlist: honest freshness, safe snapshots, and a compact responsive quote list.
assert.match(watchlist, /class="watchlist-page"/, 'watchlist needs its v3 page root');
assert.match(watchlistV3, /^\.watchlist-page\s*\{/m, 'watchlist styles must start from their page root');
assert.match(index, /optix-watchlist-v3\.css/, 'the initial watchlist route needs its v3 stylesheet');
assert.match(watchlist, /WATCHLIST_REFRESH_MS = 5 \* 60 \* 1000/, 'watchlist refresh must stay aligned to five minutes');
assert.match(watchlist, /payload\?\.as_of \|\| payload\?\.fetched_at/, 'watchlist must display backend freshness');
assert.match(watchlist, /WATCHLIST_SNAPSHOT_KEY = 'optix\.watchlist\.snapshot\.v2'/, 'only the versioned real-data snapshot may persist');
assert.match(watchlist, /WATCHLIST_SNAPSHOT_MAX_AGE_MS = 7 \* 24 \* 60 \* 60 \* 1000/, 'expired prices must not remain indefinitely');
assert.match(watchlist, /incomingByTicker[\s\S]*previousByTicker/, 'partial updates must preserve temporarily missing symbols');
assert.match(watchlist, /containsPreviousPrices: usedPreviousPrices/, 'merged snapshots must record reused prices');
assert.match(watchlist, /含上次价格/, 'reused prices must be labelled honestly');
assert.match(watchlist, /class="stock-card quote-row/, 'watchlist items must use the v3 quote-row structure');
assert.match(watchlist, /id="watchlist-spotlight"/, 'watchlist needs a focused-stock summary');
assert.match(watchlist, /data-watchlist-filter="up"/, 'quote direction filters must remain interactive');
assert.match(watchlist, /for="watchlist-add-input"/, 'custom ticker entry needs a visible label');
assert.match(watchlist, /WATCHLIST_PAGE_SIZE = 24/, 'large watchlists must render in batches');
assert.doesNotMatch(watchlist, /[?&](?:demo|mock)=/, 'production watchlist code must not expose fake-data switches');
assert.match(watchlistV3, /@media \(max-width: 760px\)[\s\S]*\.stock-card__select[\s\S]*grid-template-areas/, 'mobile quotes need a purpose-built layout');
assert.match(api, /api\.watchlist|watchlist\(options = \{\}\)/, 'the frontend API must expose watchlist data');
assert.match(api, /isTargeted && tickers\.length === 0/, 'an empty custom list must not request the full universe');
assert.match(customWatchlist, /return TICKER_PATTERN\.test\(ticker\) \? ticker : ''/, 'invalid custom tickers must be rejected before requesting data');

// Screener: low-density decision rows with evidence disclosed on demand.
assert.match(screener, /optix-screener-v3\.css/, 'screener must load its v3 stylesheet');
assert.match(screener, /class="screening-page"/, 'screener needs its isolated page root');
assert.match(screener, /renderControls\(sectors\)[\s\S]*renderMarketContext\(payload\)[\s\S]*renderResults\(payload\)/, 'screener reading order must be settings, context, then candidates');
assert.match(screener, /screening-primary-reason/, 'each default candidate row needs one primary reason');
assert.match(screener, /data-result-toggle/, 'candidate evidence must remain progressively disclosed');
assert.match(screener, /screening-context-details/, 'market dimensions and data sources must stay optional');
assert.match(screener, /id="strength-retry"/, 'failed scans need a retry action');
assert.match(screener, /本轮没有候选/, 'empty scans need a useful empty state');
assert.match(screener, /sourceStatusLabel/, 'data-source degradation must be visible');
assert.match(screener, /api\.strengthScan\(\{[\s\S]*timeframe:[\s\S]*profile:[\s\S]*top:[\s\S]*sector_id:/, 'all selected scan filters must reach the API');
assert.match(screener, /\['ArrowLeft', 'ArrowRight', 'Home', 'End'\]/, 'horizontal filters need keyboard navigation');
assert.doesNotMatch(screener, /\$\{sector\.count\}/, 'raw sector counts must not be inserted into HTML');
assert.match(screenerV3, /^\.screening-page\s*\{/m, 'screener styles must start from their page root');
assert.match(screenerV3, /\.screening-page \.screening-candidate-summary/, 'candidate row styles must stay scoped');
assert.match(screenerV3, /@container screening \(max-width: 680px\)/, 'mobile candidates need their own composition');

// Detail and chart: explicit price adjustment plus visible time and session semantics.
assert.match(detail, /optix-detail-v3\.css/, 'detail must load its v3 stylesheet');
assert.match(detail, /class="instrument-page"/, 'detail needs its isolated instrument root');
assert.match(detail, /aria-label="价格复权方式"/, 'the chart adjustment control needs an accessible name');
assert.match(detail, /\['raw', '原始'\][\s\S]*\['adjusted', '复权'\]/, 'users need original and adjusted price choices');
assert.match(detail, /CHART_ADJUSTMENT_KEY/, 'the selected adjustment mode must persist');
assert.match(detail, /data-adjustment="\$\{value\}"[\s\S]*aria-pressed/, 'adjustment buttons need explicit selected state');
assert.match(detail, /price_adjustment === 'adjusted' \? '复权价格' : '原始价格'/, 'chart context must state the active adjustment basis');
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
assert.match(sectorsV3, /\.sector-page\[data-mobile-view="inspector"\] \.sector-map-stage/, 'mobile view switching must replace rather than stack content');
assert.match(sectorsV3, /@media \(prefers-reduced-motion: reduce\)/, 'sector interactions must respect reduced motion');

// Earnings calendar: a navigable seven-day view with optional impact research.
assert.match(earnings, /optix-earnings-v3\.css/, 'earnings must load its v3 stylesheet');
assert.match(earnings, /class="earnings-page"/, 'earnings needs its isolated page root');
assert.match(earnings, /role="tooltip"/, 'earnings hover details need tooltip semantics');
assert.match(earnings, /addEventListener\('focusin'/, 'earnings tooltip must also work from the keyboard');
assert.match(earnings, /\['ArrowLeft', 'ArrowRight', 'Home', 'End'\]/, 'earnings dates need arrow-key navigation');
assert.match(earnings, /data-impact-close/, 'impact research must remain optional and dismissible');
assert.match(earnings, /returnTarget\?\.focus\(\)/, 'closing impact research must restore keyboard focus');
assert.match(earnings, /未来时间范围内暂无已确认财报/, 'the calendar needs a truthful empty state');
assert.match(earningsV3, /^\.earnings-page\s*\{/m, 'earnings styles must start from their page root');
assert.match(earningsV3, /\.earnings-week-nav button[\s\S]*min-height: 2\.75rem/, 'calendar navigation needs reliable touch targets');
assert.match(earningsV3, /@media \(prefers-reduced-motion: reduce\)/, 'earnings interactions must respect reduced motion');

// Shared interactive components retain local icons and accessible controls.
assert.match(search, /addEventListener\('focus'.*ensureUniverse/, 'search data must load on first focus');
assert.doesNotMatch(search, /let universePromise = loadSearchUniverse\(\)/, 'search data must not load during initialization');
assert.match(optionChain, /import \{ renderIcon \} from '\.\.\/icons\.js'/, 'option alerts must use local SVG icons');

console.log('frontend v3 static assertions: ok');
