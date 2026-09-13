/**
 * 新闻页 / 手机端加载优化的行为契约：
 * 分包不能丢掉默认 feed，预取不能发业务请求，新闻行无动画时不得挂 framer。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const source = (p) => readFile(path.join(src, p), 'utf8');
const codeOf = (text) =>
  text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => {
      const trimmed = line.trimStart();
      return !trimmed.startsWith('//') && !trimmed.startsWith('*');
    })
    .join('\n');

test('催化页默认新闻流仍同步挂载，其余标签与抽屉按需加载', async () => {
  const page = codeOf(await source('pages/Catalysts.tsx'));
  assert.match(page, /import FeedPanel from '@\/components\/catalysts\/FeedPanel'/);
  assert.match(page, /const FocusCycleCard = lazy\(/);
  assert.match(page, /const StocksPanel = lazy\(/);
  assert.match(page, /const CalendarPanel = lazy\(/);
  assert.match(page, /const SourcesPanel = lazy\(/);
  assert.match(page, /const NewsDrawer = lazy\(/);
  assert.match(page, /<StatusHero refreshToken=\{refreshToken\} \/>/);
  assert.match(page, /<HotspotsStrip onOpenNews=\{setSelectedNewsId\} refreshToken=\{refreshToken\} \/>/);
  assert.match(page, /<FocusCycleCard\b[^>]*\brefreshToken=\{refreshToken\}/);
  assert.match(page, /<DeferredMount refreshToken=\{refreshToken\}>/);
});

test('新闻行在 animate=false 时走普通 article，不给每条建 motion 节点', async () => {
  const feed = codeOf(await source('components/catalysts/FeedPanel.tsx'));
  assert.match(feed, /if \(!animate\) \{\s*return <article className=\{className\}>/);
  assert.match(feed, /<NewsRow key=\{it\.newsId\} item=\{it\} index=\{i\} animate=\{false\}/);
  assert.match(feed, /className = 'news-row group relative flex/);
});

test('今日新闻汇总只拉 1 条正文，计数仍读完整窗口 summary', async () => {
  const api = codeOf(await source('components/catalysts/api.ts'));
  assert.match(api, /windowHours: 24,\s*limit: 1,/);
  assert.doesNotMatch(api, /windowHours: 24,\s*limit: 50,/);
  assert.match(api, /completeCount === null && items\.length >= 50/);
});

test('状态栏把今日计数放到空闲后再取，刷新令牌仍立即启用', async () => {
  const hero = codeOf(await source('components/catalysts/StatusHero.tsx'));
  assert.match(hero, /enabled: todayEnabled/);
  assert.match(hero, /if \(refreshToken > 0\) \{\s*setTodayEnabled\(true\)/);
  assert.match(hero, /\[refreshToken\]\)/, 'hotspots/status 轮询仍吃刷新令牌');
});

test('导航与手机 Dock 预取路由 chunk，不发业务接口', async () => {
  const prefetch = codeOf(await source('lib/prefetchRoutes.ts'));
  const nav = codeOf(await source('components/Navbar.tsx'));
  const dock = codeOf(await source('components/MobileDock.tsx'));
  const watchlist = codeOf(await source('pages/Watchlist.tsx'));
  assert.match(prefetch, /void loader\(\)/);
  assert.doesNotMatch(prefetch, /fetch\(|registryGet|cachedGet/);
  assert.match(prefetch, /'\/catalysts': \(\) => import\('@\/pages\/Catalysts'\)/);
  assert.match(prefetch, /prefetchStockPage/);
  assert.match(nav, /onPointerEnter=\{\(\) => prefetchRoute\(item\.path\)\}/);
  assert.match(dock, /prefetchPrimaryRoutes\(\)/);
  assert.match(dock, /for \(const item of MORE_ITEMS\) prefetchRoute\(item\.path\)/);
  assert.match(watchlist, /onPointerEnter=\{prefetchStockPage\}/);
});

test('新闻首屏关键路径不再拉 framer-motion', async () => {
  for (const file of [
    'components/catalysts/StatusHero.tsx',
    'components/catalysts/HotspotsStrip.tsx',
    'components/catalysts/FilterBar.tsx',
    'components/catalysts/bits.tsx',
    'components/catalysts/FeedPanel.tsx',
  ]) {
    const text = codeOf(await source(file));
    assert.doesNotMatch(text, /from 'framer-motion'/, `${file} 仍静态引入 framer-motion`);
  }
});

test('手机端新闻行启用 content-visibility，不改可见行交互', async () => {
  const css = await source('index.css');
  assert.match(css, /\.news-row \{\s*content-visibility: auto;/);
  assert.match(css, /contain-intrinsic-size: auto 140px;/);
  assert.match(css, /\.hotspot-card,\s*\.watch-card \{\s*content-visibility: auto;/);
});
