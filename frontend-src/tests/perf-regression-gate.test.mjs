/**
 * Lightweight performance contracts for ordinary CI.
 * Heavy browser/load jobs stay in scripts/perf and must not be imported here.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..', '..');

async function read(rel) {
  return readFile(path.join(repo, rel), 'utf8');
}

test('实验室计时仍以真实新闻标题为准，不用骨架屏冒充完成', async () => {
  const browser = await read('scripts/perf/measure_browser.mjs');
  assert.match(browser, /news_content_ready/);
  assert.match(browser, /article h3/);
  assert.match(browser, /title\.textContent/);
  assert.doesNotMatch(browser, /SkeletonBlock/);
  const interact = await read('scripts/perf/measure_interact.mjs');
  assert.match(interact, /window_hours=24/);
  assert.match(interact, /limit=12/);
  assert.match(interact, /dialog\?\.querySelector\('h2'\)/);
  assert.match(interact, /drawer_detail_ms/);
  assert.match(interact, /OPTIX_PERF_INTERACT_EXTRA/);
  assert.match(interact, /按代码过滤/);
  const spa = await read('scripts/perf/measure_spa.mjs');
  assert.match(spa, /新闻催化/);
  assert.doesNotMatch(spa, /text=新闻/);
  const pages = await read('scripts/perf/measure_pages.mjs');
  assert.match(pages, /\/cta/);
  assert.match(pages, /\/stock\/NVDA/);
  assert.match(pages, /页面不存在/);
  const interleaved = await read('scripts/perf/measure_interleaved.mjs');
  assert.match(interleaved, /interleaved/);
  assert.match(interleaved, /OPTIX_PERF_UNOPT_BASE/);
  const faults = await read('scripts/perf/run_faults.mjs');
  assert.match(faults, /feed_429_then_retry/);
  assert.match(faults, /internetdisconnected/);
});

test('启动预取与非首屏让路约束仍在', async () => {
  const boot = await read('frontend-src/public/theme-boot.js');
  const idle = await read('frontend-src/src/lib/afterLoadIdle.ts');
  const client = await read('frontend-src/src/api/client.ts');
  assert.match(boot, /\/api\/access\/status/);
  assert.match(boot, /window_hours=72&include_unanalyzed=true&include_neutral=true&limit=12/);
  assert.match(client, /function consumeBootPrefetch/);
  const idleCode = idle
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('//'))
    .join('\n');
  assert.match(idleCode, /setTimeout\(run, delayMs\)/);
  assert.doesNotMatch(idleCode, /requestIdleCallback/);
});
