// cn() 合并类名时不能丢掉自定义字阶：tailwind-merge 默认不认识 text-micro、text-data-l 等，
// 会把它们当成文字颜色，与 text-ink-* 合并时只留下颜色，字号退回继承值。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { cn } from '../src/lib/utils.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('cn keeps every custom font size next to a text colour', async () => {
  const config = await readFile(path.join(root, 'tailwind.config.js'), 'utf8');
  const block = config.slice(config.indexOf('fontSize:'), config.indexOf('maxWidth:'));
  const sizes = [...block.matchAll(/^\s*'?([a-z0-9-]+)'?\s*:\s*\[/gm)].map((m) => m[1]);
  assert.ok(sizes.includes('micro') && sizes.includes('data-l'), '字阶解析失败');
  for (const size of sizes) {
    assert.equal(cn(`text-${size}`, 'text-ink-400'), `text-${size} text-ink-400`, size);
    assert.equal(cn('text-ink-400', `text-${size}`), `text-ink-400 text-${size}`, size);
  }
});

test('cn still lets a later font size or colour win', () => {
  assert.equal(cn('text-micro', 'text-data-l'), 'text-data-l');
  assert.equal(cn('text-ink-400', 'text-up-700'), 'text-up-700');
  assert.equal(cn('text-[13px] leading-[18px]', 'text-data-l'), 'text-data-l');
});
