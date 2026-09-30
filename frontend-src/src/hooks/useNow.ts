import { useEffect, useState } from 'react';

/** 按间隔走字的当前时间。intervalMs <= 0 时只取一次，不挂定时器。 */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!intervalMs || intervalMs <= 0) return undefined;
    let timer: ReturnType<typeof setInterval> | undefined;
    const stop = () => { clearInterval(timer); timer = undefined; };
    const resume = () => {
      stop();
      if (document.hidden) return;
      setNow(Date.now());
      timer = setInterval(() => setNow(Date.now()), intervalMs);
    };
    if (!document.hidden) timer = setInterval(() => setNow(Date.now()), intervalMs);
    document.addEventListener('visibilitychange', resume);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', resume);
    };
  }, [intervalMs]);
  return now;
}
