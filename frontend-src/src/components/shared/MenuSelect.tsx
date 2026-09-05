/**
 * Select composition adapted from shadcn/ui (MIT).
 * Radix owns focus, typeahead, Escape and collision handling; the portal avoids
 * clipping by a table's scroll container. Paint/clock follow our t-dropdown system.
 */
import { useCallback, useRef, type ReactNode } from 'react';
import { registerFocusPortal } from '@/lib/focusScope';
import * as Select from '@radix-ui/react-select';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';

export default function MenuSelect<T extends string | number>({
  value, onChange, options, ariaLabel, className, triggerClassName,
  align = 'left', leading,
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
  // Index keys preserve numeric values and allow an empty string option: Radix
  // reserves the empty string for its placeholder, and all its values are strings.
  const triggerRef = useRef<HTMLButtonElement>(null);
  const releasePortal = useRef<(() => void) | null>(null);
  const contentRef = useCallback((node: HTMLDivElement | null) => {
    releasePortal.current?.();
    releasePortal.current = node ? registerFocusPortal(node, triggerRef.current) : null;
  }, []);
  const selected = options.findIndex((o) => o.value === value);
  return (
    <div className={className}>
      <Select.Root
        value={selected >= 0 ? `option-${selected}` : ''}
        disabled={options.length === 0}
        onValueChange={(key) => {
          const option = options[Number(key.slice(7))];
          if (option) onChange(option.value);
        }}
      >
        <Select.Trigger
          ref={triggerRef}
          aria-label={ariaLabel}
          className={cn(
            'flex min-h-9 max-w-full items-center gap-1.5 rounded-md border border-line-strong bg-card pl-2.5 pr-2 text-caption text-ink-600 shadow-btn transition-colors duration-fast hover:border-brand-400 data-[state=open]:border-brand-400 data-[state=open]:text-brand-600 disabled:cursor-not-allowed disabled:opacity-50',
            triggerClassName,
          )}
        >
          {leading}
          <span className="min-w-0 truncate"><Select.Value placeholder="—" /></span>
          <Select.Icon asChild><Icon name="chevron-down" size={12} className="shrink-0 text-ink-400" /></Select.Icon>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            ref={contentRef}
            position="popper"
            align={align === 'right' ? 'end' : 'start'}
            sideOffset={6}
            collisionPadding={12}
            className="select-surface z-[85] overflow-hidden rounded-lg border border-line-strong bg-card p-1 shadow-sh-3"
          >
            <Select.ScrollUpButton className="flex h-6 items-center justify-center text-ink-500">
              <Icon name="chevron-down" size={12} className="rotate-180" />
            </Select.ScrollUpButton>
            <Select.Viewport>
              {options.map((option, index) => (
                <Select.Item
                  key={`${typeof option.value}-${String(option.value)}`}
                  value={`option-${index}`}
                  className="relative flex min-h-9 cursor-default select-none items-center gap-3 rounded-md py-2 pl-2.5 pr-8 text-caption text-ink-600 outline-none data-[highlighted]:bg-brand-50 data-[highlighted]:text-brand-700 data-[state=checked]:font-semibold"
                >
                  <Select.ItemText>{option.label}</Select.ItemText>
                  <Select.ItemIndicator className="absolute right-2 text-brand-600"><Icon name="check" size={13} /></Select.ItemIndicator>
                </Select.Item>
              ))}
            </Select.Viewport>
            <Select.ScrollDownButton className="flex h-6 items-center justify-center text-ink-500">
              <Icon name="chevron-down" size={12} />
            </Select.ScrollDownButton>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  );
}
