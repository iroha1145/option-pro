import test from 'node:test';
import assert from 'node:assert/strict';
import { fmtScanCountdown } from '../src/lib/format.ts';
import { setLocale } from '../src/i18n/testing.ts';

const now = Date.parse('2026-10-09T20:00:00Z');
const after = ms => new Date(now + ms).toISOString();

test('scan wait uses days or hours for closed-market intervals and minutes for short waits', () => {
  setLocale('zh');
  assert.equal(fmtScanCountdown(after(2 * 86400000 + 13 * 3600000), now), '2 天 13 小时');
  assert.equal(fmtScanCountdown(after(23 * 3600000 + 59 * 60000), now), '23 小时 59 分钟');
  assert.equal(fmtScanCountdown(after(59 * 60000 + 59 * 1000), now), '59:59');
  assert.equal(fmtScanCountdown(after(3600000), now), '1 小时 0 分钟');
  assert.equal(fmtScanCountdown(after(86400000), now), '1 天 0 小时');
  assert.equal(fmtScanCountdown(after(-1000), now), '00:00');
  assert.equal(fmtScanCountdown(null, now), '—');
  assert.equal(fmtScanCountdown('invalid', now), '—');
});


test('scan wait labels translate with the selected language', () => {
  setLocale('en');
  assert.equal(fmtScanCountdown(after(2 * 86400000 + 13 * 3600000), now), '2d 13h');
  setLocale('ja');
  assert.equal(fmtScanCountdown(after(3600000), now), '1時間 0分');
  setLocale('zh');
});
