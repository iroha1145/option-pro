/**
 * 指数跑马灯的铺满与降级。
 *
 * 起因：原先固定两套内容、每轮平移 -50%。单套约 1250px，比宽屏窄，每轮末尾
 * 右侧露出空白（1920 宽屏实测约 540px，线上同样）；-50% 还把左内边距算进了
 * 周期，接缝处每轮跳 8px。减少动态时动画停掉，第二套仍然摆在那里，同一只指数
 * 出现两次，窄屏上排不下的几只也无从看到。
 *
 * 浏览器里的实测（各宽度扫一整轮无空白、接缝 0px、Tab 进来暂停并露出焦点）
 * 做在预览上；这里钉住算法和样式约定。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { MAX_MARQUEE_COPIES, marqueeCopies, marqueeTimeAt } from '../src/lib/marquee.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const read = (p) => readFile(path.join(root, p), 'utf8');

test('副本数按轨道宽度补足：平移一整套后仍铺满', () => {
  // 单套 1253px（6 只指数或 4 只基金的实测宽度）
  assert.equal(marqueeCopies(390, 1253), 2);
  assert.equal(marqueeCopies(1440, 1253), 3);
  assert.equal(marqueeCopies(1920, 1253), 3);
  assert.equal(marqueeCopies(2560, 1253), 4);
  for (const track of [390, 1280, 1440, 1920, 2560, 3440]) {
    const copies = marqueeCopies(track, 1253);
    assert.ok(copies * 1253 >= track + 1253 || copies === MAX_MARQUEE_COPIES, `${track}px 轨道在末尾露白`);
  }
});

test('测不到宽度时退回两套，单套极窄时不超过上限', () => {
  assert.equal(marqueeCopies(0, 1253), 2);
  assert.equal(marqueeCopies(1920, 0), 2);
  assert.equal(marqueeCopies(Number.NaN, 1253), 2);
  assert.equal(marqueeCopies(1920, 32), MAX_MARQUEE_COPIES);
});

test('焦点项停到轨道起点所需的动画时间点', () => {
  assert.equal(marqueeTimeAt(0, 1253, 28000), 0);
  assert.equal(marqueeTimeAt(626.5, 1253, 28000), 14000);
  assert.equal(marqueeTimeAt(1253, 1253, 28000), 0, '整套宽度等于回到起点');
  assert.equal(marqueeTimeAt(-313.25, 1253, 28000), 21000);
  assert.equal(marqueeTimeAt(100, 1253, Number.NaN), 0);
  assert.equal(marqueeTimeAt(100, 0, 28000), 0);
});

test('动画元素只含第一套：每轮平移 -100%，左内边距不在周期里', async () => {
  const config = await read('tailwind.config.js');
  assert.match(config, /marquee: \{\s*from: \{ transform: 'translateX\(0\)' \},\s*to: \{ transform: 'translateX\(-100%\)' \},\s*\}/);
  const tape = await read('src/components/IndexTape.tsx');
  assert.match(tape, /className="marquee-track [^"]*\bpl-4\b[^"]*"/);
  assert.match(tape, /className="marquee-inner relative flex w-max shrink-0 animate-marquee items-center"/);
  assert.match(tape, /className="marquee-echo absolute inset-y-0 [^"]*\bpr-8\b[^"]*" style=\{\{ left: `\$\{\(index \+ 1\) \* 100\}%` \}\}/);
});

test('键盘焦点进入时暂停；减少动态时藏起副本、改为可横向滑动', async () => {
  const css = await read('src/index.css');
  assert.match(css, /\.marquee-track:hover \.marquee-inner,\s*\.marquee-track:has\(:focus-visible\) \.marquee-inner \{\s*animation-play-state: paused;/);
  const reduced = css.slice(css.indexOf('/* §4.2 降级：减少动态偏好 */'));
  assert.match(reduced, /\.marquee-track \{ overflow-x: auto; \}/);
  assert.match(reduced, /\.marquee-echo \{ display: none; \}/);
  assert.match(reduced, /\.marquee-track > \.marquee-label \{[^}]*position: sticky;/);
  // 鼠标点击不挪位置，只处理键盘焦点
  const tape = await read('src/components/IndexTape.tsx');
  assert.match(tape, /target\.matches\(':focus-visible'\)/);
});
