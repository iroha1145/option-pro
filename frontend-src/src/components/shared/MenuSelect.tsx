/**
 * 自定义下拉：catalog t-dropdown，不用系统菜单。
 * 选股 Top N / 成交额下限、个股期权到期日共用。
 * 替换原生 select 的对价是键盘契约要自己补齐：触发器 ↑↓ 展开、
 * 列表 ↑↓/Home/End 移动焦点、Esc/选中后焦点回触发器、Tab 移出即收起。
 */
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import Icon from '@/components/icons';

export default function MenuSelect<T extends string | number>({
  value,
  onChange,
  options,
  ariaLabel,
  className,
  triggerClassName,
  align = 'left',
  leading,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  ariaLabel?: string;
  className?: string;
  triggerClassName?: string;
  align?: 'left' | 'right';
  leading?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const closeMs = readRootDurationMs('--dropdown-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);
  const current = options.find((o) => o.value === value) ?? options[0];
  const [placement, setPlacement] = useState<'bottom' | 'top'>('bottom');

  useEffect(() => {
    if (!open || !ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    if (spaceBelow < 280 && spaceAbove > spaceBelow) {
      setPlacement('top');
    } else {
      setPlacement('bottom');
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  /* 展开后把焦点放到当前选中项（native select 同款起点），方向键才有意义。
     effect 在 commit 后同步跑，列表此时已在 DOM——不挂 rAF（后台/隐藏页会被
     节流冻结，焦点就永远进不去了）。 */
  useEffect(() => {
    if (!open) return;
    const list = listRef.current;
    if (!list) return;
    const target =
      list.querySelector<HTMLButtonElement>('[role="option"][aria-selected="true"]') ??
      list.querySelector<HTMLButtonElement>('[role="option"]');
    target?.focus();
  }, [open]);

  const selectAndClose = (v: T) => {
    onChange(v);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const onListKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const opts = Array.from(
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [],
    );
    if (!opts.length) return;
    const idx = opts.indexOf(document.activeElement as HTMLButtonElement);
    let target = -1;
    if (e.key === 'ArrowDown') target = Math.min(idx + 1, opts.length - 1);
    else if (e.key === 'ArrowUp') target = Math.max(idx - 1, 0);
    else if (e.key === 'Home') target = 0;
    else if (e.key === 'End') target = opts.length - 1;
    else return;
    e.preventDefault();
    opts[target]?.focus();
  };

  return (
    <div
      ref={ref}
      className={cn('relative', className)}
      /* Tab 把焦点带出整个控件时收起（点击路径由 document mousedown 兜底） */
      onBlur={(e) => {
        if (!ref.current?.contains(e.relatedTarget as Node)) setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setOpen(true);
          }
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel ? `${ariaLabel}, ${current?.label ?? ''}` : undefined}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md border bg-card pl-2.5 pr-2 font-mono text-caption tnum shadow-btn transition-colors duration-fast',
          open ? 'border-brand-400 text-brand-600' : 'border-line text-ink-600 hover:border-line-strong',
          triggerClassName,
        )}
      >
        {leading}
        <span>{current?.label ?? ''}</span>
        <Icon
          name="chevron-down"
          size={12}
          className={cn('shrink-0 text-ink-400 transition-transform duration-200', open && 'rotate-180')}
        />
      </button>
      {mounted && (
        <div
          role="listbox"
          aria-label={ariaLabel}
          data-origin={
            placement === 'top'
              ? align === 'right' ? 'bottom-right' : 'bottom-left'
              : align === 'right' ? 'top-right' : 'top-left'
          }
          className={cn(
            't-dropdown absolute z-40 min-w-full whitespace-nowrap rounded-md border border-line bg-card p-1.5 shadow-sh-2',
            placement === 'top' ? 'bottom-9' : 'top-9',
            align === 'right' ? 'right-0' : 'left-0',
            overlayClassName(phase),
          )}
        >
          <div ref={listRef} className="max-h-64 overflow-y-auto" onKeyDown={onListKeyDown}>
            {options.map((o) => {
              const active = o.value === value;
              return (
                <button
                  key={String(o.value)}
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => selectAndClose(o.value)}
                  className={cn(
                    'flex w-full items-center justify-between gap-3 rounded-xs px-2 py-1.5 text-left font-mono text-caption tnum transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
                    active ? 'bg-brand-50 text-brand-600' : 'text-ink-600 hover:bg-paper-2',
                  )}
                >
                  {o.label}
                  {active && <Icon name="check" size={12} className="shrink-0" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
