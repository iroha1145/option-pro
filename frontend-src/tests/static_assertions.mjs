// 生产构建产物静态断言（自旧 frontend/tests/static_assertions.mjs 移植，面向 Vite SPA 产物）。
// 断言对象是仓库 ../frontend（frontend-src/dist 的提交副本，由 CI diff 保证一致）。
// 直接以 node 运行：node tests/static_assertions.mjs
import assert from 'node:assert/strict';
import { readFile, readdir, access } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
// frontend-src/tests → 仓库根/frontend（构建产物）
const artifactDir = path.resolve(here, '..', '..', 'frontend');

const index = await readFile(path.join(artifactDir, 'index.html'), 'utf8');

// ── 文档骨架 ────────────────────────────────────────────────────────────────
assert.match(index, /<html lang="zh-CN">/, '生产文档语言必须保持简体中文');
assert.match(index, /<title>Optix Pro — 投资研究工作台<\/title>/, '产品标题必须保持统一');
assert.match(index, /<div id="root"><\/div>/, 'SPA 挂载点 <div id="root"> 必须存在');

// ── 脚本纪律：不允许内联 <script>，只允许带 src 的 module script ─────────────
const scriptTags = [...index.matchAll(/<script\b[^>]*>/g)].map(match => match[0]);
assert.ok(scriptTags.length >= 1, 'index.html 必须引用构建脚本');
for (const tag of scriptTags) {
  assert.match(tag, /\bsrc="[^"]+"/, `不允许内联 <script>（严格 CSP）：${tag}`);
  assert.match(tag, /type="module"/, `构建脚本必须是 module script：${tag}`);
}

// ── 资源引用：全部 ./assets/ 相对路径，且对应文件真实存在 ────────────────────
const scriptRefs = [...index.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(match => match[1]);
const styleRefs = [...index.matchAll(/<link\s+rel="stylesheet"[^>]*\bhref="([^"]+)"/g)].map(match => match[1]);
for (const ref of [...scriptRefs, ...styleRefs]) {
  assert.match(ref, /^\.\/assets\//, `脚本/样式引用必须是 ./assets/ 相对路径：${ref}`);
  await assert.doesNotReject(
    access(path.join(artifactDir, ref.replace(/^\.\//, ''))),
    `index.html 引用的文件必须存在：${ref}`,
  );
}

// ── 无远程资源：整个文档不得出现 http(s):// 外链（严格 CSP、离线可用） ───────
assert.doesNotMatch(index, /\b(?:src|href)="https?:\/\//, 'index.html 不得引用远程资源');
assert.doesNotMatch(
  index,
  /fonts\.(?:googleapis|gstatic)\.com|use\.typekit\.net|use\.fontawesome\.com/i,
  '生产前端不得依赖远程字体服务',
);

// ── 根部公共 SVG（后端网关 _ROOT_PUBLIC_ASSETS 登录前放行清单）必须齐全 ──────
for (const svg of [
  'logo.svg',
  'login-motif.svg',
  'texture-dots.svg',
  'empty-scan.svg',
  'empty-radar.svg',
  'empty-news.svg',
  'empty-watchlist.svg',
  'empty-chart.svg',
]) {
  await assert.doesNotReject(access(path.join(artifactDir, svg)), `根部公共资产缺失：${svg}`);
}
assert.match(index, /rel="icon"[^>]*href="\.\/logo\.svg"/, '本地 favicon（logo.svg）必须被链接');

// ── assets 目录：恰好 1 个 js + 1 个 css，且与 index.html 引用完全一致 ───────
const assetEntries = (await readdir(path.join(artifactDir, 'assets'))).sort();
const jsAssets = assetEntries.filter(name => name.endsWith('.js'));
const cssAssets = assetEntries.filter(name => name.endsWith('.css'));
assert.equal(jsAssets.length, 1, `assets 下应恰有 1 个 js，实际：${jsAssets.join(', ') || '无'}`);
assert.equal(cssAssets.length, 1, `assets 下应恰有 1 个 css，实际：${cssAssets.join(', ') || '无'}`);
assert.deepEqual(
  [...scriptRefs, ...styleRefs].map(ref => ref.replace(/^\.\/assets\//, '')).sort(),
  [...jsAssets, ...cssAssets].sort(),
  'index.html 的资源引用必须与 assets 目录内容一一对应',
);
assert.deepEqual(
  assetEntries.filter(name => !name.endsWith('.js') && !name.endsWith('.css')),
  [],
  'assets 目录不得混入 js/css 以外的文件',
);

console.log(`Frontend build assertions passed for ${artifactDir} (1 js + 1 css + ${scriptRefs.length + styleRefs.length} references verified).`);
