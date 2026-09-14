import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
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
