import assert from 'node:assert/strict';
import { access, readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '..');
const read = relativePath => readFile(path.join(frontend, relativePath), 'utf8');

const activePaths = [
  'index.html',
  'static/favicon.svg',
  'static/css/optix-deck.css',
  'static/css/optix-catalysts.css',
  'static/js/theme-init.js',
  'static/js/deck-api.js',
  'static/js/deck-ai-jobs.js',
  'static/js/deck-catalysts.js',
  'static/js/deck-app.js',
];

const [index, favicon, deckCss, catalystCss, themeInit, api, jobs, catalysts, app] =
  await Promise.all(activePaths.map(read));
const productionBundle = [index, favicon, deckCss, catalystCss, themeInit, api, jobs, catalysts, app].join('\n');

const retiredPaths = [
  'static/css/styles.css',
  'static/css/optix-core-v3.css',
  'static/css/optix-watchlist-v3.css',
  'static/css/optix-screener-v3.css',
  'static/css/optix-detail-v3.css',
  'static/css/optix-sectors-v3.css',
  'static/css/optix-earnings-v3.css',
  'static/css/optix-breakouts-v3.css',
  'static/css/optix-nightday-v4.css',
  'static/js/app.js',
  'static/js/api.js',
  'static/js/icons.js',
  'static/js/theme-toggle.js',
  'static/icons.svg',
];
const retiredDirectories = [
  'static/js/pages',
  'static/js/components',
  'static/js/utils',
];

for (const retiredPath of retiredPaths) {
  await assert.rejects(
    access(path.join(frontend, retiredPath)),
    error => error && error.code === 'ENOENT',
    `${retiredPath} must stay removed from the production tree`,
  );
}

for (const retiredDirectory of retiredDirectories) {
  try {
    const entries = await readdir(path.join(frontend, retiredDirectory));
    assert.deepEqual(entries, [], `${retiredDirectory} must not contain production files`);
  } catch (error) {
    assert.equal(error && error.code, 'ENOENT', `${retiredDirectory} must be absent or empty`);
  }
}

assert.match(index, /<html lang="zh-CN">/, 'the production document language must be Simplified Chinese');
assert.match(index, /<title>Optix Pro · 个人投资研究<\/title>/, 'the product title must stay unified');
assert.match(index, /font-src 'self'/, 'the content security policy must keep fonts local');
assert.match(index, /rel="icon" href="\.\/static\/favicon\.svg"/, 'the local favicon must be linked');
assert.match(index, /<main id="view"[^>]+tabindex="-1"/, 'route changes need a focusable main region');
assert.match(index, /id="drawer"[^>]*role="dialog"[^>]*aria-modal="true"/, 'research drawers need dialog semantics');
assert.match(index, /id="theme-toggle"/, 'the theme control must remain in the production shell');

const linkedStyles = [...index.matchAll(/<link\s+rel="stylesheet"\s+href="([^"]+)"/g)]
  .map(match => match[1].split('?')[0]);
assert.deepEqual(linkedStyles, [
  './static/css/optix-deck.css',
  './static/css/optix-catalysts.css',
], 'the shell must load only the two Night Desk stylesheets');

const linkedScripts = [...index.matchAll(/<script\s+src="([^"]+)"/g)]
  .map(match => match[1].split('?')[0]);
assert.deepEqual(linkedScripts, [
  './static/js/theme-init.js',
  './static/js/deck-api.js',
  './static/js/deck-ai-jobs.js',
  './static/js/deck-catalysts.js',
  './static/js/deck-app.js',
], 'the shell must load only the Night Desk scripts in dependency order');

