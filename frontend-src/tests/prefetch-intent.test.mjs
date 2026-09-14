import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = (...parts) => path.join(here, '..', 'src', ...parts);

test('intent prefetch only loads route chunks and respects save-data / concurrency', async () => {
  const prefetch = await readFile(src('lib', 'prefetchRouteChunk.ts'), 'utf8');
  assert.match(prefetch, /MAX_INTENT = 2/);
  assert.match(prefetch, /saveData === true/);
  assert.match(prefetch, /current === normalized/);
  assert.match(prefetch, /void import\('@\/pages\/Earnings'\)/);
  assert.match(prefetch, /normalized\.startsWith\('\/stock\/'\)/);
  assert.doesNotMatch(prefetch, /\/api\//);
  assert.doesNotMatch(prefetch, /earningsApi|catalystsContract|stocksApi/);
});

test('surfaces lab clicks the visible home card and desktop nav, not a hidden earnings anchor', async () => {
  const surfaces = await readFile(path.join(here, '..', '..', 'scripts', 'perf', 'measure_round6_surfaces.mjs'), 'utf8');
  assert.match(surfaces, /section\[aria-label="财报临近"\] a\[href="\/earnings"\]/);
  assert.match(surfaces, /nav\[aria-label="主导航"\] a\[href="\/earnings"\]/);
  assert.match(surfaces, /viewport: 'desktop'/);
  assert.doesNotMatch(surfaces, /page\.click\('a\[href="\/earnings"\]'\)/);
  assert.doesNotMatch(surfaces, /querySelector\('a\[href="\/earnings"\]'\)/);
});

test('surfaces lab fulfills home and earnings APIs so isolate uvicorn does not call paid upstream', async () => {
  const surfaces = await readFile(path.join(here, '..', '..', 'scripts', 'perf', 'measure_round6_surfaces.mjs'), 'utf8');
  for (const pathName of [
    '/api/earnings/upcoming',
    '/api/market/indices',
    '/api/market/status',
    '/api/strength/market',
    '/api/signals/market',
    '/api/breakouts/current',
    '/api/stocks/watchlist',
    '/api/market/cta',
    '/api/quotes',
  ]) {
    assert.match(surfaces, new RegExp(pathName.replace(/\//g, '\\/')));
  }
  assert.match(surfaces, /quotes\/stream/);
  assert.match(surfaces, /挡不住 uvicorn 出站/);
});

test('navbar, dock, palette and login expose hover/focus route prefetch', async () => {
  const navbar = await readFile(src('components', 'Navbar.tsx'), 'utf8');
  const dock = await readFile(src('components', 'MobileDock.tsx'), 'utf8');
  const palette = await readFile(src('components', 'CommandPalette.tsx'), 'utf8');
  assert.match(navbar, /routeIntentHandlers\(item\.path\)/);
  assert.match(navbar, /routeIntentHandlers\('\/login'\)/);
  assert.match(dock, /routeIntentHandlers\(item\.path\)/);
  assert.match(dock, /prefetchRouteOnIntent\(m\.path\)/);
  assert.match(palette, /prefetchRouteOnIntent/);
  assert.match(palette, /path: n\.path/);
  assert.match(palette, /path: `\/stock\/\$\{r\.ticker\}`/);
});

test('i18n boot loads only the active language table', async () => {
  const boot = await readFile(src('i18n', 'boot.ts'), 'utf8');
  const main = await readFile(src('main.tsx'), 'utf8');
  assert.match(boot, /locale === 'en'/);
  assert.match(boot, /runtime-en\.ts/);
  assert.match(boot, /runtime-ja\.ts/);
  assert.match(boot, /if \(locale !== 'zh'\)/);
  assert.doesNotMatch(boot, /from '\.\/dict\/index/);
  assert.match(main, /prepareI18n\(\)/);
  assert.ok(main.indexOf('prepareI18n') < main.indexOf("import('./App.tsx')"));
});
