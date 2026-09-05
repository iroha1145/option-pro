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

// ── 资源引用：全部 /assets/ 绝对根路径，且对应文件真实存在 ──────────────────
// （原为 ./assets/ 相对路径；/stock/NVDA 这类二级深链硬刷新时相对路径会解析成
//  /stock/assets/* 直接 404 白屏，详情页改全屏整页后 base 已切绝对根。）
const scriptRefs = [...index.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(match => match[1]);
const styleRefs = [...index.matchAll(/<link\s+rel="stylesheet"[^>]*\bhref="([^"]+)"/g)].map(match => match[1]);
for (const ref of [...scriptRefs, ...styleRefs]) {
  assert.match(ref, /^\/assets\//, `脚本/样式引用必须是 /assets/ 绝对根路径：${ref}`);
  await assert.doesNotReject(
    access(path.join(artifactDir, ref.replace(/^\//, ''))),
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
assert.match(index, /rel="icon"[^>]*href="\/logo\.svg"/, '本地 favicon（logo.svg）必须被链接');

// ── assets 目录（路由级分包时代）────────────────────────────────────────────
// HTML 只引用唯一的 index 入口 js + 唯一入口 css；路由可另有按需加载的样式。
// 全部文件必须带 Vite 内容哈希（后端对 /assets/ 下发 immutable 一年缓存的前提），
// 目录不得混入 js/css 以外的文件。
const assetEntries = (await readdir(path.join(artifactDir, 'assets'))).sort();
const jsAssets = assetEntries.filter(name => name.endsWith('.js'));
const cssAssets = assetEntries.filter(name => name.endsWith('.css'));
assert.ok(jsAssets.length >= 1, 'assets 下必须存在 js 产物');
assert.ok(cssAssets.length >= 1, 'assets 下必须存在 css 产物');
for (const name of [...jsAssets, ...cssAssets]) {
  assert.match(
    name,
    /-[A-Za-z0-9_-]{8,}\.(?:js|css)$/,
    `assets 文件必须带内容哈希（immutable 缓存契约）：${name}`,
  );
}
const entryJsRefs = scriptRefs.filter(ref => /^\/assets\/index-[^/]+\.js$/.test(ref));
assert.equal(entryJsRefs.length, 1, `index.html 应恰好引用 1 个 index-*.js 入口，实际：${scriptRefs.join(', ') || '无'}`);
assert.equal(scriptRefs.length, 1, 'index.html 除入口外不得直接引用其他 js（chunk 走动态 import）');
assert.equal(styleRefs.length, 1, 'index.html 应恰好引用 1 个样式表');
assert.match(styleRefs[0], /^\/assets\/index-[^/]+\.css$/, 'CI 就绪探针依赖 index-*.css 命名');
const builtScripts = await Promise.all(jsAssets.map(name => readFile(path.join(artifactDir, 'assets', name), 'utf8')));
for (const name of cssAssets) {
  assert.ok(
    styleRefs.includes(`/assets/${name}`) || builtScripts.some(source => source.includes(`assets/${name}`)),
    `按需样式必须有实际加载入口，不能遗留无引用产物：${name}`,
  );
}
for (const ref of [...scriptRefs, ...styleRefs]) {
  assert.ok(
    assetEntries.includes(ref.replace(/^\/assets\//, '')),
    `index.html 引用必须落在 assets 目录内：${ref}`,
  );
}
assert.deepEqual(
  assetEntries.filter(name => !name.endsWith('.js') && !name.endsWith('.css')),
  [],
  'assets 目录不得混入 js/css 以外的文件',
);

console.log(`Frontend build assertions passed for ${artifactDir} (${jsAssets.length} js chunks + ${cssAssets.length} css, entry ${entryJsRefs[0]}).`);
