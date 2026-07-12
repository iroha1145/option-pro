import assert from 'node:assert/strict';
import test from 'node:test';

import { marketCalendarDayDelta, marketTodayISO } from '../static/js/utils/marketDate.js';

test('marketTodayISO follows the New York trading date across UTC midnight', () => {
  assert.equal(marketTodayISO('2026-07-13T00:01:00+09:00'), '2026-07-12');
  assert.equal(marketTodayISO('2026-07-13T08:00:00+09:00'), '2026-07-12');
  assert.equal(marketTodayISO('2026-07-12T23:59:00-04:00'), '2026-07-12');
  assert.equal(marketTodayISO('2026-07-13T00:01:00-04:00'), '2026-07-13');
  assert.equal(marketTodayISO('2026-07-12T03:30:00Z'), '2026-07-11');
  assert.equal(marketTodayISO('2026-07-12T04:30:00Z'), '2026-07-12');
  assert.equal(marketTodayISO('2026-01-01T04:30:00Z'), '2025-12-31');
  assert.equal(marketCalendarDayDelta('2026-03-07', '2026-03-09'), 2);
  assert.equal(marketCalendarDayDelta('bad-date', '2026-03-09'), null);
});
