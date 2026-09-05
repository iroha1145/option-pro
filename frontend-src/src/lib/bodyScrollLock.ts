/** Every overlay owns one release; the last release restores the original overflow declarations. */
type LockState = { count: number; properties: [string, string, string][] };
const locks = new WeakMap<Document, LockState>();
export function acquireBodyScrollLock(doc: Document = document): () => void {
  let state = locks.get(doc);
  if (!state) {
    // Capture longhands too: an existing overflow-y alone has no shorthand value.
    const style = doc.body.style;
    state = {
      count: 0,
      properties: [...style].filter((name) => ['overflow', 'overflow-x', 'overflow-y'].includes(name))
        .map((name) => [name, style.getPropertyValue(name), style.getPropertyPriority(name)]),
    };
    locks.set(doc, state);
    style.setProperty('overflow', 'hidden', 'important');
  }
  state.count += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    state.count -= 1;
    if (state.count > 0) return;
    doc.body.style.removeProperty('overflow');
    for (const [name, value, priority] of state.properties) doc.body.style.setProperty(name, value, priority);
    locks.delete(doc);
  };
}
