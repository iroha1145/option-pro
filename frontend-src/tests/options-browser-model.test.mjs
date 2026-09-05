import test from 'node:test';
import assert from 'node:assert/strict';
import { contractsForChain, selectContracts, isAlerting } from '../src/components/detail/options/chainMetrics.ts';

const row = (strike, changes = {}) => ({ strike, callVol: 300, callOi: 100, callBid: 1, callAsk: 1.2, callIv: 0.32, putVol: 10, putOi: 100, putBid: 0.8, putAsk: 1, putIv: 0.34, ...changes });
const chain = (rows, spot = 102.5) => ({ ticker: 'TEST', expiration: '2030-01-18', spot, rows });

test('exact fractional strikes and one-sided contracts retain their identity', () => {
  const cs = contractsForChain(chain([row(102.5, { putVol: null, putOi: null, putBid: null, putAsk: null, putIv: null })]));
  assert.equal(cs.length, 1);
  assert.equal(cs[0].strike, 102.5);
  assert.equal(cs[0].id, 'call-102.5');
  assert.equal(cs[0].iv, 0.32);
});

test('three times open interest is flagged at the boundary, without direction inference', () => {
  const [c] = contractsForChain(chain([row(100)]));
  assert.deepEqual(c.activity, ['ratio']);
  assert.equal(isAlerting(c.volOi), true);
  assert.equal(c.direction, undefined);
});

test('zero open interest and missing open interest remain different', () => {
  const cs = contractsForChain(chain([row(100, { callOi: 0, putOi: null, putVol: 100 })]));
  assert.deepEqual(cs[0].volOi, { kind: 'new_opening' });
  assert.deepEqual(cs[0].activity, ['zero_oi']);
  assert.deepEqual(cs[1].volOi, { kind: 'unavailable' });
  assert.deepEqual(cs[1].activity, []);
});

test('invalid and crossed quotes never become estimated trading value', () => {
  const cs = contractsForChain(chain([row(100, { callBid: 3, callAsk: 2, putBid: -1, putVol: Infinity })]));
  assert.equal(cs[0].mid, null);
  assert.equal(cs[0].premium, null);
  assert.equal(cs[1].bid, null);
  assert.equal(cs[1].volume, null);
  assert.equal(cs[1].premium, null);
});

test('nearby filtering uses observed strikes and never mutates the full chain', () => {
  const cs = contractsForChain(chain(Array.from({ length: 25 }, (_, i) => row(90 + i))));
  const before = structuredClone(cs);
  const selected = selectContracts(cs, 'near', 'all', 102);
  assert.deepEqual([...new Set(selected.map((c) => c.strike))], Array.from({ length: 11 }, (_, i) => 97 + i));
  assert.deepEqual(cs, before);
  assert.equal(selectContracts(cs, 'near', 'put', 102).length, 11);
  assert.equal(selectContracts(cs, 'all', 'all', 102).length, 50);
});

test('missing spot cannot silently select a made-up at-the-money group', () => {
  const cs = contractsForChain(chain(Array.from({ length: 15 }, (_, i) => row(100 + i))));
  assert.equal(selectContracts(cs, 'near', 'all', null).length, cs.length);
});

test('activity filtering ranks real volume and includes large contracts without a computable ratio', () => {
  const cs = contractsForChain(chain([row(100, { callVol: 6000, callOi: null }), row(105, { callVol: 7000 })]));
  const selected = selectContracts(cs, 'alerts', 'call', 102.5);
  assert.deepEqual(selected.map((c) => c.volume), [7000, 6000]);
  assert.ok(selected[1].activity.includes('volume'));
});
