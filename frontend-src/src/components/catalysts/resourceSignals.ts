/** The legacy contract and the shared view cache invalidate together. */
const listeners = new Set<() => void>();
export function onCatalystReadsInvalidated(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function notifyCatalystReadsInvalidated(): void {
  for (const listener of [...listeners]) listener();
}
