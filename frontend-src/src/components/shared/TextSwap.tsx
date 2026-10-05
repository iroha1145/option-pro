/**
 * TextSwap —— 按钮与状态文字原位换句（transitions.dev 04-text-swap 的进场半程）。
 *
 * swapKey 变了才播：新文字从下方 4px、带 2px 模糊淡入，时长与缓动取 --text-swap-*。
 * 旧文字直接让位，不再多等一个退场时长——按钮状态要跟手。首次渲染、同一句的
 * 重渲染都不动；倒计时这类每秒变的数字放进同一个 swapKey，只在阶段切换时播。
 */
import { useState, type ReactNode } from 'react';

export default function TextSwap({ swapKey, children }: { swapKey: string; children: ReactNode }) {
  const [seen, setSeen] = useState({ key: swapKey, changed: false });
  // 渲染期间按上一次的 key 推导「是否换过句」（React 允许的派生状态写法），
  // 这样新文字挂载的第一帧就带着动画类，不会先闪出一帧静止的新字。
  if (seen.key !== swapKey) setSeen({ key: swapKey, changed: true });
  return (
    <span key={swapKey} className={seen.changed && seen.key === swapKey ? 't-text-swap' : undefined}>
      {children}
    </span>
  );
}
