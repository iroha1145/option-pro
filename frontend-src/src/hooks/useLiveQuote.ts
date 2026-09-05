import { useCallback, useEffect, useSyncExternalStore } from 'react';
import { normalizeQuoteSymbols, quoteStore } from '@/lib/liveQuotes';
import type { RadarUpdate } from '@/lib/liveQuotes';

export function useQuoteSymbols(symbols: readonly string[], focus: readonly string[] = []) {
  const key = normalizeQuoteSymbols(symbols).join(',');
  const focusKey = normalizeQuoteSymbols(focus).join(',');
  useEffect(() => quoteStore.register(key ? key.split(',') : [], focusKey ? focusKey.split(',') : []), [key, focusKey]);
}
export function useLiveQuote(symbol: string) {
  const normalized = symbol.toUpperCase();
  const subscribe = useCallback((fn: () => void) => quoteStore.subscribe(normalized, fn), [normalized]);
  const snapshot = useCallback(() => quoteStore.getQuote(normalized), [normalized]);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
export function useQuoteStatus() { return useSyncExternalStore(quoteStore.subscribeStatus, quoteStore.getStatus, quoteStore.getStatus); }
export function useRadarUpdates(onUpdate: (update: RadarUpdate) => void) {
  useEffect(() => quoteStore.subscribeRadar(onUpdate), [onUpdate]);
}

export function useLiveRadarEvent<T extends { event_id: string; state_version?: number }>(event: T): T {
  const id = event.event_id;
  const subscribe = useCallback((fn: () => void) => quoteStore.subscribeRadarEvent(id, fn), [id]);
  const snapshot = useCallback(() => quoteStore.getRadarEvent(id), [id]);
  const update = useSyncExternalStore(subscribe, snapshot, snapshot);
  return update && Number(update.state_version) > (event.state_version ?? 0) ? { ...event, ...update } : event;
}

export function useRadarVersion() { return useSyncExternalStore(quoteStore.subscribeRadarVersion, quoteStore.getRadarVersion, quoteStore.getRadarVersion); }
