/**
 * 涨跌色彩习惯：CSS / Tailwind / CH 必须共用一份状态，
 * 且 tooltip 字体栈不能打断 HTML style 属性。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  PRICE_COLORS,
  applyColorMode,
  directionColors,
  getColorMode,
  setColorMode,
  subscribeColorMode,
} from '../src/lib/colorPreference.ts';
import { CH, heatColor, withAlpha } from '../src/lib/chart.ts';
import { CHART_MONO_FONT } from '../src/lib/chartFonts.ts';

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

test.afterEach(() => {
  setColorMode('western');
});

test('directionColors 在亚洲习惯下对调涨跌 hex', () => {
  assert.equal(directionColors('western').up600, PRICE_COLORS.western.up600);
  assert.equal(directionColors('western').down600, PRICE_COLORS.western.down600);
  assert.equal(directionColors('asian').up600, PRICE_COLORS.western.down600);
  assert.equal(directionColors('asian').down600, PRICE_COLORS.western.up600);
});

test('setColorMode 更新内存快照并通知订阅者', () => {
  const seen = [];
  const stop = subscribeColorMode(() => seen.push(getColorMode()));
  setColorMode('asian');
  assert.equal(getColorMode(), 'asian');
  assert.deepEqual(seen, ['asian']);
  setColorMode('western');
  assert.equal(getColorMode(), 'western');
  assert.deepEqual(seen, ['asian', 'western']);
  stop();
});

test('CH.up600 / CH.down600 跟随当前色彩习惯', () => {
  applyColorMode('western');
  assert.equal(CH.up600, PRICE_COLORS.western.up600);
  assert.equal(CH.down600, PRICE_COLORS.western.down600);
  applyColorMode('asian');
  assert.equal(CH.up600, PRICE_COLORS.asian.up600);
  assert.equal(CH.down600, PRICE_COLORS.asian.down600);
});

test('heatColor 在亚洲习惯下翻转涨跌两端', () => {
  applyColorMode('western');
  const westUp = heatColor(3);
  const westDown = heatColor(-3);
  applyColorMode('asian');
  assert.equal(heatColor(3), westDown);
  assert.equal(heatColor(-3), westUp);
});

test('withAlpha 能给当前涨跌色加透明度', () => {
  applyColorMode('asian');
  assert.equal(withAlpha(CH.up600, 0.4), 'rgba(229,72,77,0.4)');
  assert.equal(withAlpha(CH.down600, 0.4), 'rgba(14,159,110,0.4)');
});

test('图表等宽字体栈不含双引号，可安全插进 style="…"', () => {
  assert.equal(CHART_MONO_FONT.includes('"'), false);
  assert.match(CHART_MONO_FONT, /'SF Mono'/);
  assert.match(CHART_MONO_FONT, /'Liberation Mono'/);
});

test('CSS 亚洲模式重映射与 PRICE_COLORS.asian 一致', async () => {
  const css = await source('index.css');
  const block = css.match(/html\[data-color-mode="asian"\]\s*\{([^}]+)\}/);
  assert.ok(block, '缺少 html[data-color-mode=asian] 规则');
  const body = block[1];
  assert.match(body, new RegExp(`--up-600:\\s*${PRICE_COLORS.asian.up600}`, 'i'));
  assert.match(body, new RegExp(`--down-600:\\s*${PRICE_COLORS.asian.down600}`, 'i'));
  assert.match(body, new RegExp(`--up-700:\\s*${PRICE_COLORS.asian.up700}`, 'i'));
  assert.match(body, new RegExp(`--down-700:\\s*${PRICE_COLORS.asian.down700}`, 'i'));
});

test('Tailwind up/down 色阶从 CSS 变量生成，而不是编译期写死 hex', async () => {
  const config = await source('../tailwind.config.js');
  assert.match(config, /up:\s*\{[^}]*var\(--up-600\)/s);
  assert.match(config, /down:\s*\{[^}]*var\(--down-600\)/s);
  assert.doesNotMatch(config, /up:\s*\{[^}]*#0E9F6E/s);
  assert.doesNotMatch(config, /down:\s*\{[^}]*#E5484D/s);
});

test('顶栏与 Dock 共用 useColorMode，不再各自 useState', async () => {
  const switcher = codeOf(await source('components/ColorModeSwitcher.tsx'));
  const dock = codeOf(await source('components/MobileDock.tsx'));
  assert.match(switcher, /useColorMode\(\)/);
  assert.match(dock, /useColorMode\(\)/);
  assert.doesNotMatch(switcher, /useState/);
  assert.doesNotMatch(dock, /setLocalColorMode|getColorMode\(\)/);
});

test('K 线 / 情景 / 仓位 / 迷你 K 在色彩习惯变化时重建 option', async () => {
  const kline = codeOf(await source('components/detail/KlineChart.tsx'));
  const scenario = codeOf(await source('components/cta/ScenarioChart.tsx'));
  const history = codeOf(await source('components/cta/PositionHistoryChart.tsx'));
  const lead = codeOf(await source('components/breakouts/LeadBigCard.tsx'));
  assert.match(kline, /\[data, range, mode, prevClose, overlay, extraMarks, analysisOption, colorMode\]/);
  assert.match(scenario, /\[row, colorMode\]/);
  assert.match(history, /\[history, colorMode\]/);
  assert.match(lead, /\[data, colorMode\]/);
});
