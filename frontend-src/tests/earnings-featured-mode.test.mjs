/**
 * 财报页「重点公司 / 全部公司」双模式的行为契约。
 *
 * 直接编译并运行 components/earnings/types.ts 里的真实实现（页面共用同一份
 * 纯函数），不做源码正则式的假断言。覆盖任务清单中的：
 *  #9  周历/月历/密度条在两个模式下恒用完整 allItems；
 *  #10 重点/全部计数正确且随当前日期范围计算；
 *  #11 点击非重点公司切到全部模式；
 *  #12 非重点公司缺预期波动仍可选中且行可见；
 *  #13 切换模式与日期后不产生孤儿 selectedTicker；
 *  #16 大规模列表下渐进挂载不回归。
 * 以及重点资格判定（市值门槛旁路无关字段 / unknown ≠ small / 账号自选并入）。
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import vm from 'node:vm';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

function loadTypesModule(globalOverrides = {}) {
  const modulePath = path.join(src, 'components/earnings/types.ts');
  const compiled = ts.transpileModule(fs.readFileSync(modulePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  const require = (id) => {
    if (id.includes('i18n/core')) {
      return { t: (value) => value, getLocale: () => 'zh' };
    }
    return new Proxy({}, { get: () => () => undefined });
  };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require,
    Intl,
    Date,
    JSON,
    Math,
    Number,
    Object,
    Array,
    Map,
    Set,
    String,
    Boolean,
    ...globalOverrides,
  });
  return module.exports;
}

const earnings = loadTypesModule();
const {
  addDays,
  computeEarningsListState,
  etToday,
  isFeaturedRow,
  planTickerSelection,
  prioritizeEarningsRows,
  resolveFeaturedSelection,
} = earnings;

const TODAY = etToday();

function row(ticker, overrides = {}) {
  return {
    ticker,
    name: ticker,
    date: addDays(TODAY, 2),
    timing: 'amc',
    epsEstimate: 1,
    epsActual: null,
    revEstimate: null,
    revActual: null,
    ...overrides,
  };
}

const NONE = new Set();

/* ---------------- 重点资格判定 ---------------- */

test('后端公共标注（市值门槛/公共池）直接决定重点资格', () => {
  assert.equal(isFeaturedRow(row('BIG', { public_featured: true }), NONE), true);
  assert.equal(isFeaturedRow(row('SML', { public_featured: false }), NONE), false);
});

test('market_cap 缺失是 unknown，不是小公司：不标注也不剔除', () => {
  const unknown = row('UNK', {
    public_featured: false,
    market_cap: null,
    market_cap_status: 'unavailable',
  });
  // 不因缺市值进入重点……
  assert.equal(isFeaturedRow(unknown, NONE), false);
  // ……也不因缺市值被从任何列表剔除（全部模式仍包含它）
  const state = computeEarningsListState({
    items: [unknown, row('BIG', { public_featured: true })],
    selectedDay: null,
    mode: 'all',
    personalTickers: NONE,
    visibleLimit: 24,
    selectedTicker: null,
  });
  assert.deepEqual(state.listItems.map((r) => r.ticker).sort(), ['BIG', 'UNK']);
});

test('账号自选始终进入重点公司（账号上下文在前端合并）', () => {
  const personal = new Set(['MYPK']);
  const mine = row('MYPK', { public_featured: false });
  assert.equal(isFeaturedRow(mine, personal), true);
  const state = computeEarningsListState({
    items: [mine, row('OTHER', { public_featured: false })],
    selectedDay: null,
    mode: 'featured',
    personalTickers: personal,
    visibleLimit: 24,
    selectedTicker: null,
  });
  assert.deepEqual(state.listItems.map((r) => r.ticker), ['MYPK']);
});

test('预期波动缺失不影响重点资格', () => {
  const featured = row('NOEM', {
    public_featured: true,
    expected_move_pct: null,
    expected_move_status: 'unavailable:no_usable_straddle',
  });
  assert.equal(isFeaturedRow(featured, NONE), true);
});

/* ---------------- #9 / #10：日历恒全量 · 计数随范围 ---------------- */

test('周历/月历/密度条数据在两个模式下完全一致，恒为 allItems', () => {
  const items = [
    row('A', { public_featured: true }),
    row('B', { public_featured: false }),
    row('C', { public_featured: false, date: addDays(TODAY, 45) }), // 窗口外
  ];
  for (const mode of ['featured', 'all']) {
    const state = computeEarningsListState({
      items,
      selectedDay: null,
      mode,
      personalTickers: NONE,
      visibleLimit: 24,
      selectedTicker: null,
    });
    // 引用恒等：日历组件拿到的就是完整 allItems，本身不经过任何过滤
    assert.equal(state.calendarItems, items);
  }
});

