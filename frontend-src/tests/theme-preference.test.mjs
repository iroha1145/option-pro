/**
 * 夜间模式：跟随系统 / 手动浅色 / 手动深色共用一份状态。
 * 画布色必须对上 Cloud Monitor，顶栏与登录页都要有开关。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  PRICE_COLORS_DARK,
  applyColorMode,
  directionColors,
  setColorMode,
} from '../src/lib/colorPreference.ts';
import { CH } from '../src/lib/chart.ts';
import { manualLineInk, renderPatternInk } from '../src/components/detail/chart-drawings/linePresentation.ts';
import { drawingPaint } from '../src/components/detail/chart-drawings/drawingAppearance.ts';
import {
  THEME_KEY,
  applyAppearance,
  getAppearance,
  getThemePreference,
  resolveAppearance,
  setThemePreference,
  subscribeAppearance,
} from '../src/lib/themePreference.ts';

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
  setThemePreference('system');
});

test('画线与标签随主题切换，深色没有白色底板或白色光晕', () => {
  const pattern = { id: 'support', kind: 'support_trend', status: 'active', confidence: 90, label: '支撑' };
  const geometry = { segments: [{ a: { x: 0, y: 10 }, b: { x: 10, y: 12 } }], fill: null };
  const context = { xMin: 0, xMax: 10, yMin: 5, yMax: 20 };
  const render = () => renderPatternInk(pattern, geometry, context).lines[0][0];
  setThemePreference('light');
  const light = render();
  assert.equal(light.label.backgroundColor, 'rgba(255,255,255,0.97)');
  setThemePreference('dark');
  const dark = render();
  /* 2026-10-09 Arc 改版：深色卡片改为纯灰 #161617，画线标签底与光晕跟着换。 */
  assert.equal(dark.label.backgroundColor, 'rgba(22,22,23,0.97)');
  assert.equal(dark.lineStyle.shadowColor, 'rgba(22,22,23,0.95)');
  assert.notEqual(dark.lineStyle.color, light.lineStyle.color);
  setThemePreference('light');
  assert.deepEqual(render(), light);
});

test('夜间只调整内置画线颜色的显示，保留自定义颜色与手绘宽度', () => {
  const saved = { color: '#3D4A68', width: 4 };
  setThemePreference('dark');
  const ink = manualLineInk(saved.color, saved.width);
  assert.equal(ink.color, '#B0B6C0');
  assert.equal(ink.width, 4);
  assert.deepEqual(saved, { color: '#3D4A68', width: 4 });
  assert.equal(drawingPaint('#123ABC'), '#123ABC');
  setThemePreference('light');
  assert.equal(manualLineInk(saved.color, saved.width).color, saved.color);
});

test('未选择时默认跟随系统，resolveAppearance 按设备 color-scheme 解析', () => {
  assert.equal(resolveAppearance('light'), 'light');
  assert.equal(resolveAppearance('dark'), 'dark');
  const previous = globalThis.window;
  globalThis.window = {
    matchMedia: (query) => ({ matches: String(query).includes('dark') }),
  };
  try {
    assert.equal(resolveAppearance('system'), 'dark');
  } finally {
    if (previous) globalThis.window = previous;
    else delete globalThis.window;
  }
});

test('setThemePreference 更新偏好、解析结果并通知订阅者', () => {
  const seen = [];
  const stop = subscribeAppearance(() => seen.push(`${getThemePreference()}:${getAppearance()}`));
  setThemePreference('dark');
  assert.equal(getThemePreference(), 'dark');
  assert.equal(getAppearance(), 'dark');
  setThemePreference('light');
  assert.equal(getThemePreference(), 'light');
  assert.equal(getAppearance(), 'light');
  stop();
  assert.deepEqual(seen, ['dark:dark', 'light:light']);
});

