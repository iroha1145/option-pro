/** Pure helpers for chart-drawing list responses. Safe to call on 429 / empty bodies. */

export function drawingIdentity(listed) {
  const row = listed?.drawings?.[0];
  if (!row) return null;
  return {
    id: row.id,
    price: row.anchors?.[0]?.price,
    time: row.anchors?.[0]?.time,
    barKey: row.anchors?.[0]?.barKey,
    locked: Boolean(row.locked),
  };
}

/**
 * Classify a listDrawings() result without throwing on 429 / empty payloads.
 * Callers must read `identity` only when `ok` is true.
 */
export function describeListedDrawing(listed) {
  if (!listed || listed.status === 429) {
    return { ok: false, state: "rate-limited", identity: null };
  }
  if (listed.status !== 200) {
    return { ok: false, state: `http ${listed.status}`, identity: null };
  }
  if (!Array.isArray(listed.drawings)) {
    return { ok: false, state: "n=?", identity: null };
  }
  if (listed.drawings.length !== 1) {
    return { ok: false, state: `n=${listed.drawings.length}`, identity: null };
  }
  const identity = drawingIdentity(listed);
  if (!identity) return { ok: false, state: "n=?", identity: null };
  return {
    ok: true,
    state: identity.locked ? "locked" : "unlocked",
    identity,
  };
}

/** Negative assertion helper: same id, still locked, price unchanged. */
export function unchangedLockedState(listed, expected) {
  const snap = describeListedDrawing(listed);
  if (!snap.ok) return snap.state;
  if (expected?.id && snap.identity.id !== expected.id) return "id-changed";
  if (!snap.identity.locked) return "unlocked";
  if (expected?.price != null && snap.identity.price !== expected.price) {
    return `moved:${snap.identity.price}`;
  }
  return "unchanged";
}
