// 构建一致性冒烟（node --test tests/build.test.mjs）。
// CI 会执行 `VITE_API_MODE=live npm run build` 后 `diff -r frontend-src/dist frontend`
// 做全量一致性；这里只做最小冒烟：dist 存在时校验两边 index.html 完全一致，
// dist 不存在（例如未在本机构建）时跳过该断言并说明原因。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(here, '..', 'dist'); // frontend-src/dist（本地构建输出）
const artifactDir = path.resolve(here, '..', '..', 'frontend'); // 仓库提交的构建产物

test('committed build artifact matches the local Vite build output', async t => {
  if (!existsSync(path.join(distDir, 'index.html'))) {
    t.skip('frontend-src/dist 不存在（本机尚未构建）；一致性由 CI 的 diff -r 全量把关');
    return;
  }
  const [distIndex, artifactIndex] = await Promise.all([
    readFile(path.join(distDir, 'index.html'), 'utf8'),
    readFile(path.join(artifactDir, 'index.html'), 'utf8'),
  ]);
  assert.equal(
    distIndex,
    artifactIndex,
    'frontend-src/dist/index.html 与仓库 frontend/index.html 不一致：请重新构建并同步产物',
  );
});

test('committed artifact directory is servable (index.html present)', () => {
  assert.ok(existsSync(path.join(artifactDir, 'index.html')), '仓库 frontend/index.html 必须存在');
});
