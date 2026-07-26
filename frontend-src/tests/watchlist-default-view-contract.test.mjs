import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(here, '..', 'src', 'pages', 'Watchlist.tsx');

test('自选页默认使用卡片视图并保留真实表格切换', async () => {
  const source = await readFile(sourcePath, 'utf8');

  assert.match(source, /useState<'table' \| 'cards'>\('cards'\)/);
  assert.match(source, /\{ value: 'table', label: t\('表格'\) \}/);
  assert.match(source, /\{ value: 'cards', label: t\('卡片'\) \}/);
  // 卡片流渲染的是 cardItems 经渐进挂载后的切片（renderedCards）：
  // 视图默认仍是卡片，排序仍在完整的 cardItems 上完成。
  assert.match(source, /useProgressiveList\(cardItems/);
  assert.match(source, /renderedCards\.map/);
});
