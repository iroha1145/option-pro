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
 * 数值快照决定闪色，独立计时器负责结束。相同报价换数组或回调引用时不重启动画；
 * 删除、缺失的行从快照中移除，再出现时不与陈旧价格比较。
 */
import { useEffect, useMemo, useRef, useState } from 'react';

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
  const [previous, setPrevious] = useState<Map<string, number>>(() => new Map());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const values = useMemo(() => {
    if (!rows) return null;
    const next = new Map<string, number>();
    for (const row of rows) {
      const value = valueOf(row);
      if (value !== null && Number.isFinite(value)) next.set(keyOf(row), value);
    }
    return next;
  }, [rows, keyOf, valueOf]);

  const changed = values !== null && (values.size !== previous.size
    || [...values].some(([key, value]) => previous.get(key) !== value));
  if (changed && values) {
    const next: [string, FlashDirection][] = [];
    for (const [key, value] of values) {
      const prior = previous.get(key);
      if (prior !== undefined && prior !== value) next.push([key, value > prior ? 'up' : 'down']);
    }
    setPrevious(values);
    setFlashes(Object.fromEntries(next));
  }

  useEffect(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    if (Object.keys(flashes).length === 0) return;
    const activeTimer = setTimeout(() => {
      if (timer.current !== activeTimer) return;
      timer.current = null;
      setFlashes((current) => current === flashes ? {} : current);
    }, durationMs);
    timer.current = activeTimer;
    return () => {
      clearTimeout(activeTimer);
      if (timer.current === activeTimer) timer.current = null;
    };
  }, [flashes, durationMs]);

  return flashes;
}