test('重点/全部计数按当前日期范围计算（滚动窗口与选中日各自成立）', () => {
  const dayA = addDays(TODAY, 3);
  const dayB = addDays(TODAY, 5);
  const items = [
    row('F1', { public_featured: true, date: dayA }),
    row('N1', { public_featured: false, date: dayA }),
    row('N2', { public_featured: false, date: dayA }),
    row('F2', { public_featured: true, date: dayB }),
    row('OUT', { public_featured: true, date: addDays(TODAY, 60) }), // 窗口外
  ];
  const rolling = computeEarningsListState({
    items,
    selectedDay: null,
    mode: 'featured',
    personalTickers: NONE,
    visibleLimit: 24,
    selectedTicker: null,
  });
  assert.equal(rolling.featuredCount, 2); // F1+F2（OUT 在 30 天窗口外）
  assert.equal(rolling.allCount, 4);
  const daily = computeEarningsListState({
    items,
    selectedDay: dayA,
    mode: 'featured',
    personalTickers: NONE,
    visibleLimit: 24,
    selectedTicker: null,
  });
  assert.equal(daily.featuredCount, 1);
  assert.equal(daily.allCount, 3);
});

/* ---------------- #11 / #12：非重点点击流转 ---------------- */

test('重点模式下点击非重点公司 → 自动切「全部公司」', () => {
  const items = [row('F', { public_featured: true }), row('N', { public_featured: false })];
  const plan = planTickerSelection({
    items,
    mode: 'featured',
    personalTickers: NONE,
    ticker: 'N',
  });
  // vm 沙箱另一 realm 的对象原型不同，deepEqual 会误报；JSON 折返后比较
  assert.deepEqual(JSON.parse(JSON.stringify(plan)), { mode: 'all', switched: true });
  // 点重点公司不切换；全部模式下点谁都不切换
  assert.equal(
    planTickerSelection({ items, mode: 'featured', personalTickers: NONE, ticker: 'F' }).switched,
    false,
  );
  assert.equal(
    planTickerSelection({ items, mode: 'all', personalTickers: NONE, ticker: 'N' }).switched,
    false,
  );
});

test('非重点公司即使没有市值与预期波动，选中后行仍被挂载可见', () => {
  const many = Array.from({ length: 60 }, (_, i) =>
    row(`F${String(i).padStart(2, '0')}`, {
      public_featured: true,
      market_cap: 30_000_000_000,
    }),
  );
  const bare = row('BARE', {
    public_featured: false,
    market_cap: null,
    market_cap_status: 'unavailable',
    expected_move_pct: null,
    expected_move_status: 'not_enriched',
  });
  const state = computeEarningsListState({
    items: [...many, bare],
    selectedDay: null,
    mode: 'all',
    personalTickers: NONE,
    visibleLimit: 24, // BARE 无市值，优先级排序下必然被 24 条额度挤出
    selectedTicker: 'BARE',
  });
  assert.ok(
    state.visibleItems.some((r) => r.ticker === 'BARE'),
    '选中行必须强制并入渐进挂载切片',
  );
});

/* ---------------- #13：孤儿 selectedTicker ---------------- */

test('切回重点公司时，非重点选中被换成重点默认候选（不留孤儿）', () => {
  const items = [
    row('F', { public_featured: true }),
    row('N', { public_featured: false }),
  ];
  const resolved = resolveFeaturedSelection({
    items,
    selectedDay: null,
    personalTickers: NONE,
    selectedTicker: 'N',
  });
  assert.equal(resolved, 'F');
  // 重点选中保持不变
  assert.equal(
    resolveFeaturedSelection({
      items,
      selectedDay: null,
      personalTickers: NONE,
      selectedTicker: 'F',
    }),
    'F',
  );
});

test('重点列表为空时孤儿选中清空为 null，而不是保留一个不存在的行', () => {
  const items = [row('N1', { public_featured: false }), row('N2', { public_featured: false })];
  assert.equal(
    resolveFeaturedSelection({
      items,
      selectedDay: null,
      personalTickers: NONE,
      selectedTicker: 'N1',
    }),
    null,
  );
});

test('选中日切换后孤儿解析在该日范围内取默认候选', () => {
  const dayA = addDays(TODAY, 2);
  const dayB = addDays(TODAY, 4);
  const items = [
    row('FA', { public_featured: true, date: dayA }),
    row('FB', { public_featured: true, date: dayB }),
    row('NB', { public_featured: false, date: dayB }),
  ];
  const resolved = resolveFeaturedSelection({
    items,
    selectedDay: dayB,
    personalTickers: NONE,
    selectedTicker: 'NB',
  });
  assert.equal(resolved, 'FB');
});