test('applyAppearance 在深色时给 html 加上 dark class 与 theme-color', () => {
  const attrs = new Map();
  const classList = new Set();
  const meta = {
    content: '#FAFAFA',
    setAttribute(name, value) {
      if (name === 'content') this.content = value;
    },
  };
  const previousDocument = globalThis.document;
  globalThis.document = {
    documentElement: {
      classList: {
        toggle(name, on) {
          if (on) classList.add(name);
          else classList.delete(name);
        },
        contains(name) {
          return classList.has(name);
        },
      },
      dataset: {},
      style: {},
      setAttribute(name, value) {
        attrs.set(name, value);
      },
    },
    querySelector: (selector) => (String(selector).includes('theme-color') ? meta : null),
  };
  try {
    applyAppearance('dark');
    assert.equal(classList.has('dark'), true);
    assert.equal(globalThis.document.documentElement.dataset.theme, 'dark');
    assert.equal(globalThis.document.documentElement.style.colorScheme, 'dark');
    assert.equal(meta.content, '#0F0F10');
    applyAppearance('light');
    assert.equal(classList.has('dark'), false);
    assert.equal(meta.content, '#FAFAFA');
  } finally {
    if (previousDocument) globalThis.document = previousDocument;
    else delete globalThis.document;
  }
});

test('夜间涨跌色切到 Cloud Monitor 的 ok/crit，亚洲习惯仍然对调', () => {
  setThemePreference('dark');
  applyColorMode('western');
  assert.equal(directionColors().up600, PRICE_COLORS_DARK.western.up600);
  assert.equal(directionColors().down600, PRICE_COLORS_DARK.western.down600);
  assert.equal(CH.up600, PRICE_COLORS_DARK.western.up600);
  applyColorMode('asian');
  assert.equal(CH.up600, PRICE_COLORS_DARK.asian.up600);
  assert.equal(CH.down600, PRICE_COLORS_DARK.asian.down600);
});

/* 2026-10-09 Arc 改版：深色画布由 Cloud Monitor 的蓝灰改为单独调过的纯灰（页面最深、卡片抬一档）。 */
test('CSS 暗色画布用 Arc 改版的纯灰令牌', async () => {
  const css = await source('index.css');
  const block = css.match(/html\.dark\s*\{([\s\S]*?)\n  \}/);
  assert.ok(block, '缺少 html.dark 规则');
  const body = block[1];
  for (const [token, value] of [
    ['--paper', '#0F0F10'],
    ['--card', '#161617'],
    ['--paper-2', '#1A1A1C'],
    ['--ink-900', '#F2F2F2'],
    ['--line', '#262628'],
    ['--line-strong', '#38383B'],
    ['--card-warm', '#141415'],
  ]) {
    assert.match(body, new RegExp(`${token}:\\s*${value}`, 'i'), `${token} 应对上深色纯灰令牌`);
  }
});

test('暗色模式下按钮高光变量去除白边，避免夜间模式按钮泛白', async () => {
  const css = await source('index.css');
  const darkBlock = css.match(/html\.dark\s*\{([\s\S]*?)\n  \}/);
  assert.ok(darkBlock, '缺少 html.dark 规则');
  const darkBody = darkBlock[1];
  for (const token of [
    '--btn-shadow',
    '--btn-hi-shadow',
    '--btn-primary-highlight',
    '--chip-shadow',
    '--card-hover-shadow',
    '--inset-hi-shadow',
    '--zone-shadow',
  ]) {
    assert.doesNotMatch(darkBody, new RegExp(`${token}:.*rgba\\(255,\\s*255,\\s*255`), `${token} 夜间不得再带白高光`);
  }
  const tailwind = await source('../tailwind.config.js');
  assert.match(tailwind, /btn:\s*'var\(--btn-shadow\)'/);
  assert.match(tailwind, /'btn-hi':\s*'var\(--btn-hi-shadow\)'/);
});