const deckScriptVersions = [...index.matchAll(/static\/js\/deck-(?:api|ai-jobs|catalysts|app)\.js\?v=([^"&]+)/g)]
  .map(match => match[1]);
assert.equal(deckScriptVersions.length, 4, 'all four dependent scripts must carry a cache version');
assert.equal(new Set(deckScriptVersions).size, 1, 'all dependent scripts must share one cache version');
assert.doesNotMatch(index, /<script>(?!<)/, 'the shell must not use an inline script under its content security policy');

assert.doesNotMatch(
  productionBundle,
  /fonts\.(?:googleapis|gstatic)\.com|use\.typekit\.net|use\.fontawesome\.com|material-symbols|Material Symbols/i,
  'the production frontend must not depend on remote fonts or font-backed icons',
);
assert.doesNotMatch(
  productionBundle,
  /styles\.css|optix-(?:core|watchlist|screener|detail|sectors|earnings|breakouts)-v3\.css|optix-nightday-v4\.css|static\/js\/(?:app|api|icons|theme-toggle)\.js|static\/js\/(?:pages|components|utils)\//,
  'active files must not revive a retired frontend path',
);
assert.doesNotMatch(productionBundle, /sk-proj-[A-Za-z0-9_-]+|OPENAI_API_KEY|MACROLENS_(?:READ|ACTION)_SECRET/, 'frontend files must not contain service secrets');
assert.doesNotMatch(productionBundle, /gpt-5\.6-luna/i, 'the frontend model label must remain on GPT-5.6 Terra');
assert.match(productionBundle, /gpt-5\.6-terra/, 'the current GPT-5.6 Terra model label must remain visible');

assert.match(
  app,
  /const routes = \{ watchlist: renderWatchlist, screener: \(\) => renderScreener\(\), breakouts: \(\) => renderBreakouts\(\), sectors: renderSectors, earnings: \(\) => renderEarnings\(\), catalysts: renderCatalysts \}/,
  'the production router must expose only the six Night Desk routes',
);
assert.match(app, /window\.addEventListener\("hashchange", route\)/, 'hash navigation must remain wired');
assert.match(app, /\$\("#theme-toggle"\)\.addEventListener\("click"/, 'the active bundle must own the theme toggle');
assert.doesNotMatch(app, /(?:import\s|import\()[^\n]*\/(?:pages|components|utils)\//, 'the active shell must not import a retired module');

assert.match(catalysts, /analysis && analysis\.title_zh/, 'analysis title_zh must be the first visible title source');
assert.match(catalysts, /item && item\.title_zh/, 'a persisted top-level Chinese title must remain supported');
assert.match(catalysts, /中文标题等待生成/, 'missing Chinese titles need a Chinese waiting state');
assert.match(catalysts, /item && item\.summary_zh/, 'a persisted Chinese summary must be preferred');
assert.match(catalysts, /analysis\.summary_zh \|\| analysis\.headline_summary/, 'analysis Chinese summaries must remain supported');
assert.match(catalysts, /中文摘要等待生成/, 'missing Chinese summaries need a Chinese waiting state');
assert.doesNotMatch(catalysts, /item\.title \|\| item\.headline/, 'news titles must never fall back to the source-language headline');
assert.doesNotMatch(catalysts, /item\.summary \|\| item\.description/, 'news summaries must never fall back to source-language text');

assert.match(deckCss, /:focus-visible/, 'the shell must retain keyboard focus styles');
assert.match(deckCss, /@media \(max-width: 860px\)[\s\S]*\.dock/, 'the mobile navigation must remain responsive');
assert.match(deckCss, /@media \(prefers-reduced-motion: reduce\)/, 'the shell must respect reduced motion');
assert.match(catalystCss, /\.cat-table-wrap[^}]*overflow-x:\s*auto/, 'wide catalyst tables must stay contained');
assert.match(catalystCss, /@media \(max-width: 430px\)/, 'the catalyst desk must retain its phone layout');
assert.match(catalystCss, /@media \(prefers-reduced-motion: reduce\)/, 'the catalyst desk must respect reduced motion');

console.log(`Frontend assertions passed for ${activePaths.length} production files; retired paths are absent or empty.`);