/* ---------------- #16：大规模列表性能 ---------------- */

test('4000 行下派生状态与渐进挂载保持在预算内', () => {
  // 挂钟断言在 CI 上量的是核争抢不是算法（node --test 多文件并行，双核 runner
  // 会把 <20ms 的任务拖到数百 ms），所以这里改成两层：
  //  1) 确定性计数：在 sandbox 里注入计数版 Date/Intl，直接数派生过程做了多少次
  //     日期解析/格式化——逐行 Intl.format（≥4000）、逐比较 Date.parse（≈19 万）
  //     这类数量级回归会被数出来，与机器快慢无关；
  //  2) 宽松挂钟护栏：只拦灾难性回归，阈值高于任何调度噪声。
  const counters = { dateParses: 0, dateConstructions: 0, intlFormatters: 0 };
  class CountingDate extends Date {
    constructor(...args) {
      super(...args);
      counters.dateConstructions += 1;
    }
    static parse(text) {
      counters.dateParses += 1;
      return Date.parse(text);
    }
    static now() {
      return Date.now();
    }
  }
  const CountingIntl = {
    DateTimeFormat: function DateTimeFormat(...args) {
      counters.intlFormatters += 1;
      return new Intl.DateTimeFormat(...args);
    },
  };
  const counted = loadTypesModule({ Date: CountingDate, Intl: CountingIntl });

  const items = Array.from({ length: 4000 }, (_, i) =>
    row(`T${String(i).padStart(4, '0')}`, {
      public_featured: i % 7 === 0,
      date: addDays(TODAY, i % 28),
      market_cap: i % 3 === 0 ? 25_000_000_000 : null,
      epsActual: i % 5 === 0 ? 1.2 : null,
    }),
  );

  // 模块加载本身允许构造一个 etFmt 单例；派生过程从零开始计数。
  counters.dateParses = 0;
  counters.dateConstructions = 0;
  counters.intlFormatters = 0;

  const started = process.hrtime.bigint();
  const state = counted.computeEarningsListState({
    items,
    selectedDay: null,
    mode: 'all',
    personalTickers: new Set(['T0001']),
    visibleLimit: 24,
    selectedTicker: 'T3999',
  });
  counted.prioritizeEarningsRows(items, 24);
  const elapsedMs = Number(process.hrtime.bigint() - started) / 1e6;

  // 28 个唯一日期 × 2 次 parse × 若干派生趟 ≈ 60；600 留 10 倍余量，
  // 仍比逐行（8000+）低一个数量级、比逐比较（≈19 万）低两个数量级。
  assert.ok(
    counters.dateParses <= 600,
    `4000 行派生做了 ${counters.dateParses} 次 Date.parse——日期解析没有按唯一日期记忆化`,
  );
  assert.ok(
    counters.intlFormatters === 0,
    `派生过程构造了 ${counters.intlFormatters} 个 Intl.DateTimeFormat——应复用模块级 etFmt 单例`,
  );
  assert.ok(
    counters.dateConstructions <= 200,
    `派生过程构造了 ${counters.dateConstructions} 个 Date 实例——存在逐行/逐比较的日期对象分配`,
  );

  // 灾难护栏：远高于调度噪声，只拦数量级崩坏。
  assert.ok(
    elapsedMs < 1500,
    `4000 行派生 + prioritize 耗时 ${elapsedMs.toFixed(1)}ms，超出 1500ms 灾难护栏`,
  );

  // 渐进挂载额度仍然生效：24 条 + 强制可见的选中行
  assert.ok(state.visibleItems.length <= 25);
  assert.ok(state.visibleItems.some((r) => r.ticker === 'T3999'));
});

/* ---------------- 页面结构互证：日历组件只接 allItems ---------------- */

test('Earnings.tsx 把完整 items 传给周历/月历/密度条，列表用模式切片', () => {
  const source = fs.readFileSync(path.join(src, 'pages/Earnings.tsx'), 'utf8');
  // 三个日历组件绑定完整 items
  assert.match(source, /<WeekScrubber\s+items=\{items\}/);
  assert.match(source, /<MonthCalendar\s+items=\{items\}/);
  assert.match(source, /<DensityStrip items=\{items\}/);
  // 列表用派生切片而不是 items
  assert.match(source, /<EarningsList\s+items=\{visibleItems\}/);
  // selectedRow 恒从完整 allItems 解析
  assert.match(source, /items\.find\(\(item\) => item\.ticker === selectedTicker\)/);
});
