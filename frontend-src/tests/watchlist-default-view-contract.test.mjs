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
  assert.match(source, /\{ value: 'table', label: '表格' \}/);
  assert.match(source, /\{ value: 'cards', label: '卡片' \}/);
  assert.match(source, /cardItems\.map/);
});
