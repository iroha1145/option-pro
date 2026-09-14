import assert from 'node:assert/strict';
import test from 'node:test';
import { earningsFixture, homeLabFixtures } from '../../scripts/perf/lib/round6_lab_fixtures.mjs';

test('round6 lab fixtures match live mapper contracts', () => {
  const now = new Date('2026-09-14T16:00:00Z');
  const home = homeLabFixtures(now);
  assert.equal(home.indices.indices.length, 5);
  for (const row of home.indices.indices) {
    assert.ok(row.symbol);
    assert.ok(row.price > 0);
    assert.equal(typeof row.change_percent, 'number');
  }
  assert.equal(home.status.market, 'closed');
  assert.deepEqual(home.watchlist.groups, []);
  assert.deepEqual(home.cta.instruments, []);
  assert.equal(home.quotes.status.connection_status, 'disabled');
  assert.ok(Array.isArray(home.quotes.quotes));

  const earnings = earningsFixture(now);
  assert.equal(earnings.earnings.length, 6);
  for (const row of earnings.earnings) {
    assert.ok(row.ticker);
    assert.match(row.date, /^\d{4}-\d{2}-\d{2}$/);
    assert.equal(row.date, row.earnings_date);
    assert.equal(row.timing, 'amc');
    assert.equal(typeof row.eps_estimate, 'number');
  }
  assert.equal(earnings.earnings[0].ticker, 'AAPL');
  assert.equal(earnings.earnings[0].date, '2026-09-14');
  for (const row of earnings.earnings) {
    assert.equal(row.publicFeatured, true);
    assert.equal(row.public_featured, true);
    assert.equal(row.market_cap, row.marketCap);
  }
  assert.deepEqual(home.accountWatchlist.tickers, []);
  assert.equal(home.accountWatchlist.max_tickers, 50);
});
