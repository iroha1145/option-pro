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
    content: '#F6F7F9',
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
    assert.equal(meta.content, '#191B20');
    applyAppearance('light');
    assert.equal(classList.has('dark'), false);
    assert.equal(meta.content, '#F6F7F9');
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

test('CSS 暗色画布与 Cloud Monitor 令牌一致', async () => {
  const css = await source('index.css');
  const block = css.match(/html\.dark\s*\{([\s\S]*?)\n  \}/);
  assert.ok(block, '缺少 html.dark 规则');
  const body = block[1];
  for (const [token, value] of [
    ['--paper', '#191B20'],
    ['--card', '#24262D'],
    ['--paper-2', '#1D1F24'],
    ['--ink-900', '#F1F3F5'],
    ['--line', '#323640'],
    ['--line-strong', '#454B58'],
    ['--card-warm', '#21242B'],
  ]) {
    assert.match(body, new RegExp(`${token}:\\s*${value}`, 'i'), `${token} 应对上 Cloud Monitor`);
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
  assert.match(boot, /#191B20/);
  const html = await readFile(path.resolve(here, '..', 'index.html'), 'utf8');
  assert.match(html, /src="\/theme-boot\.js"/);
  assert.match(html, /name="theme-color"/);
});

test('顶栏、登录页与手机更多菜单都有外观开关', async () => {
  const navbar = codeOf(await source('components/Navbar.tsx'));
  const login = codeOf(await source('pages/Login.tsx'));
  const dock = codeOf(await source('components/MobileDock.tsx'));
  const switcher = codeOf(await source('components/ThemeSwitcher.tsx'));
  assert.match(navbar, /<ThemeSwitcher\s*\/>/);
  assert.doesNotMatch(navbar, /<ThemeSwitcher[^>]*hidden/);
  assert.match(login, /<ThemeSwitcher\s*\/>/);
  assert.match(dock, /setThemePreference/);
  assert.match(dock, /跟随系统/);
  assert.match(switcher, /useThemePreference\(\)/);
  assert.match(switcher, /setThemePreference/);
  assert.match(switcher, /role="menuitemradio"/);
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
