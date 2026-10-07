import assert from 'node:assert/strict';
import test from 'node:test';
import { sectorBreadthReading } from '../src/lib/sectorBreadth.ts';

test('sector breadth displays the actual count and formats percentage once', () => {
  assert.equal(
    sectorBreadthReading(18.1818, { available: 11, expected: 11, aboveCount: 2 }),
    '11 个板块中，2 个站上 50 日均线，占比 18.18%',
  );
});

test('partial coverage names the real denominator and the missing coverage', () => {
  assert.equal(
    sectorBreadthReading(28.5714, { available: 7, expected: 11, aboveCount: 2 }),
    '有数据的 7 个板块中，2 个站上 50 日均线，占比 28.57%（数据覆盖 7/11 个板块）',
  );
});

test('zero sectors above the average is a valid measured count', () => {
  assert.equal(
    sectorBreadthReading(0, { available: 11, expected: 11, aboveCount: 0 }),
    '11 个板块中，0 个站上 50 日均线，占比 0.00%',
  );
});

test('old snapshots show a percentage without inferring a count', () => {
  assert.equal(
    sectorBreadthReading(18.1818, { available: 11, expected: 11, aboveCount: null }),
    '站上 50 日均线的板块占比 18.18%',
  );
  assert.equal(
    sectorBreadthReading(28.5714, { available: 7, expected: 11, aboveCount: null }),
    '站上 50 日均线的板块占比 28.57%（数据覆盖 7/11 个板块）',
  );
});

test('unavailable or invalid counts never produce invented sector counts', () => {
  for (const coverage of [
    { available: null, expected: null, aboveCount: null },
    { available: 11, expected: 11, aboveCount: 12 },
    { available: 11, expected: 11, aboveCount: 2.5 },
    { available: 11, expected: 11, aboveCount: -1 },
    { available: 0, expected: 11, aboveCount: 0 },
  ]) {
    assert.equal(sectorBreadthReading(18.1818, coverage), '站上 50 日均线的板块占比 18.18%');
  }
});
