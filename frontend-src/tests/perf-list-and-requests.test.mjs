/**
 * 性能改动的行为回归。
 *
 * 这些改动的风险不在「快不快」，而在「会不会悄悄改变呈现的事实」：
 * 渲染分批不能变成数据分批，请求共享不能把两个不同的事实合成一个。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { useProgressiveList } from '../src/hooks/useProgressiveList.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const source = (p) => readFile(path.join(src, p), 'utf8');

const codeOf = (text) =>
  text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((l) => {
      const t = l.trimStart();
      return !t.startsWith('//') && !t.startsWith('*');
    })
    .join('\n');

/* ---------- 渲染分批，不是数据分批 ---------- */

test('渐进列表的契约：前缀切片，且给得出剩余数量', async () => {
  const source = await readFile(
    path.join(src, 'hooks', 'useProgressiveList.ts'),
    'utf8',
  );
  assert.equal(typeof useProgressiveList, 'function');
  // 切片必须是前缀 —— 排序结果的前 N 条，不是任意子集
  assert.match(source, /items\.slice\(0, limit\)/);
  // 全部挂载后不再复制数组
  assert.match(source, /limit >= total \? items :/);
  // 剩余数量必须可见，否则用户无从知道「这不是全部」
  assert.match(source, /remaining: Math\.max\(0, total - limit\)/);
  // 条数变化只夹紧，不回退（见下方专门的用例）
  assert.match(source, /current > total && total > 0/);
  // 不支持 IntersectionObserver 时必须仍可手动加载
  assert.match(source, /typeof IntersectionObserver === 'undefined'/);
});

test('自选页在完整列表上排序与统计，只对渲染切片分批', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));

  // 统计口径必须仍然来自完整数据
  assert.match(page, /const items = wl\.data \?\? \[\]/);
  assert.match(page, /advancers: known\.filter/);
  // 排序在 cardItems 上完成，分批发生在排序之后
  assert.match(page, /const cardItems = useMemo/);
  assert.match(page, /useProgressiveList\(cardItems/);
  // 渲染用切片
  assert.match(page, /renderedCards\.map/);
  assert.doesNotMatch(page, /cardItems\.map\(/, '不应再有直接渲染整份列表的地方');
  // 还有剩余时必须给出可见的继续加载入口
  assert.match(page, /progressive\.hasMore/);
  assert.match(page, /加载更多/);
  assert.match(page, /还有 \{progressive\.remaining\} 只/);
});

test('桌面表格与移动卡片流用同一批次', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));
  assert.match(page, /rows=\{renderedRows\}/);
  // 同一个切片对象：两个断点下「加载更多」含义必须一致
  assert.match(page, /const renderedRows = renderedCards;/);
});

/* ---------- 不再为每张卡建投影节点 ---------- */

test('自选卡片不再使用 layout 投影，首屏之外不做入场动画', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));
  assert.doesNotMatch(page, /layout="position"/, 'layout 投影会为每张卡建节点并反复测量');
  assert.match(page, /initial=\{animateIn \? \{ opacity: 0, y: 14 \} : false\}/);
  assert.match(page, /animateIn=\{i < FIRST_BATCH\}/);
  // hover 位移必须保留
  assert.match(page, /whileHover=\{\{ y: -3/);
});

/* ---------- 占位与真实内容占同样的空间 ---------- */

test('概览统计条的占位与真实内容布局一致', async () => {
  const page = await source('pages/Watchlist.tsx');
  const layout = 'flex snap-x snap-mandatory gap-4 overflow-x-auto pb-1 no-scrollbar sm:grid sm:grid-cols-2 sm:overflow-visible xl:grid-cols-4';
  const occurrences = page.split(layout).length - 1;
  assert.equal(
    occurrences,
    2,
    '占位与真实内容必须用同一组布局类，否则两者高度不同就是 CLS',
  );
  assert.doesNotMatch(
    codeOf(page),
    /grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4/,
    '旧占位在移动端是纵向堆叠，真实内容是横向滚动条',
  );
});

/* ---------- 请求共享不改变语义 ---------- */

test('市场时段共享同一请求，且只共享这一个只读端点', async () => {
  const market = codeOf(await source('api/modules/market.ts'));
  assert.match(market, /statusInFlight/);
  assert.match(market, /STATUS_SHARE_MS/);
  assert.match(market, /sharedStatus\(\)/);
  // 共享窗口必须是短的：它替代不了轮询，只压掉同一时刻的重复
  const window = Number(/STATUS_SHARE_MS = ([\d_]+)/.exec(market)[1].replace(/_/g, ''));
  assert.ok(window > 0 && window <= 5000, `共享窗口 ${window}ms 过长，会掩盖真实的时段切换`);
});

