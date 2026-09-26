/**
 * CollapsePresence —— 条件渲染内容的展开/收起（transitions.dev 21-accordion：
 * grid-template-rows 0fr ↔ 1fr，由 CSS 补间），替代 framer 的 height:auto——
 * 后者每一帧都要测量并改写真实高度，大块内容（选股行展开、日历切换）会掉帧。
 *
 * 收起时保持挂载到 --acc-collapse 结束再卸载（useOverlayPhase 的同一套阶段机）；
 * 展开到位后加 is-settled 放开裁剪与滤镜：blur(0) 仍会让 fixed 后代以此为包含块
 * 并常驻合成层，overflow:hidden 会裁掉行内浮层。
 */
import { useEffect, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { overlayVisible, readRootDurationMs, useOverlayPhase } from '@/lib/transitions';

export default function CollapsePresence({
  open,
  id,
  className,
  wrap,
  children,
}: {
  open: boolean;
  id?: string;
  className?: string;
  /** 只在可见期间套一层外壳（如表格里的 tr/td）：收起后外壳也不留空行。 */
  wrap?: (panel: ReactNode) => ReactNode;
  children: ReactNode;
}) {
  const phase = useOverlayPhase(open, readRootDurationMs('--acc-collapse', 250));
  const [settled, setSettled] = useState(false);
  const [previousPhase, setPreviousPhase] = useState(phase);
  // 阶段一变就先收回 settled（同次渲染派生），收起的第一帧就恢复裁剪与补间。
  if (previousPhase !== phase) {
    setPreviousPhase(phase);
    if (settled) setSettled(false);
  }

  useEffect(() => {
    if (phase !== 'open') return;
    const timer = window.setTimeout(() => setSettled(true), readRootDurationMs('--acc-expand', 250));
    return () => window.clearTimeout(timer);
  }, [phase]);

  if (!overlayVisible(open, phase)) return null;
  const panel = (
    <div
      id={id}
      className={cn('t-acc', settled && 'is-settled', className)}
      data-open={phase === 'open' ? 'true' : 'false'}
      inert={!open}
      aria-hidden={!open}
    >
      <div className="t-acc-panel">
        <div className="t-acc-panel-inner">{children}</div>
      </div>
    </div>
  );
  return wrap ? wrap(panel) : panel;
}
