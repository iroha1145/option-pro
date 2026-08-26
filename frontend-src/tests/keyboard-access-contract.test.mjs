/**
 * 键盘与无障碍回归（GPT-5.6-Pro 审计 P3-1 … P3-5）
 *
 * 这一批的共同形状是：交互只对鼠标存在。可排序表头绑在 <th> 上、可点击行没有
 * tabIndex、模态没有焦点圈定、跑马灯把每个指数暴露两遍、分段控件只有 role
 * 没有键盘行为。
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

function codeOf(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trimStart();
      return !trimmed.startsWith('//') && !trimmed.startsWith('*');
    })
    .join('\n');
}

/* ---------------- P3-1：表格排序与行跳转 ---------------- */

test('可排序表头是真正的按钮，不是绑在 th 上的 onClick', async () => {
  const table = codeOf(await source('components/shared/DataTable.tsx'));
  assert.doesNotMatch(table, /onClick=\{c\.sortable \? \(\) => toggleSort\(c\.key\) : undefined\}/);
  assert.match(table, /<button\s*\n\s*type="button"\s*\n\s*onClick=\{\(\) => toggleSort\(c\.key\)\}/);
  // aria-sort 仍然留在 th 上，那是它的正确位置
  assert.match(table, /aria-sort=\{sort\?\.key === c\.key/);
});

test('可点击行可聚焦并响应 Enter / 空格', async () => {
  const table = codeOf(await source('components/shared/DataTable.tsx'));
  assert.match(table, /tabIndex: 0,/);
  assert.match(table, /role: 'button' as const,/);
  assert.match(table, /event\.key === 'Enter' \|\| event\.key === ' '/);
  // 焦点必须看得见
  assert.match(table, /focus-visible:ring-2/);
});

/* ---------------- P3-2 / P3-3：焦点圈定与归还 ---------------- */

test('焦点圈定同时做三件事：移入、限制 Tab、关闭后归还', async () => {
  const hook = codeOf(await source('hooks/useFocusTrap.ts'));
  assert.match(hook, /initial\.focus\(\{ preventScroll: true \}\)/);
  assert.match(hook, /if \(event\.key !== 'Tab'\) return;/);
  assert.match(hook, /previouslyFocused\.focus\(\{ preventScroll: true \}\)/);
  // 触发者可能已被卸载，不能盲目抢焦点
  assert.match(hook, /document\.contains\(previouslyFocused\)/);
  // 焦点已经跑到外面时要拉回来
  assert.match(hook, /!container\.contains\(current\)/);
});

test('抽屉、命令面板与事件详情都启用了焦点圈定', async () => {
  for (const [file, expected] of [
    ['components/Drawer.tsx', /useFocusTrap\(panelsRef, open\)/],
    ['components/CommandPalette.tsx', /useFocusTrap\(panelRef, open, \{ initialFocusRef: inputRef \}\)/],
    ['components/breakouts/EventDetail.tsx', /useFocusTrap\(panelRef, event !== null\)/],
  ]) {
    assert.match(codeOf(await source(file)), expected, `${file} 未启用焦点圈定`);
  }
});

/* ---------------- P3-4：跑马灯不得暴露两份按钮 ---------------- */

test('跑马灯第二套副本对键盘与读屏软件不可见', async () => {
  const tape = await source('components/IndexTape.tsx');
  const code = codeOf(tape);
  const rows = code.match(/<TapeRow /g) ?? [];
  assert.equal(rows.length, 2, '无缝滚动仍需要两套');
  assert.match(code, /aria-hidden="true" inert>/);
  // 只有第二套被屏蔽：第一套必须仍然可用
  const [firstHalf, secondHalf] = code.split('aria-hidden="true" inert>');
  assert.match(firstHalf, /<TapeRow /);
  assert.match(secondHalf, /<TapeRow /);
});

/* ---------------- P3-5：分段控件键盘导航 ---------------- */

test('分段控件实现 roving tabindex 与方向键', async () => {
  const segmented = codeOf(await source('components/shared/Segmented.tsx'));
  // 选中判定与 roving tabindex 是同一个谓词，别再各写一遍（审查 #113）
  assert.match(segmented, /const active = value === o\.value;/);
  assert.match(segmented, /tabIndex=\{active \? 0 : -1\}/);
  assert.match(segmented, /event\.key === 'ArrowRight' \|\| event\.key === 'ArrowDown'/);
  assert.match(segmented, /event\.key === 'ArrowLeft' \|\| event\.key === 'ArrowUp'/);
  assert.match(segmented, /event\.key === 'Home'/);
  assert.match(segmented, /event\.key === 'End'/);
  // 方向键要环绕，而不是停在两端
  assert.match(segmented, /\(index \+ step \+ options\.length\) % options\.length/);
});
