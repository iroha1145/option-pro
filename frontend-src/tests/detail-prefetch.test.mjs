/**
 * 个股详情的预取（性能：K 线不再等整个详情对象）。
 *
 * 问题不是请求顺序，是**组件结构**：KlineChart 挂在 `if (loading) return 骨架` 之后，
 * 所以详情请求回来之前它根本没被挂上，两段互不依赖的往返被排成了串行。
 *
 * 修法必须同时满足两件事，缺一件就换了个 bug：
 *   1. 请求提前发出（这才是优化）；
 *   2. 请求总数不变（否则等于把一个往返换成一次多余的请求）。
 *
 * 第 2 点靠 marketGet 的 in-flight 共享 + 60 秒缓存实现，所以预取和面板必须请求
 * 完全相同的 URL —— 默认周期只能有一份定义。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..');

function read(relativePath) {
  return readFileSync(resolve(repoRoot, relativePath), 'utf8');
}

/** 去掉注释后的代码：解释性注释不该满足或破坏断言。 */
function codeOf(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
}

const detailApi = read('frontend-src/src/components/detail/api.ts');
const klineChart = read('frontend-src/src/components/detail/KlineChart.tsx');
const drawerBody = read('frontend-src/src/components/StockDrawerBody.tsx');

test('默认周期只有一份定义，预取和图表因此请求同一个 URL', () => {
  assert.match(detailApi, /export const DEFAULT_CHART_RANGE: ChartRange = '1d'/);
  // KlineChart 的初始 state 必须用那个常量，不能自己写 '1d'。
  assert.match(
    codeOf(klineChart),
    /useState<ChartRange>\(DEFAULT_CHART_RANGE\)/,
    "KlineChart 又写死了一个默认周期：预取会取另一个 URL，白发一次请求",
  );
  assert.doesNotMatch(
    codeOf(klineChart),
    /useState<ChartRange>\('1d'\)/,
  );
});

test('预取取的正是 K 线和信号那两个端点', () => {
  const code = codeOf(detailApi);
  const fn = code.slice(code.indexOf('export function prefetchStockDetailPanels'));
  assert.ok(fn.length > 0, '找不到 prefetchStockDetailPanels');
  assert.match(fn, /getDetailChart\(t, DEFAULT_CHART_RANGE\)/);
  // TrendBiasPanel 与 SignalList 读的是同一个 /signals/stock/{t}，一次预取覆盖两者。
  assert.match(fn, /getTrendBias\(t\)/);
});

test('预取不 force：强制读会消耗 owner 手动拉取后那一次性的退避豁免', () => {
  const code = codeOf(detailApi);
  const fn = code.slice(
    code.indexOf('export function prefetchStockDetailPanels'),
  );
  assert.doesNotMatch(fn, /force/, '预取一旦 force，真正要用豁免的那次读取就没得用了');
});

test('预取失败被吞掉，但错误态仍由各面板自己那次调用呈现', () => {
  const fn = codeOf(detailApi).slice(
    codeOf(detailApi).indexOf('export function prefetchStockDetailPanels'),
  );
  // 未处理的 rejection 不该因为一次预取而出现。
  assert.match(fn, /\.catch\(\(\) => \{\}\)/);
  // 但预取本身不返回数据，也不缓存错误：面板共享同一个 promise，照样会看到失败。
  assert.match(fn, /: void\b/);
});

test('抽屉在 loading 分支之前就发起预取', () => {
  const code = codeOf(drawerBody);
  const prefetchAt = code.indexOf('prefetchStockDetailPanels(ticker)');
  const loadingReturnAt = code.indexOf('if (loading)');
  assert.ok(prefetchAt > 0, '抽屉没有调用预取');
  assert.ok(loadingReturnAt > 0);
  assert.ok(
    prefetchAt < loadingReturnAt,
    '预取排在 loading 提前返回之后：那正好又晚了一个往返',
  );
});

test('预取只随 ticker 变化重跑，不跟着轮询每分钟重发', () => {
  const code = codeOf(drawerBody);
  const effect = code.slice(code.indexOf('prefetchStockDetailPanels(ticker)'));
  assert.match(
    effect.slice(0, 120),
    /\}, \[ticker\]\);/,
    '预取 effect 的依赖不是只有 ticker：会随其它 state 变化反复发请求',
  );
});

test('没有在 loading 分支里另挂一个 KlineChart', () => {
  // 那样做会让 React 在两棵不同的树之间卸载重挂，图表的轮询会真的跑两遍 ——
  // 比原来的串行更糟。
  const code = codeOf(drawerBody);
  const loadingAt = code.indexOf('if (loading)');
  const errorAt = code.indexOf('if (error || !detail)');
  assert.ok(loadingAt > 0 && errorAt > loadingAt);
  const loadingBranch = code.slice(loadingAt, errorAt);
  assert.doesNotMatch(
    loadingBranch,
    /<KlineChart/,
    'loading 分支里挂了 KlineChart：位置不同会导致卸载重挂，请求发两遍',
  );
});

test('详情合并保留宏观影子字段，缺失时保持 null 而不是中性', () => {
  const code = codeOf(detailApi);
  assert.match(code, /macroFit: strength\?\.macroFit \?\? null/);
  assert.match(code, /macroTailwind: strength\?\.macroTailwind \?\? null/);
  // 补充这一路超时会让 strength 为 null；此时不能出现一个凭空的 50。
  assert.doesNotMatch(code, /macroFit:[^\n]*\?\?\s*50/);
});
