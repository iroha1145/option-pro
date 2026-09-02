/** AI 任务轮询 Hook：退避 [2,3,5,8,10]s 至 succeeded/failed/cancelled 终止，总超时 5 分钟（§11） */
import { useCallback, useEffect, useRef, useState } from 'react';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import type { AiJob } from '@/api/types';
import { t } from '../../i18n/core.ts';

const TERMINAL: ReadonlySet<string> = new Set(['succeeded', 'failed', 'cancelled']);

export function useAiJob() {
  const [job, setJob] = useState<AiJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  /* 提交在途门闩：POST 发出到 job 回来的窗口里 job 仍是 null，没有它
     「生成分析」按钮立刻恢复可点，双击会创建两个付费任务（个股路径的
     evidence_as_of 微秒时间戳让服务端 request_hash 必然不同、去重失效）。 */
  const [starting, setStarting] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);
  const generationRef = useRef(0);

  const stop = useCallback(() => {
    generationRef.current += 1;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
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
      const generation = generationRef.current;
      const deadline = Date.now() + 5 * 60_000;
      const tick = async () => {
        if (!aliveRef.current || generation !== generationRef.current) return;
        if (Date.now() >= deadline) {
          setError(`${t('分析任务仍在处理中')} · ${t('稍后刷新页面可继续查看结果')}`);
          stop();
          return;
        }
        try {
          const j = await aiJobsApi.get(id);
          if (!aliveRef.current || generation !== generationRef.current) return;
          setJob(j);
          // 轮询仍在继续时唯一可能挂着的 error 是「取消失败」：新状态到手就清掉，
          // 别让一条红字陪着后续的成功结果常驻。
          setError(null);
          if (TERMINAL.has(j.status)) {
            stop();
            return;
          }
        } catch (e) {
          if (!aliveRef.current || generation !== generationRef.current) return;
          setError(e instanceof Error ? e.message : t('任务查询失败'));
          stop();
          return;
        }
        if (!aliveRef.current || generation !== generationRef.current) return;
        const delays = [2000, 3000, 5000, 8000, 10000];
        const delay = typeof document !== 'undefined' && document.hidden
          ? 10000
          : delays[Math.min(attempt, delays.length - 1)];
        attempt += 1;
        timerRef.current = setTimeout(() => void tick(), delay);
      };
      let attempt = 0;
      timerRef.current = setTimeout(() => void tick(), 2000);
    },
    [stop],
  );

  const startingRef = useRef(false);
  const start = useCallback(
    async (create: () => Promise<AiJob>) => {
      if (startingRef.current) return; // 双击/竞态下第二次提交直接吞掉
      startingRef.current = true;
      setStarting(true);
      setError(null);
      setJob(null);
      try {
        const j = await create();
        if (!aliveRef.current) return;
        setJob(j);
        if (TERMINAL.has(j.status)) return;
        poll(j.id);
      } catch (e) {
        if (!aliveRef.current) return;
        setError(e instanceof Error ? e.message : t('任务创建失败'));
      } finally {
        startingRef.current = false;
        if (aliveRef.current) setStarting(false);
      }
    },
    [poll],
  );

  const cancel = useCallback(async () => {
    if (
      !job ||
      !['queued', 'in_progress', 'running'].includes(job.status)
    ) {
      return;
    }
    try {
      const j = await aiJobsApi.cancel(job.id);
      if (!aliveRef.current) return;
      setJob(j);
      setError(null);
      /* 后端对 in_progress 只落 cancel_requested_at、status 原样返回（非终态）：
         此时不能 stop()——worker 稍后才真正转 cancelled，轮询要留着去观察它，
         否则 UI 永远停在「模型正在处理…」。只有服务端直接给了终态（排队中的
         任务立即 cancelled）才收尾。 */
      if (TERMINAL.has(j.status)) stop();
    } catch (e) {
      if (!aliveRef.current) return;
      setError(e instanceof Error ? e.message : t('取消失败'));
    }
  }, [job, stop]);

  const reset = useCallback(() => {
    stop();
    setJob(null);
    setError(null);
  }, [stop]);

  return { job, error, starting, start, cancel, reset };
}

/** AI 输出纪律脚注：影响分非收益 · 置信度非胜率 */
export const AI_DISCLAIMER = t('AI 输出仅供研究 · 影响分为方向性估计 · 非收益预测 · 置信度非胜率');
