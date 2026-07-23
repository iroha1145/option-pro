/** AI 任务轮询 Hook：2.5s 节奏至 succeeded/failed/cancelled 终止（§11） */
import { useCallback, useEffect, useRef, useState } from 'react';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import type { AiJob } from '@/api/types';

export function useAiJob() {
  const [job, setJob] = useState<AiJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const aliveRef = useRef(true);

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      stop();
    };
  }, [stop]);

  const poll = useCallback(
    (id: string) => {
      stop();
      timerRef.current = setInterval(async () => {
        try {
          const j = await aiJobsApi.get(id);
          if (!aliveRef.current) return;
          setJob(j);
          if (j.status === 'succeeded' || j.status === 'failed' || j.status === 'cancelled') stop();
        } catch (e) {
          if (!aliveRef.current) return;
          setError(e instanceof Error ? e.message : '任务查询失败');
          stop();
        }
      }, 2500);
    },
    [stop],
  );

  const start = useCallback(
    async (create: () => Promise<AiJob>) => {
      setError(null);
      setJob(null);
      try {
        const j = await create();
        if (!aliveRef.current) return;
        setJob(j);
        poll(j.id);
      } catch (e) {
        if (!aliveRef.current) return;
        setError(e instanceof Error ? e.message : '任务创建失败');
      }
    },
    [poll],
  );

  const cancel = useCallback(async () => {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return;
    try {
      const j = await aiJobsApi.cancel(job.id);
      setJob(j);
      stop();
    } catch {
      /* 忽略取消失败 */
    }
  }, [job, stop]);

  const reset = useCallback(() => {
    stop();
    setJob(null);
    setError(null);
  }, [stop]);

  return { job, error, start, cancel, reset };
}

/** AI 输出纪律脚注：影响分非收益 · 置信度非胜率 */
export const AI_DISCLAIMER = 'AI 输出仅供研究 · 影响分为方向性估计 · 非收益预测 · 置信度非胜率';
