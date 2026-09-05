import { useCallback, useEffect, useId, useLayoutEffect, useRef, useSyncExternalStore, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';

// All table/card instances share one owner. Replacing it removes the previous
// portal in the same React update; there is no exit animation to leave a second tip.
let activeTooltip: string | null = null;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const snapshot = () => activeTooltip;
const serverSnapshot = () => null;
function claim(id: string | null) {
  if (activeTooltip === id) return;
  activeTooltip = id;
  listeners.forEach((listener) => listener());
}
function release(id: string) { if (activeTooltip === id) claim(null); }

const GAP = 12;
const GUTTER = 8;
type PointerPosition = { x: number; y: number };

/** Read-only table details: follow a mouse, anchor to the control for keyboard/touch. */
export default function PointerTooltip({
  children, content, label, side = 'top', width = 240, className, contentClassName,
}: {
  children: ReactNode;
  content: ReactNode;
  label: string;
  side?: 'top' | 'bottom';
  width?: number;
  className?: string;
  contentClassName?: string;
}) {
  const id = useId();
  const open = useSyncExternalStore(subscribe, snapshot, serverSnapshot) === id;
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLSpanElement>(null);
  const pointer = useRef<PointerPosition | null>(null);
  const pointerKind = useRef('');
  const skipPointerFocus = useRef(false);
  const dismissed = useRef(false);
  const frame = useRef<number | null>(null);

  const place = useCallback(() => {
    const tip = tipRef.current, trigger = triggerRef.current;
    if (!tip || !trigger || activeTooltip !== id) return;
    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const rect = trigger.getBoundingClientRect();
    const origin = pointer.current
      ? { left: pointer.current.x, right: pointer.current.x, top: pointer.current.y, bottom: pointer.current.y }
      : rect;
    const box = tip.getBoundingClientRect();
    const maxLeft = Math.max(GUTTER, viewportWidth - box.width - GUTTER);
    const desiredLeft = pointer.current ? origin.right + GAP : (origin.left + origin.right - box.width) / 2;
    const alternateLeft = origin.left - GAP - box.width;
    const left = Math.min(maxLeft, Math.max(GUTTER,
      desiredLeft + box.width <= viewportWidth - GUTTER ? desiredLeft : alternateLeft));
    const above = origin.top - GAP - box.height;
    const below = origin.bottom + GAP;
    const fitsAbove = above >= GUTTER;
    const fitsBelow = below + box.height <= viewportHeight - GUTTER;
    const useAbove = side === 'top' ? fitsAbove || !fitsBelow : !fitsBelow && fitsAbove;
    const top = Math.min(Math.max(GUTTER, viewportHeight - box.height - GUTTER), Math.max(GUTTER, useAbove ? above : below));
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
    tip.style.visibility = 'visible';
  }, [id, side]);

  const schedulePlace = useCallback(() => {
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => { frame.current = null; place(); });
  }, [place]);
  const dismiss = useCallback(() => { dismissed.current = true; release(id); }, [id]);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const observer = new ResizeObserver(place);
    if (tipRef.current) observer.observe(tipRef.current);
    return () => observer.disconnect();
  }, [open, place, content]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      event.stopPropagation();
      dismiss();
    };
    const onPointer = (event: PointerEvent) => {
      if (!triggerRef.current?.contains(event.target as Node)) dismiss();
    };
    // Scrolling changes the row beneath a stationary pointer. Close immediately
    // instead of leaving details attached to the old cursor position or old row.
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('resize', dismiss);
    window.addEventListener('blur', dismiss);
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onPointer, true);
    return () => {
      window.removeEventListener('scroll', dismiss, true);
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('blur', dismiss);
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onPointer, true);
    };
  }, [open, dismiss]);

  useEffect(() => () => {
    release(id);
    if (frame.current !== null) cancelAnimationFrame(frame.current);
  }, [id]);

  return <>
    <span
      ref={triggerRef}
      role="button"
      tabIndex={0}
      aria-label={label}
      aria-expanded={open}
      aria-describedby={open ? id : undefined}
      data-pointer-tooltip-trigger=""
      className={cn('inline-flex min-h-6 cursor-help items-center align-middle', className)}
      onPointerEnter={(event) => {
        if (event.pointerType === 'touch') return;
        pointerKind.current = event.pointerType;
        pointer.current = { x: event.clientX, y: event.clientY };
        dismissed.current = false;
        claim(id);
        schedulePlace();
      }}
      onPointerMove={(event) => {
        if (event.pointerType === 'touch' || dismissed.current) return;
        pointer.current = { x: event.clientX, y: event.clientY };
        claim(id);
        schedulePlace();
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === 'touch') return;
        pointer.current = null;
        release(id);
      }}
      onPointerDown={(event) => {
        pointerKind.current = event.pointerType;
        skipPointerFocus.current = true;
      }}
      onFocus={() => {
        if (skipPointerFocus.current) { skipPointerFocus.current = false; return; }
        pointer.current = null;
        dismissed.current = false;
        claim(id);
      }}
      onBlur={() => { skipPointerFocus.current = false; release(id); }}
      onClick={(event) => {
        event.stopPropagation();
        if (pointerKind.current === 'touch' && activeTooltip === id) { dismiss(); return; }
        if (pointerKind.current === 'touch') pointer.current = null;
        dismissed.current = false;
        claim(id);
        schedulePlace();
      }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.stopPropagation();
        if (activeTooltip === id) dismiss();
        else { pointer.current = null; dismissed.current = false; claim(id); }
      }}
    >{children}</span>
    {open && createPortal(
      <span
        ref={tipRef}
        id={id}
        role="tooltip"
        data-pointer-tooltip=""
        className={cn('glass pointer-events-none fixed z-[88] overflow-hidden rounded-md border border-line shadow-sh-2', contentClassName)}
        // The app's reduced-motion rule assigns a duration to every element.
        // Disable transition properties so cursor coordinates never tween from (0, 0).
        style={{ left: 0, top: 0, width, maxWidth: 'calc(100vw - 16px)', maxHeight: 'calc(100dvh - 16px)', visibility: 'hidden', transitionProperty: 'none' }}
      >{content}</span>,
      document.body,
    )}
  </>;
}
