/**
 * 站内 title 提示改由网页绘制（2026-10-06）
 *
 * 原生 title 由操作系统画，外观与站内提示两套。TitleTooltipLayer 在文档级接管：
 * 悬停时借走 title、离开时还回。这里钉住三件事：全站挂载、只接管鼠标、title 一定还回去。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

async function source(relativePath) {
  return readFile(path.join(src, relativePath), 'utf8');
}

test('App mounts the title tooltip layer inside the access provider (covers /login too)', async () => {
  const app = await source('App.tsx');
  assert.match(app, /import TitleTooltipLayer from '@\/components\/shared\/TitleTooltipLayer'/);
  const layer = app.indexOf('<TitleTooltipLayer />');
  assert.ok(layer > app.indexOf('<AccessProvider>') && layer < app.indexOf('</AccessProvider>'));
  assert.ok(layer > app.indexOf('</Routes>'), '挂在路由之外，登录页与 404 也生效');
});

test('only mouse pointers are taken over; touch and pen keep default behaviour', async () => {
  const layer = await source('components/shared/TitleTooltipLayer.tsx');
  const guards = layer.match(/if \(event\.pointerType !== 'mouse'\) return;/g) ?? [];
  assert.equal(guards.length, 2, 'pointerover 与 pointerout 都只认鼠标');
});

test('the borrowed title is restored on release and React rewrites are re-borrowed', async () => {
  const layer = await source('components/shared/TitleTooltipLayer.tsx');
  assert.match(layer, /owner\.setAttribute\('title', value\)/, '离开时把 title 原样还回');
  assert.match(layer, /attributeFilter: \['title'\]/, '悬停期间 React 改写 title 要再借走，原生提示不能冒出来');
  assert.match(layer, /aria-hidden="true"/, '浮层内容与元素自己的 title 重复，读屏不再朗读');
  assert.match(layer, /window\.addEventListener\('blur', release\)/);
});

test('borrowing writes an empty title so a React removal during hover is not undone', async () => {
  const layer = await source('components/shared/TitleTooltipLayer.tsx');
  const borrow = layer.slice(layer.indexOf('const borrow ='), layer.indexOf('const hide ='));
  assert.match(borrow, /element\.setAttribute\('title', ''\)/, '借走 = 写空串：删属性的话 React 再删不留变更记录');
  assert.doesNotMatch(borrow, /removeAttribute\('title'\)/);
  assert.match(layer, /if \(value !== null && owner\.getAttribute\('title'\) === ''\) owner\.setAttribute\('title', value\)/,
    '只在仍是借走的空串时还回，React 删掉或改写过就以 React 为准');
  assert.match(layer, /if \(!target\.hasAttribute\('title'\)\) \{\s*\/\/[^\n]*\n\s*target\.removeAttribute\(BORROWED\);/,
    'React 删掉 title 时放弃借用');
  assert.match(layer, /document\.addEventListener\('pointermove', onPointer\)/, '悬停后才出现的 title 也要接管');
});
