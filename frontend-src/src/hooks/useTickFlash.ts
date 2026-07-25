/**
 * 报价变动闪烁（GPT-5.6-Pro 审计 P2-5）。
 *
 * 自选、突破雷达、指数跑马灯和选股各写了一份同构逻辑，并且都有同一个缺陷：
 *
 *   1. 有变化 → 置闪烁状态 + 创建 700ms 定时器（作为 effect 的 cleanup 返回）
 *   2. 下一轮数据较快到达 → 旧 effect 的 cleanup 清掉定时器
 *   3. 这一轮没有价格变化 → 不创建新定时器
 *   4. 第 1 步的闪烁状态再也没人清除
 *
 * 于是某几行会永久停在闪烁态。修法是把定时器放在独立的 ref 里，每轮数据到达先
 * 清理并显式复位，再决定是否启动新的闪烁 —— cleanup 不再兼任「结束动画」。
 */
import { useEffect, useRef, useState } from 'react';

export type FlashDirection = 'up' | 'down';
export type FlashMap = Record<string, FlashDirection>;

export const FLASH_DURATION_MS = 700;

/**
 * @param rows    当前一轮数据；null/undefined 表示还没有数据，保持现状
 * @param keyOf   行标识
 * @param valueOf 参与比较的数值；返回 null 表示本行本轮无可比数值
 */
export function useTickFlash<T>(
  rows: readonly T[] | null | undefined,
  keyOf: (row: T) => string,
  valueOf: (row: T) => number | null,
  durationMs: number = FLASH_DURATION_MS,
): FlashMap {
  const [flashes, setFlashes] = useState<FlashMap>({});
  const previous = useRef<Record<string, number>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!rows) return;
    // 每轮先清掉上一轮的定时器与状态：闪烁的结束不能依赖「下一轮恰好也有变化」。
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const next: FlashMap = {};
    for (const row of rows) {
      const key = keyOf(row);
      const value = valueOf(row);
      if (value === null || !Number.isFinite(value)) continue;
      const prior = previous.current[key];
      if (prior !== undefined && prior !== value) {
        next[key] = value > prior ? 'up' : 'down';
      }
      previous.current[key] = value;
    }
    // 只在真的有变化、或需要从「有闪烁」回到「无闪烁」时写状态：调用方若传入
    // 每次渲染都换引用的数组，无条件 setState 会把效应变成无限循环。
    setFlashes((current) =>
      Object.keys(next).length === 0 && Object.keys(current).length === 0 ? current : next,
    );
    if (Object.keys(next).length > 0) {
      timer.current = setTimeout(() => {
        timer.current = null;
        setFlashes({});
      }, durationMs);
    }
    // 只在卸载时清理定时器；状态复位由下一轮开头统一处理。
  }, [rows, keyOf, valueOf, durationMs]);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  return flashes;
}
