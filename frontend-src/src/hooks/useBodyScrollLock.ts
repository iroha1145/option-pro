import { useEffect } from 'react';
import { acquireBodyScrollLock } from '@/lib/bodyScrollLock';
export function useBodyScrollLock(active: boolean): void {
  useEffect(() => active ? acquireBodyScrollLock() : undefined, [active]);
}
