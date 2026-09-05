import { useEffect, type RefObject } from 'react';
import { activateFocusScope } from '@/lib/focusScope';
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  active: boolean,
  options: { initialFocusRef?: RefObject<HTMLElement | null> } = {},
): void {
  const { initialFocusRef } = options;
  useEffect(() => {
    const container = containerRef.current;
    if (!active || !container) return;
    return activateFocusScope(container, initialFocusRef?.current);
  }, [active, containerRef, initialFocusRef]);
}
