// 页头页码、指数方向线、夜间三级墨色对比度。锁住这次壳层改动，避免页码缩回眉题、
// 缺失涨跌被画成上涨，或图表色和 CSS 变量再次分叉。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => readFile(path.join(root, rel), 'utf8');

function luminance(hex) {
  const n = hex.replace('#', '');
  const ch = [0, 2, 4].map((i) => {
    const c = parseInt(n.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

function contrast(fg, bg) {
  const hi = Math.max(luminance(fg), luminance(bg));
  const lo = Math.min(luminance(fg), luminance(bg));
  return (hi + 0.05) / (lo + 0.05);
}

test('research pages use the editorial folio instead of an inline section caption', async () => {
  const header = await read('src/components/shared/PageHeader.tsx');
  const radar = await read('src/pages/Breakouts.tsx');
  const missing = await read('src/pages/NotFound.tsx');
  assert.match(header, /className="page-folio"/);
  assert.match(header, /className="page-title /);
  assert.match(header, /className="page-lede /);
  assert.match(radar, /className="page-folio">§03</);
  assert.match(radar, /className="page-title /);
  assert.doesNotMatch(header, /text-caption font-semibold text-brand-600/);
  assert.match(missing, /className="page-folio" aria-hidden="true">404</);
  assert.match(missing, /btn-primary/);
});

test('home index cards expose a direction tone and the CTA link matches other section links', async () => {
  const home = await read('src/pages/Home.tsx');
  const css = await read('src/index.css');
  assert.match(home, /data-tone=\{tone\}/);
  assert.match(home, /q\.changePct == null \? 'unknown'/);
  assert.match(home, /className="index-instrument /);
  assert.match(home, /className="link-learn shrink-0 text-caption font-medium text-brand-700/);
  assert.match(css, /\.index-instrument\[data-tone="up"\]::before \{ background: var\(--up-600\); \}/);
  assert.match(css, /\.index-instrument\[data-tone="down"\]::before \{ background: var\(--down-600\); \}/);
  assert.doesNotMatch(css, /data-tone="unknown"\]::before \{ background: var\(--up/);
  assert.doesNotMatch(css, /\.hairline-x /);
  assert.match(css, /\.shell-wash \{/);
});

test('dark tertiary ink clears WCAG AA on paper and cards and matches the chart palette', async () => {
  const css = await read('src/index.css');
  const chart = await read('src/lib/chart.ts');
  const dark = css.slice(css.indexOf('html.dark {'));
  const ink = dark.match(/--ink-300:\s*(#[0-9A-Fa-f]{6})/)?.[1];
  const paper = dark.match(/--paper:\s*(#[0-9A-Fa-f]{6})/)?.[1];
  const card = dark.match(/--card:\s*(#[0-9A-Fa-f]{6})/)?.[1];
  assert.equal(ink, '#848D9A');
  assert.ok(paper && card, 'dark paper and card tokens');
  assert.ok(contrast(ink, paper) >= 4.5, `paper contrast ${contrast(ink, paper)}`);
  assert.ok(contrast(ink, card) >= 4.5, `card contrast ${contrast(ink, card)}`);
  assert.match(chart, /ink300: '#848D9A'/);
});
