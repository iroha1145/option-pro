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
  /* 窗口取到分支真正的结尾（600ms 清 job 那句）：#129 的 refreshItem 里自带
     退避定时器，若按第一个 window.setTimeout 截断，完成 toast 落在窗口外，
     下面的先后顺序断言会永假。 */
  const terminal = drawer.match(/if \(TERMINAL\.includes\(next\.status\)\) \{[\s\S]*?\}, 600\);/);
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

test('FocusCycle 用 setTimeout 退避，状态只认归一四值，停表与弃代分职', async () => {
  const card = codeOf(await source('components/catalysts/FocusCycleCard.tsx'));
  assert.match(card, /pollGenRef = useRef\(0\)/);
  assert.match(card, /window\.setTimeout\(\(\) => void tick\(\)/);
  assert.doesNotMatch(card, /window\.setInterval/);
  /* 状态只认 nFocusJob 归一化后的四值：cancelled 归 failed、cancel_requested 归
     in_progress。此前那两条只 grep 死字串的断言反倒把不可达分支钉住了，去掉；
     真正要锁的是归一后的判定与「停表 / 弃代」分职。 */
  assert.match(card, /next\.status === 'completed' \|\| next\.status === 'failed'/);
  assert.match(card, /job\.status === 'queued' \|\| job\.status === 'in_progress'/);
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
  assert.match(watchlist, /disabled=\{personal\.busy\}/);
});

test('SignalCards / FeedPanel 不再把可交互嵌进 role=button 的 article', async () => {
  // closest('button, a, [role="button"]') 这类选择器字符串不是 JSX 属性，先剥掉再查。
  const withoutSelectors = (text) => text.replace(/\[role="button"\]/g, '');
  const cards = withoutSelectors(codeOf(await source('components/breakouts/SignalCards.tsx')));
  assert.doesNotMatch(cards, /role="button"/);
  // 内容层保留指针事件（可划选、title 可悬停），整卡点击由内容层转发并让开划选与真按钮。
  assert.doesNotMatch(cards, /pointer-events-none/);
  assert.match(cards, /getSelection\(\)/);
  const feed = withoutSelectors(codeOf(await source('components/catalysts/FeedPanel.tsx')));
  assert.doesNotMatch(feed, /role="button"/);
  assert.match(feed, /absolute inset-0/);
  assert.doesNotMatch(feed, /className="[^"]*pointer-events-none/);
  assert.match(feed, /getSelection\(\)/);
});

test('MobileDock 已登录走退出', async () => {
  const dock = codeOf(await source('components/MobileDock.tsx'));
  assert.match(dock, /logout\(\)/);
  // Escape isolation is exercised with real stacked dialogs in overlay-behavior.spec.mjs.
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

test('图表键盘尊重弹层守卫', async () => {
  // LayerMenu / DrawingWorkspace Escape behavior runs in overlay-behavior.spec.mjs.
  const chart = codeOf(await source('components/detail/KlineChart.tsx'));
  assert.match(chart, /escapeHandledByOverlay/);
  assert.match(chart, /public_snapshot_unavailable/);
});
