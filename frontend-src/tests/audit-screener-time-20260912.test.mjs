import test from 'node:test';
import assert from 'node:assert/strict';
import { barTooltipTitle, fmtAxisLabel, formatChartTime, lastBarText } from '../src/components/detail/chartTime.ts';

test('chart labels use New York trading dates in Tokyo, Shanghai, UTC and Los Angeles', () => {
  const previous = process.env.TZ;
  try {
    for (const timezone of ['Asia/Tokyo', 'Asia/Shanghai', 'UTC', 'America/Los_Angeles']) {
      process.env.TZ = timezone;
      assert.equal(formatChartTime('2026-09-11T04:00:00Z', '1d'), '2026-09-11');
      assert.equal(formatChartTime('2026-09-11T04:00:00Z', '1d', 'axis'), '09-11');
      assert.equal(formatChartTime('2026-09-11T20:00:00Z', '1d'), '2026-09-11');
      assert.equal(formatChartTime('2026-09-07T00:00:00-04:00', '1w'), '2026-09-07');
      assert.equal(formatChartTime('2026-09-11T18:30:00Z', '5m'), '2026-09-11 14:30 ET');
      assert.equal(formatChartTime('2026-09-11T18:30:00Z', '5m', 'axis'), '09-11 14:30');
    }
  } finally {
    if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous;
  }
});

test('chart formatter follows DST and preserves date-only trading dates without inventing times', () => {
  assert.equal(formatChartTime('2026-03-06T14:30:00Z', '15m'), '2026-03-06 09:30 ET');
  assert.equal(formatChartTime('2026-03-09T13:30:00Z', '15m'), '2026-03-09 09:30 ET');
  assert.equal(formatChartTime('2026-11-02T14:30:00Z', '1h'), '2026-11-02 09:30 ET');
  assert.equal(formatChartTime('2026-01-05', '1w'), '2026-01-05');
  assert.equal(formatChartTime('2026-01-05', '1d', 'axis'), '01-05');
  assert.equal(formatChartTime('2026-01-05', '5m'), '—');
  for (const value of ['', 'not a date', '2026-02-30', '2026-09-11T14:30:00']) {
    assert.equal(formatChartTime(value, '1d'), '—', `reject invalid or timezone-less input: ${value}`);
  }
});

test('axis, tooltip and last-bar wrappers share the formatter without modifying source bar timestamps', () => {
  const bars = [{ t: '2026-09-11T18:30:00Z', c: 100 }];
  const original = JSON.stringify(bars);
  assert.equal(fmtAxisLabel(bars[0].t, '5m'), '09-11 14:30');
  assert.equal(barTooltipTitle(bars[0].t, '5m'), '2026-09-11 14:30 ET');
  assert.equal(lastBarText({ bars }, '5m'), '2026-09-11 14:30 ET');
  assert.equal(JSON.stringify(bars), original);
});
