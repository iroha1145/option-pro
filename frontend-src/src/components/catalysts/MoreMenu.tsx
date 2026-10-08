/**
 * 新闻页栏目行右侧的「更多」菜单（2026-10-08 第二版）。
 * 栏目切换只留新闻列表、股票影响、经济日历三项；消息来源和所有者的管理设置不常用，收进这里。
 *
 * 动作型菜单：role="menu" + menuitem，写法沿用页头设置菜单（SettingsMenu）。
 * 打开后焦点进入菜单，方向键、Home、End 在菜单项间移动，Esc 关闭并把焦点还给触发器，
 * Tab 离开时关闭，点菜单外关闭。选中后菜单关闭、焦点回到触发器。
 * 当前停在「更多」里的哪一项，触发器就显示那一项的名字并高亮——此时分段控件里没有选中项。
 */
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { overlayClassName, overlayVisible, readRootDurationMs, useOverlayPhase } from '@/lib/transitions';
import Icon from '@/components/icons';
import { t } from '../../i18n/core.ts';

export type MoreView = 'sources' | 'manage';

const ITEM_CLASS =
  'touch-target flex w-full items-center gap-2 rounded-xs px-2 py-1.5 text-left text-body-s transition-colors duration-fast focus-visible:bg-paper-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600';

interface MoreMenuProps {
  /** 当前停在「更多」里的哪一项；null 表示停在分段控件的某个栏目上。 */
  current: MoreView | null;
  /** 管理设置只对所有者显示。 */
  showManage: boolean;
  onSelect: (view: MoreView) => void;
  className?: string;
}

export default function MoreMenu({ current, showManage, onSelect, className }: MoreMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeMs = readRootDurationMs('--dropdown-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);

  const items: { value: MoreView; label: string }[] = [
    { value: 'sources', label: t('消息来源') },
    ...(showManage ? [{ value: 'manage' as const, label: t('管理设置') }] : []),
  ];
  const triggerLabel = items.find((item) => item.value === current)?.label ?? t('更多');

  useEffect(() => {
    if (!open) return;
    const target =
      menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"][aria-current="true"]') ??
      menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]');
    target?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
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

  const onMenuKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Tab') {
      setOpen(false);
      return;
    }
    const nodes = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
    if (!nodes.length) return;
    const index = nodes.indexOf(document.activeElement as HTMLButtonElement);
    let target = -1;
    if (e.key === 'ArrowDown') target = Math.min(index + 1, nodes.length - 1);
    else if (e.key === 'ArrowUp') target = Math.max(index - 1, 0);
    else if (e.key === 'Home') target = 0;
    else if (e.key === 'End') target = nodes.length - 1;
    else return;
    e.preventDefault();
    nodes[target]?.focus();
  };

  const choose = (view: MoreView) => {
    setOpen(false);
    triggerRef.current?.focus();
    onSelect(view);
  };

  return (
    <div ref={rootRef} className={cn('relative', className)} data-testid="catalyst-more-menu">
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
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          'touch-target inline-flex min-h-9 items-center gap-1.5 rounded-md border px-3 py-1.5 text-caption font-medium shadow-btn transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600',
          current ? 'border-brand-400 bg-brand-50 text-brand-700' : open ? 'border-brand-400 bg-card text-brand-700' : 'border-line bg-card text-ink-600 hover:text-ink-800',
        )}
      >
        {triggerLabel}
        <Icon name="chevron-down" size={12} className={cn('transition-transform duration-fast', open && 'rotate-180')} />
      </button>
      {mounted && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={t('更多')}
          data-origin="top-right"
          onKeyDown={onMenuKeyDown}
          className={cn(
            't-dropdown absolute right-0 top-full z-40 mt-2 min-w-[160px] rounded-md border border-line bg-card p-1.5 shadow-sh-2',
            overlayClassName(phase),
          )}
        >
          {items.map((item) => {
            const active = item.value === current;
            return (
              <button
                key={item.value}
                type="button"
                role="menuitem"
                tabIndex={-1}
                aria-current={active ? 'true' : undefined}
                onClick={() => choose(item.value)}
                className={cn(ITEM_CLASS, active ? 'text-brand-600' : 'text-ink-700 hover:bg-paper-2')}
              >
                <span className="flex-1 whitespace-nowrap">{item.label}</span>
                {active && <Icon name="check" size={13} className="shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
