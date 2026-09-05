import { useEffect, useRef, useState } from 'react';
import { usePrefersReducedMotion } from './usePrefersReducedMotion';

/**
 * 初次显示真实值，后续变化从当前显示位置过渡；读屏层由调用方呈现最终值。
 * 参考 Rare UI Animated Counter 的首帧与减少动态效果处理，保留现有数字样式。
 */
export function useCountUp(target: number, duration = 900): number {
  const reduced = usePrefersReducedMotion();
  const [value, setValue] = useState(target);
  const displayedRef = useRef(target);

  useEffect(() => {
    const from = displayedRef.current;
    const instant = reduced || document.hidden || !Number.isFinite(target) || !Number.isFinite(from)
      || !Number.isFinite(duration) || duration <= 0 || duration > 2_147_483_567 || Object.is(from, target);
    if (instant) {
      // 同步动画时钟的终点；依赖中没有 value，不会由本次更新重新触发效果。
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setValue(target);
      displayedRef.current = target;
      return;
    }

    let active = true;
    let frame = 0;
    let settle = 0;
    const finish = () => {
      if (!active) return;
      active = false;
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
      displayedRef.current = target;
      setValue(target);
    };
    const start = performance.now();
    const step = (now: number) => {
      if (!active) return;
      const t = Math.min(1, (now - start) / duration);
      if (t >= 1) {
        finish();
        return;
      }
      // ease-paper ≈ out-expo
      const eased = 1 - Math.pow(2, -10 * t);
      // Convex interpolation also stays finite for opposite-sign large values.
      const next = from * (1 - eased) + target * eased;
      // 中途换目标时从当前读数接续，不跳回上一轮已完成的数值。
      displayedRef.current = next;
      setValue(next);
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    // 后台帧暂停时仍落定；同时取消旧帧，防止恢复窗口后覆盖最终值。
    settle = window.setTimeout(finish, duration + 80);
    const onVisibility = () => { if (document.hidden) finish(); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      active = false;
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [target, duration, reduced]);

  return reduced || !Number.isFinite(target) || !Number.isFinite(value) ? target : value;
}
