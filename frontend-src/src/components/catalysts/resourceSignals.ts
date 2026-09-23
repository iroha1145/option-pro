/** The legacy contract and the shared view cache invalidate together. */
export type CatalystInvalidateOptions = {
  userInitiated?: boolean;
  /** Login, logout or account switch: persisted snapshots of the old principal go too. */
  principalChanged?: boolean;
};
const listeners = new Set<(options?: CatalystInvalidateOptions) => void>();
export function onCatalystReadsInvalidated(listener: (options?: CatalystInvalidateOptions) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function notifyCatalystReadsInvalidated(options?: CatalystInvalidateOptions): void {
  for (const listener of [...listeners]) listener(options);
}
