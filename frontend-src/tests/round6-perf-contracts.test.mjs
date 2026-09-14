import assert from 'node:assert/strict';
import test from 'node:test';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = (...parts) => path.join(here, '..', 'src', ...parts);

test('core i18n does not statically import merged dictionaries', async () => {
  const core = await readFile(src('i18n', 'core.ts'), 'utf8');
  assert.doesNotMatch(core, /from ['"]\.\/dict\/index/);
  assert.match(core, /installTranslations/);
  assert.match(core, /window\.location\.reload\(\)/);
  assert.match(core, /AI 生成的正文/);
});

test('visual review waits for async i18n boot before interacting with the app shell', async () => {
  const visual = (...parts) => path.join(here, '..', 'visual-tests', ...parts);
  const drawings = await readFile(visual('smart-drawings.spec.mjs'), 'utf8');
  const review = await readFile(visual('ui-review.spec.mjs'), 'utf8');
  const overlay = await readFile(visual('overlay-behavior.spec.mjs'), 'utf8');
  assert.match(drawings, /prepareLegendI18n/);
  assert.ok(drawings.indexOf('prepareLegendI18n') < drawings.indexOf('renderLegend(overlays'));
  assert.match(drawings, /installTranslations\('en', EN\)/);
  assert.match(drawings, /installTranslations\('ja', JA\)/);
  const skipAt = review.indexOf("name: '跳到主要内容'");
  const headingBeforeSkip = review.lastIndexOf("getByRole('heading'", skipAt);
  assert.ok(headingBeforeSkip >= 0 && headingBeforeSkip < skipAt, 'skip-link test must wait for the app heading');
  const shortcutAt = overlay.indexOf("keyboard.press('Control+k')");
  const headingBeforeShortcut = overlay.lastIndexOf("getByRole('heading'", shortcutAt);
  assert.ok(headingBeforeShortcut >= 0 && headingBeforeShortcut < shortcutAt, 'Ctrl+K test must wait for the app heading');
});

test('production chunks keep en/ja dictionaries off the shared entry', async () => {
  const dist = path.join(here, '..', 'dist');
  const html = await readFile(path.join(dist, 'index.html'), 'utf8');
  const entry = html.match(/assets\/(index-[^"]+\.js)/);
  assert.ok(entry, 'index.html must point at the hashed entry script');
  const leaked = /Skip to main content|サポート|レジスタンス|Demo mode · Prices/;
  const entryText = await readFile(path.join(dist, 'assets', entry[1]), 'utf8');
  assert.doesNotMatch(entryText, leaked);
  for (const name of await readdir(path.join(dist, 'assets'))) {
    if (!name.endsWith('.js') || name.startsWith('runtime-en') || name.startsWith('runtime-ja')) continue;
    const text = await readFile(path.join(dist, 'assets', name), 'utf8');
    assert.doesNotMatch(text, leaked, `${name} must not embed en/ja UI dictionaries`);
  }
});

test('earnings page localizes the clock and defers the EPS chart', async () => {
  const page = await readFile(src('pages', 'Earnings.tsx'), 'utf8');
  const chart = await readFile(src('components', 'earnings', 'DeferredEpsChart.tsx'), 'utf8');
  const refresh = await readFile(src('components', 'earnings', 'EarningsRefreshButton.tsx'), 'utf8');
  const calendar = await readFile(src('hooks', 'useEtCalendarDate.ts'), 'utf8');
  const nowHook = await readFile(src('hooks', 'useNow.ts'), 'utf8');
  assert.doesNotMatch(page, /useNow\s*\(/);
  assert.match(page, /useEtCalendarDate/);
  assert.match(page, /DeferredEpsChart/);
  assert.match(page, /weekPinned/);
  assert.match(chart, /lazy\(\(\) => import\('\.\/EpsHatchChart'\)\)/);
  assert.match(chart, /PRELOAD_ROOT_MARGIN = '100% 0px'/);
  assert.match(chart, /PLACEHOLDER_HEIGHT_PX = 320/);
  assert.match(chart, /if \(mounted \|\| !hasRows\) return/);
  assert.match(refresh, /useNow\(cooldownUntil > 0 \? 1000 : 0\)/);
  assert.match(calendar, /setInterval\(tick, 15_000\)/);
  assert.match(nowHook, /if \(!intervalMs \|\| intervalMs <= 0\) return/);
});
