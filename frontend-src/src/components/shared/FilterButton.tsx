import type { ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface FilterButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active: boolean;
}

/** 筛选使用按压按钮语义；面板切换仍由 Segmented 的 tablist 负责。 */
export default function FilterButton({ active, className, type = 'button', ...props }: FilterButtonProps) {
  return (
    <button {...props} type={type} aria-pressed={active} className={cn('control-button', className)} />
  );
}
