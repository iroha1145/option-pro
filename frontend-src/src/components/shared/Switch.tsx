/**
 * Switch —— 全站唯一的拨杆开关（transitions.dev 27-toggle：拇指双段回弹）。
 *
 * 此前散落着四份手搓轨道（LayerMenu / FilterBar / ManagePanel / Breakouts），
 * 三种尺寸、三种动画写法，stopPropagation、禁用态、is-init 这些修复只落在其中
 * 一份。收口成一个组件，尺寸走 size 三档，几何由表驱动：
 *   sm 28×16 / 拇指 12 / 行程 12   md 32×18 / 14 / 14   lg 36×20 / 16 / 16
 *   （行程 = 轨宽 − 2×边距(2) − 拇指）
 * is-init 只在首次交互后加：否则挂载时每个「开」态开关都要空放一次回弹。
 * 只渲染轨道本体；文字标签由调用方用 <label> 包裹（button 是 labelable 元素，
 * 点文字会激活它），或直接给 aria-label。
 */
import { useState, type CSSProperties } from 'react';
import { cn } from '@/lib/utils';

export type SwitchSize = 'sm' | 'md' | 'lg';

const GEOMETRY: Record<SwitchSize, { track: string; thumb: string; travel: number }> = {
  sm: { track: 'h-4 w-7', thumb: 'size-3', travel: 12 },
  md: { track: 'h-[18px] w-8', thumb: 'size-[14px]', travel: 14 },
  lg: { track: 'h-5 w-9', thumb: 'size-4', travel: 16 },
};

export default function Switch({
  checked,
  onToggle,
  label,
  disabled,
  size = 'md',
  className,
  id,
}: {
  checked: boolean;
  onToggle: () => void;
  /** 可访问名；被 <label> 包裹时可省略。 */
  label?: string;
  disabled?: boolean;
  size?: SwitchSize;
  className?: string;
  id?: string;
}) {
  const [init, setInit] = useState(false);
  const geo = GEOMETRY[size];
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={(event) => {
        // 所在行往往也可点：拨杆自吃事件，避免冒泡到行再翻一次等于没动。
        event.stopPropagation();
        if (disabled) return;
        setInit(true);
        onToggle();
      }}
      data-on={checked ? 'true' : 'false'}
      style={{ '--toggle-travel': `${geo.travel}px` } as CSSProperties}
      className={cn(
        't-toggle relative shrink-0 rounded-pill shadow-track transition-transform duration-fast active:scale-95',
        init && 'is-init',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
        geo.track,
        checked ? 'bg-brand-600' : 'bg-ink-300',
        disabled && 'cursor-not-allowed opacity-40',
        className,
      )}
    >
      <span className={cn('t-toggle-thumb absolute left-[2px] top-[2px] rounded-full bg-card shadow-knob', geo.thumb)} />
    </button>
  );
}
