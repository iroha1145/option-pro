// 色阶契约：源码里用到的 <utility>-<scale>-<step> 必须真的定义在 tailwind.config.js，
// 否则 Tailwind 不生成任何规则，类名静默失效（2026-07-27 审计 2.4.7/3.2：
// 19 处 text-ink-700 / text-warn-700 全部退回继承色，视觉层级消失）。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { strengthBarClass } from '../src/lib/strengthColor.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const srcDir = path.join(root, 'src');

test('shared strength color helper preserves score thresholds after moving out of the component', () => {
  for (const [score, expected] of [
    [0, 'bg-ink-300'], [49.9, 'bg-ink-300'], [50, 'bg-brand-400'],
    [69.9, 'bg-brand-400'], [70, 'bg-brand-600'], [84.9, 'bg-brand-600'],
    [85, 'bg-up-600'], [100, 'bg-up-600'],
  ]) assert.equal(strengthBarClass(score), expected);
});

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(p)));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(p);
  }
  return out;
}

test('every ink/warn/brand/up/down/ai scale step used in src exists in tailwind.config.js', async () => {
  const config = await readFile(path.join(root, 'tailwind.config.js'), 'utf8');
  const defined = new Set();
  // 解析 config 里的 `ink: { 900: …, 700: … }` 等数字键
  for (const [, scale, body] of config.matchAll(
    /(ink|warn|brand|up|down|ai)\s*:\s*\{([^}]*)\}/g,
  )) {
    for (const [, step] of body.matchAll(/(?:^|[\s,{])(\d{2,3})\s*:/g)) {
      defined.add(`${scale}-${step}`);
    }
  }
  assert.ok(defined.has('ink-900') && defined.has('brand-600'), '配置解析失败');

  const offenders = [];
  for (const file of await walk(srcDir)) {
    if (file.includes(`${path.sep}components${path.sep}ui${path.sep}`)) continue; // shadcn 基座另有令牌体系
    const text = await readFile(file, 'utf8');
    for (const match of text.matchAll(
      /(?:text|bg|border|decoration|ring|fill|stroke|from|to|via|divide|outline|shadow|caret|accent)-(ink|warn|brand|up|down|ai)-(\d{2,3})(?![\d-])/g,
    )) {
      const key = `${match[1]}-${match[2]}`;
      if (!defined.has(key)) {
        offenders.push(`${path.relative(root, file)} → ${match[0]}`);
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `发现引用未定义色阶的类名（Tailwind 不会为它们生成规则）：\n${offenders.join('\n')}`,
  );
});
