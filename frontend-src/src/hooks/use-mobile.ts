import { useSyncExternalStore } from 'react';

const QUERY = '(max-width: 767px)';
let media: MediaQueryList | undefined;
const listeners = new Set<() => void>();
const getMedia = () => media ??= window.matchMedia(QUERY);
const notify = () => listeners.forEach(listener => listener());
const snapshot = () => getMedia().matches;
const serverSnapshot = () => false;

function subscribe(listener: () => void) {
  if (listeners.size === 0) getMedia().addEventListener('change', notify);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) getMedia().removeEventListener('change', notify);
  };
}

export function useIsMobile() {
  // 首帧即使用正确断点；整张行情表共用一个原生监听器。
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}
