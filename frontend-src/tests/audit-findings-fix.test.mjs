/**
 * 只读审计缺陷回归：theme 查询、日期本地化、404、轮询世代、嵌套交互。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'path';
import { sanitizeThemeId } from '../src/components/catalysts/filters.ts';
import { fmtLocaleDate, fmtLocaleDateTime, fmtLocaleTime } from '../src/lib/format.ts';
import { isNavPathActive } from '../src/lib/utils.ts';

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

test('sanitizeThemeId 只接受安全主题 ID', () => {
  assert.equal(sanitizeThemeId('theme-ai-capex'), 'theme-ai-capex');
  assert.equal(sanitizeThemeId('evt_' + 'a'.repeat(32)), 'evt_' + 'a'.repeat(32));
  assert.equal(sanitizeThemeId('not a theme!!'), null);
  assert.equal(sanitizeThemeId(''), null);
  assert.equal(sanitizeThemeId(null), null);
});

test('live feed 查询串带上 theme', async () => {
  const api = codeOf(await source('components/catalysts/api.ts'));
  assert.match(api, /theme: q\.themeId \|\| undefined/);
  assert.match(api, /serializeCatalystFeedQuery/);
});

test('界面日期走 localeTag，不再写死 zh-CN', () => {
  const iso = '2026-07-15T04:00:00Z';
  assert.notEqual(fmtLocaleDate(iso), '—');
  assert.notEqual(fmtLocaleTime(iso), '—');
  assert.notEqual(fmtLocaleDateTime(iso), '—');
  assert.equal(fmtLocaleDate(''), '—');
  assert.equal(fmtLocaleTime('not-a-date'), '—');
});

test('NewsDrawer 关闭/换条会递增世代，在途 job 不得写回', async () => {
  const drawer = codeOf(await source('components/catalysts/NewsDrawer.tsx'));
  assert.match(drawer, /pollGenRef = useRef\(0\)/);
  assert.match(drawer, /pollGenRef\.current \+= 1/);
  assert.match(drawer, /stillThisPoll/);
  assert.match(drawer, /openNewsRef\.current = newsId/);
  assert.match(drawer, /sameNews = \(\) => openNewsRef\.current === job\.newsId/);
  assert.doesNotMatch(drawer, /activeNewsRef/);
  const terminal = drawer.match(/if \(TERMINAL\.includes\(next\.status\)\) \{[\s\S]*?window\.setTimeout/);
  assert.ok(terminal, '终态分支应存在');
  assert.match(terminal[0], /const sameNews = \(\) => openNewsRef\.current === job\.newsId/);
  assert.match(terminal[0], /if \(!sameNews\(\)\) return/);
  assert.doesNotMatch(terminal[0], /stillThisPoll\(\)/);
  const toastAfterGuard = terminal[0].indexOf('if (!sameNews()) return');
  const toastSuccess = terminal[0].indexOf("toast.success");
  assert.ok(toastAfterGuard >= 0 && toastSuccess > toastAfterGuard, '完成 toast 必须在抽屉仍开着这条之后');
});

test('ImpactCard 跟日历修订刷新，超时文案在 job 阶段可见', async () => {
  const card = codeOf(await source('components/earnings/ImpactCard.tsx'));
  assert.match(card, /calendarRevision/);
  assert.match(card, /errorMsg && \(/);
  assert.match(card, /__t\('分析完成'\)/);
  assert.doesNotMatch(card, /return __t\('简体中文'\)/);
});

test('404 主按钮回首页', async () => {
  const page = codeOf(await source('pages/NotFound.tsx'));
  assert.match(page, /to="\/"/);
  assert.match(page, /t\('返回首页'\)/);
  assert.doesNotMatch(page, /to="\/watchlist"/);
});

test('useAiJob 用退避轮询，取消失败写入 error', async () => {
  const hook = codeOf(await source('components/detail/useAiJob.ts'));
  assert.match(hook, /\[2000, 3000, 5000, 8000, 10000\]/);
  assert.match(hook, /document\.hidden/);
  assert.match(hook, /setError\(e instanceof Error \? e\.message : t\('取消失败'\)\)/);
  assert.doesNotMatch(hook, /setTimeout\(\(\) => void tick\(\), 2500\)/);
});

test('FocusCycle 用 setTimeout 退避，cancel_requested 算运行中', async () => {
  const card = codeOf(await source('components/catalysts/FocusCycleCard.tsx'));
  assert.match(card, /pollGenRef = useRef\(0\)/);
  assert.match(card, /window\.setTimeout\(\(\) => void tick\(\)/);
  assert.doesNotMatch(card, /window\.setInterval/);
  assert.match(card, /cancel_requested/);
  assert.match(card, /cancelled/);
  assert.match(card, /abandonPoll = useCallback/);
  const stopPoll = card.match(/const stopPoll = useCallback\(\(\) => \{[\s\S]*?\}, \[\]\);/);
  assert.ok(stopPoll, 'stopPoll 应只杀计时器');
  assert.doesNotMatch(stopPoll[0], /pollGenRef/);
});

test('DataTable 的 hint 在排序按钮外，Watchlist 强度列不再把 InfoHint 塞进 button', async () => {
  const table = codeOf(await source('components/shared/DataTable.tsx'));
  assert.match(table, /hint\?: ReactNode/);
  assert.match(table, /\{c\.hint\}/);
  const watchlist = codeOf(await source('pages/Watchlist.tsx'));
  assert.match(watchlist, /hint: <InfoHint/);
  assert.match(watchlist, /removingTickers/);
});

test('SignalCards / FeedPanel 不再把可交互嵌进 role=button 的 article', async () => {
  const cards = codeOf(await source('components/breakouts/SignalCards.tsx'));
  assert.doesNotMatch(cards, /role="button"/);
  assert.match(cards, /pointer-events-auto/);
  const feed = codeOf(await source('components/catalysts/FeedPanel.tsx'));
  assert.doesNotMatch(feed, /role="button"/);
  assert.match(feed, /absolute inset-0/);
});

test('MobileDock 已登录走退出，Escape 拦住冒泡', async () => {
  const dock = codeOf(await source('components/MobileDock.tsx'));
  assert.match(dock, /logout\(\)/);
  assert.match(dock, /e\.stopPropagation\(\)/);
  assert.match(dock, /t\('退出登录'\)/);
});

test('isNavPathActive 根路径精确匹配，/cta 不点亮 /catalysts', () => {
  assert.equal(isNavPathActive('/', '/'), true);
  assert.equal(isNavPathActive('/watchlist', '/'), false);
  assert.equal(isNavPathActive('/stock/AAPL', '/'), false);
  assert.equal(isNavPathActive('/watchlist', '/watchlist'), true);
  assert.equal(isNavPathActive('/watchlist/extra', '/watchlist'), true);
  assert.equal(isNavPathActive('/cta', '/cta'), true);
  assert.equal(isNavPathActive('/cta/depth', '/cta'), true);
  assert.equal(isNavPathActive('/catalysts', '/cta'), false);
  assert.equal(isNavPathActive('/catalysts', '/catalysts'), true);
});

test('Navbar 与 MobileDock 用边界匹配，文字高亮不跟 NavLink isActive', async () => {
  const nav = codeOf(await source('components/Navbar.tsx'));
  const dock = codeOf(await source('components/MobileDock.tsx'));
  assert.match(nav, /isNavPathActive\(location\.pathname, item\.path\)/);
  assert.match(nav, /end=\{item\.path === '\/'\}/);
  assert.doesNotMatch(nav, /location\.pathname\.startsWith/);
  assert.doesNotMatch(nav, /\(\{ isActive \}\)/);
  assert.match(dock, /isNavPathActive\(location\.pathname/);
  assert.doesNotMatch(dock, /location\.pathname\.startsWith/);
});

test('图层菜单与回撤尺 Escape 对齐 overlay 守卫', async () => {
  const menu = codeOf(await source('components/detail/chart-drawings/LayerMenu.tsx'));
  assert.match(menu, /e\.stopPropagation\(\)/);
  const chart = codeOf(await source('components/detail/KlineChart.tsx'));
  assert.match(chart, /escapeHandledByOverlay/);
  assert.match(chart, /public_snapshot_unavailable/);
});
