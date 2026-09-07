/**
 * 首页/催化页共用的日历选择与国家展示，以及资源缓存合并读取。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';
import ts from 'typescript';
import { readFile } from 'node:fs/promises';

import {
  calendarCopy,
  calendarInterval,
  flatCountry,
  localDay,
  selectCalendarEvents,
} from '../src/components/catalysts/calendarPresentation.ts';

const here = path.dirname(fileURLToPath(import.meta.url));

test('国家展示去掉区旗，只留文字或两位代码', () => {
  assert.equal(flatCountry('🇺🇸 美国'), '美国');
  assert.equal(flatCountry('🇯🇵'), 'JP');
  assert.equal(flatCountry(''), '—');
});

test('UI-09 东京与纽约本地日期不被 UTC 字符串截断', () => {
  const moduleUrl = new URL('../src/components/catalysts/calendarPresentation.ts', import.meta.url).href;
  const script = `
    const { localDay } = await import(${JSON.stringify(moduleUrl)});
    const d1 = new Date('2026-09-07T15:30:00Z');
    const d2 = new Date('2026-09-08T00:30:00Z');
    const clock = (d) => [String(d.getHours()).padStart(2, '0'), String(d.getMinutes()).padStart(2, '0')].join(':');
    console.log(localDay(d1), clock(d1));
    console.log(localDay(d2), clock(d2));
  `;
  const run = (tz) => spawnSync(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', script], {
    encoding: 'utf8',
    env: { ...process.env, TZ: tz },
  });
  const tokyo = run('Asia/Tokyo');
  const newYork = run('America/New_York');
  assert.equal(tokyo.status, 0, tokyo.stderr);
  assert.equal(newYork.status, 0, newYork.stderr);
  const [tokyoA] = tokyo.stdout.trim().split('\n');
  const [, newYorkB] = newYork.stdout.trim().split('\n');
  assert.equal(tokyoA, '2026-09-08 00:30');
  assert.equal(newYorkB, '2026-09-07 20:30');
});

test('今日窗口按本地自然日，即将公布按当前时刻', () => {
  const iso = (y, m, d, h, min) => new Date(y, m - 1, d, h, min, 0, 0).toISOString();
  const now = new Date(2026, 8, 7, 12, 0, 0, 0);
  const yesterday = iso(2026, 9, 6, 23, 30);
  const morning = iso(2026, 9, 7, 8, 30);
  const evening = iso(2026, 9, 7, 21, 0);
  const tomorrow = iso(2026, 9, 8, 8, 30);
  const items = [
    { scheduledAt: yesterday, impact: 'low', actual: '1' },
    { scheduledAt: morning, impact: 'high', actual: null },
    { scheduledAt: evening, impact: 'medium', actual: null },
    { scheduledAt: tomorrow, impact: 'medium', actual: null },
  ];
  const today = selectCalendarEvents(items, now, false).map((item) => item.scheduledAt);
  const next = selectCalendarEvents(items, now, true).map((item) => item.scheduledAt);
  assert.deepEqual(today, [morning, evening]);
  assert.deepEqual(next, [evening, tomorrow]);
  assert.equal(localDay(now), '2026-09-07');
});

test('临近公布且实际值为空时加快检查间隔', () => {
  const now = Date.parse('2026-09-07T08:25:00.000Z');
  assert.equal(calendarInterval([
    { scheduledAt: '2026-09-07T08:30:00.000Z', impact: 'high', actual: null },
  ], now), 60_000);
  assert.equal(calendarInterval([
    { scheduledAt: '2026-09-07T08:30:00.000Z', impact: 'holiday', actual: null },
  ], now), 300_000);
  assert.equal(calendarInterval([
    { scheduledAt: '2026-09-07T08:30:00.000Z', impact: 'high', actual: '0.2%' },
  ], now), 300_000);
});

test('日历文案按 locale 分枝，中英日都有界面词', () => {
  assert.equal(calendarCopy('zh').title, '经济日历');
  assert.equal(calendarCopy('zh').next, '即将公布');
  assert.equal(calendarCopy('en').title, 'Economic calendar');
  assert.equal(calendarCopy('en').delayed, 'Awaiting source');
  assert.equal(calendarCopy('ja').title, '経済カレンダー');
  assert.equal(calendarCopy('ja').holiday, '休場');
});

async function loadResourceCache() {
  const source = await readFile(path.join(here, '../src/components/catalysts/resourceCache.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const require = createRequire(import.meta.url);
  const module = { exports: {} };
  const fn = new Function('exports', 'module', 'require', compiled);
  fn(module.exports, module, require);
  return module.exports.ResourceCache;
}

test('同 key 并发读取合并，重挂不重复打网', async () => {
  const ResourceCache = await loadResourceCache();
  let now = 100;
  let calls = 0;
  const cache = new ResourceCache(undefined, () => now);
  const policy = { freshMs: 1000, retainMs: 10_000 };
  const data = { v: 1 };
  const load = async () => {
    calls += 1;
    return data;
  };
  await Promise.all([cache.ensure('a', policy, load), cache.ensure('a', policy, load)]);
  const old = cache.snapshot('a', policy);
  now = 500;
  await cache.ensure('a', policy, load);
  assert.equal(calls, 1);
  assert.equal(cache.snapshot('a', policy), old);
  assert.equal(old.validatedAt, 100);
});

for (const [label, retryAfter, delay] of [
  ['服务器指定的重试期限', 120, 120_000],
  ['请求失败后的退避期限', undefined, 15_000],
]) {
  test(`后台失效通知保留${label}，到期后恢复读取`, async () => {
    const ResourceCache = await loadResourceCache();
    let now = 1000;
    let calls = 0;
    let recovered = false;
    const old = { version: 1 };
    const fresh = { version: 2 };
    const cache = new ResourceCache(undefined, () => now);
    const policy = { freshMs: 1000, retainMs: 300_000 };
    const unsubscribe = cache.subscribe('feed', policy, () => {});
    const load = async () => {
      calls += 1;
      if (calls === 1) return old;
      if (recovered) return fresh;
      const error = new Error('temporarily unavailable');
      error.retryAfter = retryAfter;
      throw error;
    };
    await cache.ensure('feed', policy, load);
    now = 2000;
    await cache.ensure('feed', policy, load);
    assert.equal(calls, 2);
    now += delay - 1;
    cache.invalidate();
    cache.tick();
    await cache.ensure('feed', policy, load);
    assert.equal(calls, 2, '后台通知不得提前重试');
    assert.equal(cache.snapshot('feed', policy).data, old, '等待期间保留上次数据');
    recovered = true;
    now += 1;
    cache.tick();
    await cache.ensure('feed', policy, load);
    assert.equal(calls, 3);
    assert.equal(cache.snapshot('feed', policy).data, fresh);
    assert.equal(cache.snapshot('feed', policy).error, null);
    unsubscribe();
  });
}

test('保留重试期限后，失效前的旧响应仍不能覆盖新数据', async () => {
  const ResourceCache = await loadResourceCache();
  let calls = 0;
  let resolveOld;
  const oldResponse = new Promise((resolve) => { resolveOld = resolve; });
  const fresh = { version: 2 };
  const cache = new ResourceCache();
  const policy = { freshMs: 1000, retainMs: 10_000 };
  const load = async () => (++calls === 1 ? oldResponse : fresh);
  const first = cache.ensure('feed', policy, load);
  await Promise.resolve();
  assert.equal(calls, 1);
  cache.invalidate();
  await cache.ensure('feed', policy, load);
  resolveOld({ version: 1 });
  await first;
  assert.equal(cache.snapshot('feed', policy).data, fresh);
  assert.equal(cache.snapshot('feed', policy).refreshing, false);
});
