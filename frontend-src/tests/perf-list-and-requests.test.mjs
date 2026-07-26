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

test('共享落在原始响应层，覆盖所有拉同一端点的模块', async () => {
  const shared = codeOf(await source('api/sharedRead.ts'));
  const market = codeOf(await source('api/modules/market.ts'));
  const strength = codeOf(await source('api/modules/strength.ts'));
  const pulse = codeOf(await source('components/market/api.ts'));

  // 第一版共享的是映射结果，只覆盖得了同一个模块内的调用；
  // components/market/api.ts 用自己的 mapper 拉同样的端点，于是 /market 页
  // 仍然有两次 /market/status 和两次 /strength/market。
  assert.match(market, /sharedGlobalGet<unknown>\('\/market\/status'\)/);
  assert.match(market, /sharedGlobalGet<unknown>\('\/market\/indices'\)/);
  assert.match(strength, /sharedGlobalGet<unknown>\('\/strength\/market'\)/);
  assert.match(pulse, /sharedGlobalGet<RawMarketStatus>\('\/market\/status'\)/);
  assert.match(pulse, /sharedGlobalGet<\{ market_regime[^}]*\}>\('\/strength\/market'\)/);

  // 白名单：不在名单里的路径直接放行，避免有人把按用户变化的接口塞进来
  assert.match(shared, /const SHAREABLE = new Set/);
  assert.match(shared, /if \(!SHAREABLE\.has\(path\)\) \{/);
  // 窗口必须短：替代不了轮询，只压掉同一时刻的重复
  const window = Number(/SHARE_WINDOW_MS = ([\d_]+)/.exec(shared)[1].replace(/_/g, ''));
  assert.ok(window > 0 && window <= 5000, `共享窗口 ${window}ms 过长`);
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

  // 真实请求桩：解析要跨一次微任务，否则并发根本不会重叠。
  const clientStub = {
    get: async () => {
      calls += 1;
      await new Promise((r) => setTimeout(r, 5));
      return { market: 'open', session: 'regular', label: '盘中' };
    },
    mockOr: (_fixture, live) => live(),
    toQuery: () => '',
    isMock: false,
  };

  // 加载**真实**的 sharedRead：去重逻辑就在它里面，桩掉它这条测试就没有意义了。
  const sharedSrc = ts.transpileModule(
    fs.readFileSync(path.join(src, 'api', 'sharedRead.ts'), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
  ).outputText;
  const sharedModule = { exports: {} };
  vm.runInNewContext(sharedSrc, {
    module: sharedModule,
    exports: sharedModule.exports,
    require: () => clientStub,
    Date, Promise, Map, Set, Object, Number, String, Boolean,
  });

  const module = { exports: {} };
  const require = (id) => {
    if (id === '../sharedRead' || id === './sharedRead') return sharedModule.exports;
    if (id === '../client' || id === './client') {
      return clientStub;
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
  assert.equal(typeof resetMarketStatusShare, 'function');
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

  // 指数走同一套：常驻 IndexTape 与大盘页并发时也只发一次。
  resetMarketStatusShare();
  const before = calls;
  await Promise.all([marketApi.indices(), marketApi.indices()]);
  assert.equal(calls, before + 1, '两个并发的指数调用者只应发一次请求');

  // 两个端点的共享互不干扰
  resetMarketStatusShare();
  const beforeBoth = calls;
  await Promise.all([marketApi.status(), marketApi.indices()]);
  assert.equal(calls, beforeBoth + 2, '不同端点必须各发各的');

  // 身份切换必须作废共享读：/strength/market 对访客和 owner 返回的不是同一份
  // 数据（访客读落库的公开快照，owner 实时算），2 秒窗口足以让登录后的第一次
  // 读取复用上一个身份那份。
  const { dropSharedReads } = sharedModule.exports;
  assert.equal(typeof dropSharedReads, 'function', 'sharedRead 没有导出 dropSharedReads');
  await marketApi.status();
  const beforeDrop = calls;
  await marketApi.status();
  assert.equal(calls, beforeDrop, '窗口内本该复用');
  dropSharedReads();
  await marketApi.status();
  assert.equal(calls, beforeDrop + 1, '身份切换后仍然复用了上一个身份的响应');
});

test('身份变化才作废共享读，每次探测都清会废掉共享窗口', async () => {
  // 身份不只在登录/登出时变：会话过期是由 60 秒定时或重新聚焦那次核验发现的，
  // 没有任何本地写操作，那条路径同样要作废。但只在**变了**的时候作废 ——
  // 每次探测都清一遍会让一个轮询周期里三个组件各发一次 /market/status。
  const hook = codeOf(await source('hooks/useAccess.tsx'));
  assert.match(hook, /import \{ dropSharedReads \} from '@\/api\/sharedRead'/);
  // 写操作路径（登录/注册/登出）：写完到状态读回来之间也不能有窗口。
  assert.match(hook, /generationRef\.current \+= 1;[\s\S]{0,400}?dropSharedReads\(\);/);
  // 核验路径：只有身份真的变了才清。
  assert.match(
    hook,
    /if \(identityRef\.current !== null && identityRef\.current !== identity\) \{\s*dropSharedReads\(\);/,
    '核验路径要么不作废共享读，要么每次探测都清 —— 两者都不对',
  );
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

/* ---------- 个人自选的加载 / 失败 / 真空三态 ---------- */

test('个人自选读取失败不能冒充成空自选', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));

  // 旧写法 .catch(() => setMyTickers([])) 让请求失败长得和「还没建过自选」
  // 一模一样：横幅说「你还没有自己的自选」，下面照常摆默认池。
  assert.doesNotMatch(page, /if \(alive\) setMyTickers\(\[\]\);/);
  assert.match(page, /setPersonalState\('error'\)/);
  // 只有成功读到空列表才算「还没建过」
  assert.match(page, /personalState === 'ready' && myTickers !== null && myTickers\.length === 0/);
  // 失败要说出来并可重试
  assert.match(page, /读不到你的自选列表/);
  assert.match(page, /setPersonalReloadToken/);
});

test('个人自选还没读回来时算加载中，不先摆默认池', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));
  assert.match(page, /wl\.loading \|\| \(canManageWatchlist && personalState === 'loading'\)/);
});