test('theme-boot 与运行时共用 optix_theme 键，并按系统色决定默认夜间', async () => {
  const boot = await readFile(path.resolve(here, '..', 'public', 'theme-boot.js'), 'utf8');
  assert.match(boot, new RegExp(THEME_KEY));
  assert.match(boot, /prefers-color-scheme:\s*dark/);
  assert.match(boot, /classList\.toggle\("dark"/);
  assert.match(boot, /#0F0F10/);
  assert.doesNotMatch(boot, /\?\./);
  assert.match(boot, /if \(themeColor\)/);
  const html = await readFile(path.resolve(here, '..', 'index.html'), 'utf8');
  assert.match(html, /src="\/theme-boot\.js"/);
  assert.match(html, /name="theme-color"/);
});

test('系统外观监听兼容 addListener，清空本地存储会重置偏好', async () => {
  const sourceText = await source('lib/themePreference.ts');
  assert.match(sourceText, /addListener\(/);
  assert.match(sourceText, /event\.key !== THEME_KEY && event\.key !== null/);
});

test('页头设置菜单（桌面与手机同一入口）与登录页都有外观开关', async () => {
  const navbar = codeOf(await source('components/Navbar.tsx'));
  const login = codeOf(await source('pages/Login.tsx'));
  const settings = codeOf(await source('components/SettingsMenu.tsx'));
  const switcher = codeOf(await source('components/ThemeSwitcher.tsx'));
  assert.match(navbar, /<SettingsMenu\s*\/>/);
  assert.doesNotMatch(navbar, /<SettingsMenu[^>]*hidden/);
  assert.match(settings, /setThemePreference\(option\.value\)/);
  assert.match(settings, /跟随系统/);
  assert.match(settings, /useThemePreference\(\)/);
  assert.match(login, /<ThemeSwitcher\s*\/>/);
  assert.match(switcher, /useThemePreference\(\)/);
  assert.match(switcher, /setThemePreference/);
  assert.match(switcher, /role="menuitemradio"/);
});

test('手机底栏不再放外观开关：外观只在页头设置菜单里改', async () => {
  /* 去掉注释再匹配：文件头的说明里会提到「外观」，注释不算数。 */
  const dock = codeOf(await source('components/MobileDock.tsx'));
  assert.doesNotMatch(dock, /setThemePreference/, '底栏不再直接改外观偏好');
  assert.doesNotMatch(dock, /跟随系统/, '底栏不再出现「跟随系统」选项');
  assert.doesNotMatch(dock, /ThemeSwitcher/, '底栏也不能换个组件把外观开关放回来');
});

test('图表与热力在外观变化时重建 option / 订阅 useAppearance', async () => {
  const kline = codeOf(await source('components/detail/KlineChart.tsx'));
  const scenario = codeOf(await source('components/cta/ScenarioChart.tsx'));
  const history = codeOf(await source('components/cta/PositionHistoryChart.tsx'));
  const lead = codeOf(await source('components/breakouts/LeadBigCard.tsx'));
  const macro = codeOf(await source('components/market/macro/MacroHistoryChart.tsx'));
  const eps = codeOf(await source('components/earnings/EpsHatchChart.tsx'));
  const heat = codeOf(await source('components/sectors/HeatMatrix.tsx'));
  assert.match(kline, /useAppearance\(\)/);
  assert.match(scenario, /useAppearance\(\)/);
  assert.match(history, /useAppearance\(\)/);
  assert.match(lead, /useAppearance\(\)/);
  assert.match(macro, /useAppearance\(\)/);
  assert.match(eps, /useAppearance\(\)/);
  assert.match(heat, /useAppearance\(\)/);
});

test('渲染期读主题调色的 .tsx 必须订阅 useAppearance', async () => {
  const READS_THEME = /\bCH\.(ink400|ink300|lineChart|brand600|brand500|brand400|warn600|ai600|tooltipBg)\b|\bivRank(?:Color|Ink|Tint)\s*\(/;
  const offenders = [];
  const walk = async (dir) => {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.name.endsWith('.tsx')) {
        const code = codeOf(await readFile(full, 'utf8'));
        if (READS_THEME.test(code) && !/useAppearance\s*\(/.test(code)) {
          offenders.push(path.relative(src, full));
        }
      }
    }
  };
  await walk(src);
  assert.deepEqual(offenders, [], `这些组件读主题色却没订阅外观：${offenders.join(', ')}`);
});
