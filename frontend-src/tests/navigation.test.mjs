/**
 * 全站导航结构（2026-10-08 第二版）：九个页面、六个一级入口，以及一级入口的高亮规则。
 * 直接引用真实模块 src/lib/navigation.ts，不读源码文本。
 * 入口名字随界面语言变，所以这里只比较路径，不比较文字。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { NAV_GROUPS, NAV_PAGES, isNavGroupActive, navSectionPages } from '../src/lib/navigation.ts';

const pathsOf = (pages) => pages.map((page) => page.path);

/** 按一级入口自己的路径找到它（首页 '/'、选股 '/screener'……）。 */
function groupAt(path) {
  const found = NAV_GROUPS.find((item) => item.path === path);
  assert.ok(found, `找不到以 ${path} 为入口的一级菜单`);
  return found;
}

/** 在某个地址下被点亮的一级入口，用入口自己的路径表示，顺序同导航。 */
const litGroups = (pathname) => NAV_GROUPS.filter((item) => isNavGroupActive(pathname, item)).map((item) => item.path);

test('NAV_PAGES：九个页面，地址与顺序固定，各有名字', () => {
  assert.equal(NAV_PAGES.length, 9);
  assert.deepEqual(pathsOf(NAV_PAGES), [
    '/',
    '/watchlist',
    '/screener',
    '/breakouts',
    '/market',
    '/sectors',
    '/cta',
    '/earnings',
    '/catalysts',
  ]);
  assert.ok(NAV_PAGES.every((page) => typeof page.label === 'string' && page.label.length > 0), '每个页面都有名字');
});

test('NAV_GROUPS：六个一级入口；带二级页面的入口先进组内第一页；九个页面一个不漏、不重复', () => {
  assert.equal(NAV_GROUPS.length, 6);
  assert.deepEqual(
    NAV_GROUPS.map((item) => item.path),
    ['/', '/watchlist', '/screener', '/market', '/earnings', '/catalysts'],
  );
  assert.ok(NAV_GROUPS.every((item) => typeof item.label === 'string' && item.label.length > 0), '每个入口都有名字');
  for (const item of NAV_GROUPS) {
    if (item.pages.length > 0) assert.equal(item.path, item.pages[0].path, `${item.path} 入口应进组内第一页`);
  }
  /* 单页入口自己就是一个页面；带二级页面的入口由子页面承载。合起来正好是 NAV_PAGES 的九个地址。 */
  const reachable = NAV_GROUPS.flatMap((item) => (item.pages.length > 0 ? pathsOf(item.pages) : [item.path]));
  assert.deepEqual([...reachable].sort(), pathsOf(NAV_PAGES).sort());
  assert.equal(new Set(reachable).size, reachable.length, '同一个页面不能挂在两个入口下');
});

test('isNavGroupActive：九个页面各自只点亮所属的那一个入口', () => {
  const expected = {
    '/': '/',
    '/watchlist': '/watchlist',
    '/screener': '/screener',
    '/breakouts': '/screener',
    '/market': '/market',
    '/sectors': '/market',
    '/cta': '/market',
    '/earnings': '/earnings',
    '/catalysts': '/catalysts',
  };
  assert.deepEqual(Object.keys(expected).sort(), pathsOf(NAV_PAGES).sort(), '期望表要覆盖全部九个页面');
  for (const [pathname, entry] of Object.entries(expected)) {
    assert.deepEqual(litGroups(pathname), [entry], `${pathname} 应当只点亮 ${entry}`);
  }
});

test('isNavGroupActive：首页只在根路径点亮', () => {
  const home = groupAt('/');
  assert.equal(isNavGroupActive('/', home), true);
  for (const pathname of ['/watchlist', '/stock/AAPL', '/screener', '/market', '/catalysts']) {
    assert.equal(isNavGroupActive(pathname, home), false, `${pathname} 不点亮首页`);
  }
});

test('isNavGroupActive：选股组在条件选股、突破雷达（含子路径）点亮，行业表现不点亮选股', () => {
  const screen = groupAt('/screener');
  for (const pathname of ['/screener', '/breakouts', '/breakouts/x']) {
    assert.equal(isNavGroupActive(pathname, screen), true, `${pathname} 点亮选股`);
  }
  for (const pathname of ['/sectors', '/market', '/cta', '/', '/breakoutsx']) {
    assert.equal(isNavGroupActive(pathname, screen), false, `${pathname} 不点亮选股`);
  }
});

test('isNavGroupActive：市场组在美股概况、行业表现、CTA 趋势点亮', () => {
  const market = groupAt('/market');
  for (const pathname of ['/market', '/sectors', '/cta']) {
    assert.equal(isNavGroupActive(pathname, market), true, `${pathname} 点亮市场`);
  }
  for (const pathname of ['/screener', '/breakouts', '/earnings', '/catalysts', '/']) {
    assert.equal(isNavGroupActive(pathname, market), false, `${pathname} 不点亮市场`);
  }
});

test('isNavGroupActive：新闻组在 /catalysts 点亮，/cta 不能点亮新闻（按路径段匹配，不按前缀）', () => {
  const news = groupAt('/catalysts');
  const market = groupAt('/market');
  assert.equal(isNavGroupActive('/catalysts', news), true);
  assert.equal(isNavGroupActive('/cta', news), false, '/cta 是 /catalysts 的前缀兄弟，不能点亮新闻');
  assert.equal(isNavGroupActive('/catalysts', market), false, '/catalysts 也不能点亮含 /cta 的市场组');
});

test('isNavGroupActive：财报组只在 /earnings 点亮', () => {
  const earnings = groupAt('/earnings');
  assert.equal(isNavGroupActive('/earnings', earnings), true);
  for (const pathname of ['/', '/watchlist', '/screener', '/market', '/cta', '/catalysts', '/stock/AAPL']) {
    assert.equal(isNavGroupActive(pathname, earnings), false, `${pathname} 不点亮财报`);
  }
});

test('navSectionPages：页内二级标签的页面与顺序', () => {
  assert.deepEqual(pathsOf(navSectionPages('screen')), ['/screener', '/breakouts']);
  assert.deepEqual(pathsOf(navSectionPages('market')), ['/market', '/sectors', '/cta']);
  /* 二级标签就是对应一级入口的子页面，不是另一份清单 */
  assert.deepEqual(pathsOf(navSectionPages('screen')), pathsOf(groupAt('/screener').pages));
  assert.deepEqual(pathsOf(navSectionPages('market')), pathsOf(groupAt('/market').pages));
});
