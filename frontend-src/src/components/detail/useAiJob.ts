/** 持续查询同一任务至服务端终态；连续查询失败时暂停，保留任务供恢复。 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import { ApiError } from '@/api/client';
import type { AiJob } from '@/api/types';
import { t } from '../../i18n/core.ts';

const TERMINAL: ReadonlySet<string> = new Set(['succeeded', 'failed', 'cancelled']);
const MAX_QUERY_FAILURES = 5;
type QueryIssue = 'retrying' | 'paused' | 'blocked' | null;

function canRetryQuery(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.retryable !== false && (error.code === 0 || error.code === 408 || error.code === 429 || error.code >= 500);
  }
  return error instanceof TypeError;
}

export function useAiJob() {
  const [job, setJob] = useState<AiJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queryIssue, setQueryIssue] = useState<QueryIssue>(null);
  /* 提交在途门闩：POST 发出到 job 回来的窗口里 job 仍是 null，没有它
     「生成分析」按钮立刻恢复可点，双击会创建两个付费任务（个股路径的
     evidence_as_of 微秒时间戳让服务端 request_hash 必然不同、去重失效）。 */
  const [starting, setStarting] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);
  const generationRef = useRef(0);
  const activeJobRef = useRef<string | null>(null);

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
    (id: string, immediate = false) => {
      stop();
      const generation = generationRef.current;
      setQueryIssue(null);
      setError(null);
      let attempt = 0;
      let failures = 0;
      const tick = async () => {
        if (!aliveRef.current || generation !== generationRef.current) return;
        timerRef.current = null;
        try {
          const j = await aiJobsApi.get(id);
          if (!aliveRef.current || generation !== generationRef.current) return;
          failures = 0;
          setJob(j);
          // 成功核验后清除此前的网络或取消错误。
          setError(null);
          setQueryIssue(null);
          if (TERMINAL.has(j.status)) {
            activeJobRef.current = null;
            stop();
            return;
          }
        } catch (e) {
          if (!aliveRef.current || generation !== generationRef.current) return;
          failures += 1;
          if (!canRetryQuery(e)) {
            setError(e instanceof Error ? e.message : t('任务查询失败'));
            setQueryIssue('blocked');
            stop();
            return;
          }
          setError(t('暂时无法读取任务状态，任务可能仍在后台运行'));
          if (failures >= MAX_QUERY_FAILURES) {
            setQueryIssue('paused');
            stop();
            return;
          }
          setQueryIssue('retrying');
          const retryAfter = e instanceof ApiError ? e.retryAfter : undefined;
          const delay = typeof retryAfter === 'number' && Number.isFinite(retryAfter)
            ? Math.min(300, Math.max(1, retryAfter)) * 1000
            : [2000, 5000, 10000, 30000][failures - 1];
          timerRef.current = setTimeout(() => void tick(), delay);
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
      timerRef.current = setTimeout(() => void tick(), immediate ? 0 : 2000);
    },
    [stop],
  );

  const startingRef = useRef(false);
  const start = useCallback(
    async (create: () => Promise<AiJob>) => {
      if (!aliveRef.current || startingRef.current || activeJobRef.current !== null) return;
      stop();
      const generation = generationRef.current;
      startingRef.current = true;
      setStarting(true);
      setError(null);
      setQueryIssue(null);
      setJob(null);
      try {
        const j = await create();
        if (!aliveRef.current || generation !== generationRef.current) return;
        setJob(j);
        if (TERMINAL.has(j.status)) return;
        activeJobRef.current = j.id;
        poll(j.id);
      } catch (e) {
        if (!aliveRef.current || generation !== generationRef.current) return;
        setError(e instanceof Error ? e.message : t('任务创建失败'));
      } finally {
        startingRef.current = false;
        if (aliveRef.current) setStarting(false);
      }
    },
    [poll, stop],
  );

  const cancel = useCallback(async () => {
    if (
      !job ||
      !['queued', 'in_progress', 'running'].includes(job.status)
    ) {
      return;
    }
    const generation = generationRef.current;
    try {
      const j = await aiJobsApi.cancel(job.id);
      if (!aliveRef.current || generation !== generationRef.current) return;
      setJob(j);
      setError(null);
      /* 后端对 in_progress 只落 cancel_requested_at、status 原样返回（非终态）：
         此时不能 stop()——worker 稍后才真正转 cancelled，轮询要留着去观察它，
         否则 UI 永远停在「模型正在处理…」。只有服务端直接给了终态（排队中的
         任务立即 cancelled）才收尾。 */
      if (TERMINAL.has(j.status)) {
        activeJobRef.current = null;
        setQueryIssue(null);
        stop();
      } else if (queryIssue === 'paused' || queryIssue === 'blocked') {
        poll(j.id);
      }
    } catch (e) {
      if (!aliveRef.current || generation !== generationRef.current) return;
      setError(e instanceof Error ? e.message : t('取消失败'));
    }
  }, [job, poll, queryIssue, stop]);

  const resume = useCallback(() => {
    if (job && !TERMINAL.has(job.status) && (queryIssue === 'paused' || queryIssue === 'blocked')) {
      poll(job.id, true);
    }
  }, [job, poll, queryIssue]);

  /** 回填已有任务：页面刷新后继续展示结果或接着轮询，不另开付费任务。 */
  const adopt = useCallback(
    (next: AiJob) => {
      if (!aliveRef.current || !next.id) return;
      stop();
      setError(null);
      setQueryIssue(null);
      setJob(next);
      if (TERMINAL.has(next.status)) {
        activeJobRef.current = null;
        return;
      }
      activeJobRef.current = next.id;
      poll(next.id, true);
    },
    [poll, stop],
  );

  const reset = useCallback(() => {
    // 查询失败不等于任务失败，不能忘记仍在运行的任务后重新收费创建。
    if (activeJobRef.current !== null) return;
    stop();
    setJob(null);
    setError(null);
    setQueryIssue(null);
  }, [stop]);

  return { job, error, queryIssue, starting, start, cancel, resume, reset, adopt };
}
