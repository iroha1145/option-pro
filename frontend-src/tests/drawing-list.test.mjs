import test from 'node:test';
import assert from 'node:assert/strict';
import {
  describeListedDrawing,
  drawingIdentity,
  unchangedLockedState,
} from '../visual-tests/support/drawingList.mjs';

const lockedRow = {
  id: 'draw-1',
  locked: true,
  anchors: [{ price: 100, time: '2026-01-02T14:30:00Z', barKey: '2026-01-02' }],
};

test('drawingIdentity is null when 429 or empty so callers must not read .id', () => {
  assert.equal(drawingIdentity({ status: 429, drawings: null }), null);
  assert.equal(drawingIdentity({ status: 200, drawings: [] }), null);
  assert.equal(drawingIdentity({ status: 200, drawings: null }), null);
});

test('describeListedDrawing treats 429 as rate-limited without an identity', () => {
  const snap = describeListedDrawing({ status: 429, drawings: null });
  assert.equal(snap.ok, false);
  assert.equal(snap.state, 'rate-limited');
  assert.equal(snap.identity, null);
  assert.throws(() => snap.identity.id, TypeError);
  assert.throws(() => snap.identity.price, TypeError);
});

test('describeListedDrawing reports empty and http failures', () => {
  assert.equal(describeListedDrawing({ status: 200, drawings: [] }).state, 'n=0');
  assert.equal(describeListedDrawing({ status: 500, drawings: [] }).state, 'http 500');
  assert.equal(describeListedDrawing(null).state, 'rate-limited');
});

test('describeListedDrawing returns the locked snapshot on a 200', () => {
  const snap = describeListedDrawing({ status: 200, drawings: [lockedRow] });
  assert.equal(snap.ok, true);
  assert.equal(snap.state, 'locked');
  assert.equal(snap.identity.id, 'draw-1');
  assert.equal(snap.identity.price, 100);
  assert.equal(snap.identity.locked, true);
});

test('unchangedLockedState retries 429 and flags a moved or unlocked object', () => {
  const expected = { id: 'draw-1', price: 100 };
  assert.equal(unchangedLockedState({ status: 429, drawings: null }, expected), 'rate-limited');
  assert.equal(unchangedLockedState({ status: 200, drawings: [] }, expected), 'n=0');
  assert.equal(unchangedLockedState({ status: 200, drawings: [lockedRow] }, expected), 'unchanged');
  assert.equal(
    unchangedLockedState({
      status: 200,
      drawings: [{ ...lockedRow, anchors: [{ price: 110 }] }],
    }, expected),
    'moved:110',
  );
  assert.equal(
    unchangedLockedState({
      status: 200,
      drawings: [{ ...lockedRow, locked: false }],
    }, expected),
    'unlocked',
  );
});
