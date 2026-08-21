/**
 * 横向卡片带的可操作性回归。
 *
 * 起因：热点带 8 张 260px 的卡合计约 2200px，一定溢出；而容器是
 * `overflow-x-auto` + `no-scrollbar`，滚动条被藏起来了。触屏能划，桌面端普通
 * 鼠标完全无从滚动 —— 最右边那张永远半截，也看不出右边还有没有内容。
 *
 * 状态迁移无法在浏览器里验证：后台标签页不派发 scroll 事件（实测自建监听器
 * 也是 0 次触发）。因此把判定逻辑抽成纯函数，在这里直接测。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { computeScrollEdges } from '../src/lib/scrollEdges.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const source = (p) => readFile(path.join(src, p), 'utf8');

/* ---------- 边缘判定 ---------- */

test('内容不溢出时两侧都没有箭头', () => {
  assert.deepEqual(computeScrollEdges(0, 800, 800), { left: false, right: false });
  // 子像素带来的 1px 差不算溢出，否则不该出现的箭头会一直挂着
  assert.deepEqual(computeScrollEdges(0, 801, 800), { left: false, right: false });
});

test('溢出且在起点：只提示右边还有', () => {
  assert.deepEqual(computeScrollEdges(0, 2540, 1269), { left: false, right: true });
});

test('溢出且在中间：两侧都提示', () => {
  assert.deepEqual(computeScrollEdges(600, 2540, 1269), { left: true, right: true });
});

test('滚到最右：只提示左边，不再画一个点了没反应的右箭头', () => {
  const maxScroll = 2540 - 1269;
  assert.deepEqual(computeScrollEdges(maxScroll, 2540, 1269), { left: true, right: false });
  // 差 1px 也算到底（惯性滚动经常停在小数上）
  assert.deepEqual(computeScrollEdges(maxScroll - 0.5, 2540, 1269), { left: true, right: false });
});

test('热点带的真实尺寸：8 张 260px 卡一定溢出桌面视口', () => {
  const track = 8 * 260 + 7 * 12;  // 卡宽 + gap-3
  const edges = computeScrollEdges(0, track, 1269);
  assert.equal(edges.right, true, '这正是用户看到「最右边一张永远显示不全」的情形');
});

/* ---------- 组件契约 ---------- */

test('溢出时提供可点、可聚焦的入口，而不是只靠隐藏的滚动条', async () => {
  const code = await source('components/shared/HorizontalScroller.tsx');
  assert.match(code, /aria-label=\{side === 'left' \? t\('向左滚动'\) : t\('向右滚动'\)\}/);
  // 箭头只在该方向真的还能滚时渲染
  assert.match(code, /if \(!active\) return null;/);
  // 滚动容器要能获得焦点，键盘才能用方向键
  assert.match(code, /tabIndex=\{edges\.left \|\| edges\.right \? 0 : -1\}/);
  // 触屏保持原样：按钮只在指针设备出现
  assert.match(code, /md:inline-flex/);
});

test('不会因为 children 引用变化陷入无限重渲染', async () => {
  const code = await source('components/shared/HorizontalScroller.tsx');
  // 监听器的 effect 依赖里不能有 children：那是每次渲染都变的引用，
  // 会变成 measure → setState → 重渲染 → effect 重跑 → measure。
  assert.doesNotMatch(code, /\}, \[measure, children\]\);/);
  // 值没变必须返回原对象，让 React 跳过渲染
  assert.match(code, /prev\.left === next\.left && prev\.right === next\.right \? prev : next/);
});

test('热点带用上了这个组件', async () => {
  const strip = await source('components/catalysts/HotspotsStrip.tsx');
  assert.match(strip, /<HorizontalScroller/);
  assert.match(strip, /label=\{__t\("热点主题带，可横向滚动"\)\}/);
  // 原来那层裸的 overflow-x-auto 不该再留着
  assert.doesNotMatch(strip, /className="-mx-4 mt-4 overflow-x-auto/);
});

test('热点卡顶条被卡片圆角裁切，不会画出圆角', async () => {
  const strip = await source('components/catalysts/HotspotsStrip.tsx');
  assert.match(strip, /absolute inset-x-0 top-0 h-\[2px\]/);
  assert.match(
    strip,
    /overflow-hidden rounded-lg border border-line bg-card/,
    'heat bar is a 2px rect; without overflow-hidden it squares off the card corners',
  );
});

test('板块 chip 条也用上了这个组件，且 tablist 语义留在原处', async () => {
  const chips = await source('components/sectors/SectorChips.tsx');
  // 实测 /sectors 上这条 chip 带藏掉 1474px（2117 轨道 / 643 视口）且没有任何控件
  assert.match(chips, /<HorizontalScroller/);
  // role="tablist" 必须留在真正装 tab 的元素上，不能挪到滚动容器
  assert.match(chips, /<div ref=\{listRef\} role="tablist" aria-label=\{t\("板块切换"\)\} className="flex gap-1\.5">/);
  // 原来那层裸的 overflow-x-auto 不该再留着
  assert.doesNotMatch(chips, /overflow-x-auto py-0\.5 no-scrollbar/);
});