/* ---------- 打印挂载全部 ---------- */

test('打印前挂载全部，打印后还回去，且不假称解决了浏览器查找', async () => {
  const page = codeOf(await source('pages/Watchlist.tsx'));
  const hook = codeOf(await source('hooks/useProgressiveList.ts'));
  assert.match(page, /addEventListener\('beforeprint', prepareForPrint\)/);
  assert.match(page, /removeEventListener\('beforeprint', prepareForPrint\)/);

  // 打印结束必须还回上限。不还的话两百多张卡会一直挂在 DOM 里，这一轮渐进挂载
  // 的收益到下次整页刷新前都作废 —— 等于打印一次就把优化撤销了。
  assert.match(page, /addEventListener\('afterprint', restoreAfterPrint\)/);
  assert.match(page, /removeEventListener\('afterprint', restoreAfterPrint\)/);

  // 必须同步提交。beforeprint 返回后浏览器可能立刻截取打印文档，而普通 setState
  // 在 React 里是异步提交的 —— 纸上仍然只有已挂载的那 24 张。
  assert.match(hook, /flushSync\(\(\) => setLimit\(Number\.MAX_SAFE_INTEGER\)\)/);
  assert.match(hook, /import \{ flushSync \} from 'react-dom'/);
  // 直接挂 loadAll 就是原来那个错法：上限永久留在「全部」上。
  assert.doesNotMatch(page, /addEventListener\('beforeprint', (?:loadAll|progressive\.loadAll)/);

  // 注释里必须承认 ⌘F 的限制没有解决，而不是留下一个做不到的承诺
  assert.match(await source('hooks/useProgressiveList.ts'), /无法在页面里可靠拦截/);
});

/* ---------- 高度保留放在共用外壳 ---------- */

test('列表高度保留对 loading/error/empty/list 一视同仁', async () => {
  const page = await source('pages/Watchlist.tsx');
  // 外壳带 min-h，而不是只有 loading 分支带
  assert.match(page, /<div className="mt-4 min-h-\[70vh\]">/);
  assert.doesNotMatch(page, /<div className="card-surface min-h-\[70vh\]">/);
});

/* ---------- 个股详情：核心行情与强度补充并行 ---------- */

test('详情与强度补充同时发出，不是首尾相接', async () => {
  const api = codeOf(await source('components/detail/api.ts'));

  // 两个 Promise 必须在 await 之前就创建出来
  const detailIdx = api.indexOf('const detailPromise = stocksApi.detail(symbol, force);');
  const strengthIdx = api.indexOf('const supplementPromise: Promise<StrengthSupplement> = Promise.race([');
  const firstAwait = api.indexOf('await detailPromise');
  assert.ok(detailIdx > 0 && strengthIdx > 0, '两条请求都要提前发起');
  assert.ok(
    detailIdx < firstAwait && strengthIdx < firstAwait,
    '两个请求都必须在第一个 await 之前发起，否则仍是串行',
  );
  // 旧写法：先 await 详情，再去要强度
  assert.doesNotMatch(api, /const detail = await stocksApi\.detail\(t, force\);/);
  // 回退路径复用已经在飞的那次请求
  assert.match(api, /const supplement = await supplementPromise;/);
});

test('强度补充明确失败时不重发；只有超时才重发', async () => {
  // 原来失败和超时都被折成 null，于是回退分支靠「是不是 null」决定要不要再要一次
  // —— 一个刚刚 404 的端点于是又被请求一遍，第二次还是 404。超时才值得重发：
  // 那次请求可能还在飞，marketGet 会让两者共用同一个 in-flight promise。
  const api = codeOf(await source('components/detail/api.ts'));
  assert.match(api, /kind: 'ok'/);
  assert.match(api, /kind: 'timeout'/);
  assert.match(api, /kind: 'failed'/);
  assert.match(
    api,
    /supplement\.kind === 'timeout'\s*\?\s*await marketGet\(supplementUrl/,
    '回退分支没有把重发限制在超时这一种情况上',
  );
  // 失败也被折成 null 的旧写法不能再出现。
  assert.doesNotMatch(api, /\)\.catch\(\(\) => null\),?\s*\n\s*new Promise<null>/);
});
