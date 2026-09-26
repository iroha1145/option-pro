/**
 * IconSwap —— 同一格里两枚图标交叉淡换（transitions.dev 09-icon-swap）。
 *
 * 两枚都常驻 DOM、叠在同一个 grid 格里，只切 data-state，所以换图标时按钮
 * 不会因为插入/移除节点而改变宽度，文字也不会被挤动。纯 CSS，无计时器。
 *
 * BusyIcon 是最常见的用法：图标 ↔ 加载圈。此前刷新按钮只给图标转一圈
 * （animate-spin-once，600ms），请求往往还没回来图标就停了，看上去像卡住；
 * 行内重试按钮则干脆静止不动。现在请求在途期间一直转，回来后平滑换回图标。
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import Icon, { type IconName } from '@/components/icons';
import Spinner, { type SpinnerTone } from '@/components/shared/Spinner';

export default function IconSwap({
  state,
  a,
  b,
  className,
}: {
  state: 'a' | 'b';
  a: ReactNode;
  b: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn('t-icon-swap shrink-0 place-items-center', className)} data-state={state} aria-hidden="true">
      <span className="t-icon inline-flex" data-icon="a">{a}</span>
      <span className="t-icon inline-flex" data-icon="b">{b}</span>
    </span>
  );
}

export function BusyIcon({
  busy,
  icon = 'refresh',
  size = 14,
  tone = 'brand',
  className,
}: {
  busy: boolean;
  icon?: IconName;
  size?: number;
  tone?: SpinnerTone;
  className?: string;
}) {
  return (
    <IconSwap
      state={busy ? 'b' : 'a'}
      className={className}
      a={<Icon name={icon} size={size} />}
      b={<Spinner size={Math.max(10, size - 2)} tone={tone} />}
    />
  );
}
