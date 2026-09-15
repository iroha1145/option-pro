import { useEffect, useState } from 'react';
import { etToday } from '@/components/earnings/types';

/**
 * 纽约日历日。不按秒重绘整页，只在 ET 日期变化时更新。
 * 15s 轮询足以跨过午夜；同一秒内 etToday 本身已有记忆化。
 */
export function useEtCalendarDate(): string {
  const [date, setDate] = useState(() => etToday());
  useEffect(() => {
    const tick = () => {
      const next = etToday();
      setDate((current) => (current === next ? current : next));
    };
    const id = window.setInterval(tick, 15_000);
    return () => window.clearInterval(id);
  }, []);
  return date;
}
