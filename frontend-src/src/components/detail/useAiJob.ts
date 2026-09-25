/**
 * 持续查询同一任务至服务端终态；连续查询失败时暂停，保留任务供恢复。
 * 页面隐藏时不发查询，回到前台立即补查一次。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import { aiJobCreateErrorMessage } from '@/api/aiJobNormalize';
import { ApiError } from '@/api/client';
import type { AiJob } from '@/api/types';
import { t } from '../../i18n/core.ts';

const TERMINAL: ReadonlySet<string> = new Set(['succeeded', 'failed', 'cancelled']);
const MAX_QUERY_FAILURES = 5;
/** 创建响应没有 Retry-After 时的首次查询间隔，与创建接口契约的 Retry-After: 2 一致。 */
const FIRST_POLL_MS = 2000;
type QueryIssue = 'retrying' | 'paused' | 'blocked' | 'missing' | null;

function canRetryQuery(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.retryable !== false && (error.code === 0 || error.code === 408 || error.code === 429 || error.code >= 500);
  }
  return error instanceof TypeError;
}

/**
 * 后端确认任务记录不存在：FastAPI 的 404 带 detail「AI job not found」，mock 带 job_not_found。
 * 代理层或路由错误的 404 不算，那时任务可能还在跑，放下它会让人重复发起付费任务。
 */
function jobRecordMissing(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.code !== 404) return false;
  if (error.bizCode === 'job_not_found') return true;
  const payload = error.payload;
  return typeof payload === 'object' && payload !== null
    && (payload as { detail?: unknown }).detail === 'AI job not found';
}

/** 服务端给的等待秒数钳到 1–300 秒，防止异常值把轮询拖死或打成忙等。 */
function retryAfterMs(seconds: number | null | undefined): number | null {
  return typeof seconds === 'number' && Number.isFinite(seconds)
    ? Math.min(300, Math.max(1, seconds)) * 1000
    : null;
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
  /** 页面隐藏时到点的那一拍挂在这里，回到前台再执行。 */
  const parkedTickRef = useRef<(() => void) | null>(null);
  const aliveRef = useRef(true);
  const generationRef = useRef(0);
  const activeJobRef = useRef<string | null>(null);
  const cancelingRef = useRef(false);
  /** 已被服务端记下取消请求的任务：取消前发出、取消后才回来的旧查询不能把标记冲掉。 */
  const cancelRequestedIdRef = useRef<string | null>(null);

  const stop = useCallback(() => {
    generationRef.current += 1;
    parkedTickRef.current = null;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    const onVisibilityChange = () => {
      if (document.hidden) return;
      const parked = parkedTickRef.current;
      parkedTickRef.current = null;
      parked?.();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      aliveRef.current = false;
      document.removeEventListener('visibilitychange', onVisibilityChange);
      stop();
    };
  }, [stop]);

  const poll = useCallback(
    (id: string, firstDelayMs: number) => {
      stop();
      const generation = generationRef.current;
      setQueryIssue(null);
      setError(null);
      let attempt = 0;
      let failures = 0;
      const tick = async () => {
        if (!aliveRef.current || generation !== generationRef.current) return;
        timerRef.current = null;
        if (document.hidden) {
          parkedTickRef.current = () => void tick();
          return;
        }
        try {
          const fetched = await aiJobsApi.get(id);
          if (!aliveRef.current || generation !== generationRef.current) return;
          failures = 0;
          const j = cancelRequestedIdRef.current === fetched.id && !TERMINAL.has(fetched.status)
            ? { ...fetched, cancelRequested: true }
            : fetched;
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
          if (jobRecordMissing(e)) {
            /* 记录已不存在，再查也只会 404：放下这个任务，允许重新发起。 */
            activeJobRef.current = null;
            setJob(null);
            setError(t('任务记录已不存在'));
            setQueryIssue('missing');
            stop();
            return;
          }
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
          const delay = retryAfterMs(e instanceof ApiError ? e.retryAfter : undefined)
            ?? [2000, 5000, 10000, 30000][failures - 1];
          timerRef.current = setTimeout(() => void tick(), delay);
          return;
        }
        if (!aliveRef.current || generation !== generationRef.current) return;
        const delays = [2000, 3000, 5000, 8000, 10000];
        const delay = delays[Math.min(attempt, delays.length - 1)];
        attempt += 1;
        timerRef.current = setTimeout(() => void tick(), delay);
      };
      timerRef.current = setTimeout(() => void tick(), firstDelayMs);
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
        poll(j.id, retryAfterMs(j.retryAfter) ?? FIRST_POLL_MS);
      } catch (e) {
        if (!aliveRef.current || generation !== generationRef.current) return;
        setError(aiJobCreateErrorMessage(e));
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
      job.cancelRequested ||
      cancelingRef.current ||
      !['queued', 'in_progress', 'running'].includes(job.status)
    ) {
      return;
    }
    const generation = generationRef.current;
    cancelingRef.current = true;
    try {
      const j = await aiJobsApi.cancel(job.id);
      if (!aliveRef.current || generation !== generationRef.current) return;
      if (j.cancelRequested) cancelRequestedIdRef.current = j.id;
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
        poll(j.id, FIRST_POLL_MS);
      }
    } catch (e) {
      if (!aliveRef.current || generation !== generationRef.current) return;
      setError(e instanceof Error ? e.message : t('取消失败'));
    } finally {
      cancelingRef.current = false;
    }
  }, [job, poll, queryIssue, stop]);

  const resume = useCallback(() => {
    if (job && !TERMINAL.has(job.status) && (queryIssue === 'paused' || queryIssue === 'blocked')) {
      poll(job.id, 0);
    }
  }, [job, poll, queryIssue]);

  const reset = useCallback(() => {
    // 查询失败不等于任务失败，不能忘记仍在运行的任务后重新收费创建。
    if (activeJobRef.current !== null) return;
    stop();
    setJob(null);
    setError(null);
    setQueryIssue(null);
  }, [stop]);

  return { job, error, queryIssue, starting, start, cancel, resume, reset };
}
