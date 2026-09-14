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
  assert.match(spa, /更多/);
  assert.doesNotMatch(spa, /text=新闻/);
  const pages = await read('scripts/perf/measure_pages.mjs');
  const ready = await read('scripts/perf/lib/page_ready.mjs');
  assert.match(pages, /\/cta/);
  assert.match(pages, /\/stock\/NVDA/);
  assert.match(pages, /this-page-is-not-a-route/);
  assert.match(pages, /content_p75/);
  assert.match(pages, /error_rate/);
  assert.match(ready, /export function classifyPageReady/);
  assert.match(ready, /行情服务暂不可用/);
  assert.match(ready, /页面不存在/);
  assert.match(ready, /data-optix-region/);
  assert.match(ready, /idle/);
  assert.doesNotMatch(ready, /main\.length\s*>\s*(40|80)/);
  assert.doesNotMatch(pages, /button, form, input/);
  const interleaved = await read('scripts/perf/measure_interleaved.mjs');
  assert.match(interleaved, /interleaved/);
  assert.match(interleaved, /OPTIX_PERF_UNOPT_BASE/);
  const faults = await read('scripts/perf/run_faults.mjs');
  assert.match(faults, /feed_429_then_retry/);
  assert.match(faults, /internetdisconnected/);
  assert.match(faults, /更新失败，保留上次数据/);
  assert.match(faults, /first_load_disconnect_then_retry/);
  const cache = await read('frontend-src/src/components/catalysts/resourceCache.ts');
  assert.match(cache, /userInitiated/);
  assert.match(cache, /entry\.retryAt = 0/);
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
