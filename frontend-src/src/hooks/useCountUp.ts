import { useEffect, useRef, useState } from 'react';

/** count-up（design.md §4.2）：0→目标 900ms ease-paper，保留原小数位 */
export function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(0);
  const fromRef = useRef(0);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      setValue(target);
      fromRef.current = target;
      return;
    }
    const from = fromRef.current;
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      // ease-paper ≈ out-expo
      const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      setValue(from + (target - from) * eased);
      if (t < 1) rafRef.current = requestAnimationFrame(step);
      else fromRef.current = target;
    };
    rafRef.current = requestAnimationFrame(step);
    /* 兜底：后台/嵌入式环境 rAF 可能被冻结 → 到时强制落到目标值，避免一直显示 0 */
    const settle = window.setTimeout(() => {
      setValue(target);
      fromRef.current = target;
    }, duration + 80);
    return () => {
      cancelAnimationFrame(rafRef.current);
      window.clearTimeout(settle);
    };
  }, [target, duration]);

  return value;
}