test('共享的市场时段请求在并发调用下只发一次', async () => {
  // market.ts 用无扩展名的相对导入，node 的 strip-types 加载器解析不了，
  // 因此按仓库既有做法转译后在 vm 里跑。
  const fs = await import('node:fs');
  const vm = await import('node:vm');
  const ts = (await import('typescript')).default;

  const compiled = ts.transpileModule(
    fs.readFileSync(path.join(src, 'api', 'modules', 'market.ts'), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
  ).outputText;

  let calls = 0;
  const asRec = (v) => (v !== null && typeof v === 'object' && !Array.isArray(v) ? v : {});
  const module = { exports: {} };
  const require = (id) => {
    if (id === '../client' || id === './client') {
      return {
        get: async () => {
          calls += 1;
          // 与真实请求一样：解析要跨一次微任务，否则并发根本不会重叠。
          await new Promise((r) => setTimeout(r, 5));
          return { market: 'open', session: 'regular', label: '盘中' };
        },
        mockOr: (_fixture, live) => live(),
        toQuery: () => '',
        isMock: false,
      };
    }
    if (id === '../live' || id === './live') {
      return {
        asRec,
        pickN: (r, ...k) => { for (const x of k) { const v = asRec(r)[x]; if (typeof v === 'number') return v; } return null; },
        pickS: (r, ...k) => { for (const x of k) { const v = asRec(r)[x]; if (typeof v === 'string' && v) return v; } return null; },
        pickB: (r, ...k) => { for (const x of k) { const v = asRec(r)[x]; if (typeof v === 'boolean') return v; } return null; },
        unwrap: (b, ...k) => { if (Array.isArray(b)) return b; for (const x of k) { if (Array.isArray(asRec(b)[x])) return asRec(b)[x]; } return []; },
      };
    }
    return new Proxy({}, { get: () => () => undefined });
  };
  vm.runInNewContext(compiled, {
    module, exports: module.exports, require,
    Date, Promise, JSON, Math, Number, Object, Array, Map, Set, String, Boolean, setTimeout,
  });

  const { marketApi, resetMarketStatusShare } = module.exports;
  resetMarketStatusShare();
  const [a, b, c] = await Promise.all([
    marketApi.status(),
    marketApi.status(),
    marketApi.status(),
  ]);
  assert.equal(calls, 1, '三个并发调用者要的是同一个事实，只应发一次请求');
  assert.deepEqual(a, b);
  assert.deepEqual(b, c);

  // 共享窗口之外必须重新请求：这是缓存，不是冻结。
  resetMarketStatusShare();
  await marketApi.status();
  assert.equal(calls, 2, '复位后必须重新发出请求');
});

/* ---------- 分批发生在排序之后（review 发现的真实缺陷） ---------- */

test('桌面表格拿到的是排序后的切片，不是未排序列表的前 N 条', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));

  // DataTable 内部还会按 sort 再排一次。如果喂给它未排序的前 N 条，
  // 「涨幅优先」看到的就是「任意前 24 只里涨得最多的」，而不是
  // 「涨得最多的 24 只」—— 局部样本冒充完整结果。
  assert.doesNotMatch(
    page,
    /items\.slice\(0, renderedCards\.length\)/,
    '表格不能拿未排序的 items 切片',
  );
  assert.match(page, /const renderedRows = renderedCards;/);
  // renderedCards 来自 cardItems（已排序）
  assert.match(page, /useProgressiveList\(cardItems/);
});

test('DataTable 确实会内部排序，所以切片顺序有意义', async () => {
  const table = await source('components/shared/DataTable.tsx');
  // 这条断言存在的意义：一旦 DataTable 改成不排序，上面那条的理由就变了
  assert.match(table, /const sorted = useMemo\(/);
  assert.match(table, /\[\.\.\.rows\]\.sort\(/);
});

/* ---------- 后台刷新不能收回用户已经加载的内容 ---------- */

test('条数变化时夹紧上限，而不是退回首批', async () => {
  const hook = codeOf(await source('hooks/useProgressiveList.ts'));

  // 列表每 60 秒轮询。原先「条数一变就 setLimit(initial)」会让已经加载到
  // 96 条的人被打回 24 条：页面高度少掉约一万两千像素，滚动位置被夹到底，
  // 人被甩到列表的另一处。
  assert.doesNotMatch(
    hook,
    /useEffect\(\(\) => \{\s*setLimit\(initial\);\s*\}/,
    '不能因为条数变化就回退到首批',
  );
  assert.match(hook, /current > total && total > 0/);
  assert.match(hook, /Math\.max\(initial, total\)/);
});
