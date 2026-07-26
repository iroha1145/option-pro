/**
 * 排序 + 渐进切片的**行为**测试。
 *
 * 上一轮的性能测试几乎全是源码正则匹配，所以「表格拿到未排序切片」这个真实
 * 缺陷全绿通过。这里执行真正的排序函数，并把全局赢家放在首批之外 —— 这正是
 * 「先切片再排序」会漏掉它的位置。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { sortWatchlistItems } from '../src/pages/watchlistSort.ts';

const FIRST_BATCH = 24;

/** 30 条数据；全局极值刻意放在第 30 条（下标 29），即首批之外。 */
function dataset() {
  const rows = [];
  for (let i = 0; i < 30; i++) {
    rows.push({
      ticker: `T${String(i).padStart(2, '0')}`,
      name: `Name ${i}`,
      sector: 'x',
      price: 100 + i,            // 最贵的也在最后
      change: 0,
      changePct: i * 0.1,        // 涨幅最大的在最后
      sparkline: [],
      strengthScore: i,          // 强度最高的也在最后
      signals: [],
      updatedAt: '',
    });
  }
  return rows;
}

const winner = 'T29';

for (const [key, label] of [
  ['changePct', '涨幅'],
  ['price', '最新价'],
  ['strength', '强度'],
]) {
  test(`按${label}降序时，全局第一名即使排在第 30 条也进入首批`, () => {
    const rows = dataset();
    const sorted = sortWatchlistItems(rows, { key, desc: true });
    const firstBatch = sorted.slice(0, FIRST_BATCH).map((r) => r.ticker);

    assert.equal(sorted[0].ticker, winner, `${label}的全局第一名必须排在最前`);
    assert.ok(
      firstBatch.includes(winner),
      `${label}的全局第一名必须出现在首批 ${FIRST_BATCH} 条里；` +
        '「先切原始顺序前 24 条、再在其中排序」会漏掉它',
    );
  });
}

test('按最新价排序不能退化成按涨跌幅排序', () => {
  // 让两者的顺序互相冲突：价格递增，涨跌幅递减。
  const rows = dataset().map((r, i) => ({ ...r, price: 100 + i, changePct: -i }));
  const byPrice = sortWatchlistItems(rows, { key: 'price', desc: true });
  const byChange = sortWatchlistItems(rows, { key: 'changePct', desc: true });

  assert.equal(byPrice[0].ticker, 'T29', '价格最高的是 T29');
  assert.equal(byChange[0].ticker, 'T00', '涨幅最高的是 T00');
  assert.notEqual(
    byPrice[0].ticker,
    byChange[0].ticker,
    '旧实现把 ticker/strength 之外的一切都当成 changePct，点「最新价」时切片按涨跌幅排',
  );
});

test('代码排序是真的按字母，不是按别的字段', () => {
  const rows = dataset();
  const asc = sortWatchlistItems(rows, { key: 'ticker', desc: false });
  assert.equal(asc[0].ticker, 'T00');
  assert.equal(asc.at(-1).ticker, 'T29');
});

test('缺失值排在末尾，且不随升降序在头尾之间跳动', () => {
  const rows = dataset().slice(0, 5);
  rows[2] = { ...rows[2], changePct: Number.NaN };

  const desc = sortWatchlistItems(rows, { key: 'changePct', desc: true });
  const asc = sortWatchlistItems(rows, { key: 'changePct', desc: false });

  assert.equal(desc.at(-1).ticker, rows[2].ticker, '降序时缺失值在末尾');
  assert.equal(asc.at(-1).ticker, rows[2].ticker, '升序时缺失值仍在末尾');
});

test('未知排序键保持原顺序，不静默改按涨跌幅', () => {
  const rows = dataset();
  const out = sortWatchlistItems(rows, { key: 'not_a_column', desc: true });
  assert.deepEqual(
    out.map((r) => r.ticker),
    rows.map((r) => r.ticker),
  );
});

test('没有排序时保持接口给的顺序', () => {
  const rows = dataset();
  assert.equal(sortWatchlistItems(rows, null), rows);
});

test('排序不修改传入的数组', () => {
  const rows = dataset();
  const before = rows.map((r) => r.ticker);
  sortWatchlistItems(rows, { key: 'changePct', desc: true });
  assert.deepEqual(rows.map((r) => r.ticker), before);
});
