import { useEffect, useState } from 'react';

/** 按间隔走字的当前时间。intervalMs <= 0 时只取一次，不挂定时器。 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!intervalMs || intervalMs <= 0) return undefined;
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
