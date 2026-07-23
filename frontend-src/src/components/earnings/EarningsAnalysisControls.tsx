import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { adminApi, type RuntimeDoc, type WorkerHealth } from '@/api/modules/admin';
import { runtimeApi, type WorkerAction } from '@/api/modules/runtime';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : '请求失败';
}

interface EarningsRunSummary {
  eligible: number;
  queued: number;
  existing: number;
  invalid: number;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function count(value: unknown): number | null {
  const parsed = typeof value === 'string' ? Number(value) : value;
  return typeof parsed === 'number' && Number.isFinite(parsed) && parsed >= 0
    ? Math.floor(parsed)
    : null;
}

function readRunSummary(action: WorkerAction): EarningsRunSummary | null {
  const nested = asRecord(action.details.result);
  const result = Object.keys(nested).length > 0 ? nested : action.details;
  const eligible = count(result.eligible);
  const queued = count(result.queued);
  const existing = count(result.existing);
  const invalid = count(result.invalid);
  if (eligible === null || queued === null || existing === null || invalid === null) return null;
  return { eligible, queued, existing, invalid };
}

function summaryText(summary: EarningsRunSummary): string {
  return `范围内 ${summary.eligible} · 新建 ${summary.queued} · 已存在 ${summary.existing} · 无效 ${summary.invalid}`;
}

async function requestEarningsAnalysis(): Promise<WorkerAction> {
  const action = await runtimeApi.workerAction('earnings_analysis');
  if (action.status === 'completed') return action;
  if (!action.requestId) throw new Error('后台未返回任务编号');
  return runtimeApi.waitForWorkerAction(action.requestId);
}

export default function EarningsAnalysisControls() {
  const { isOwner } = useAccess();
  const toast = useToast();
  const [doc, setDoc] = useState<RuntimeDoc | null>(null);
  const [worker, setWorker] = useState<WorkerHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<EarningsRunSummary | null>(null);
  const [lastRunNote, setLastRunNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!isOwner) return;
    setLoading(true);
    try {
      const [runtime, health] = await Promise.all([
        adminApi.runtimeSettings(),
        adminApi.workerStatus(),
      ]);
      setDoc(runtime);
      setWorker(health);
      setError(null);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setLoading(false);
    }
  }, [isOwner]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!isOwner) return null;

  const enabled = doc?.toggles.earningsScheduledAnalysisEnabled ?? false;
  const task = worker?.tasks.find((item) => item.name === 'earnings_analysis');
  const taskReady = worker?.healthy === true && task?.enabled === true && task?.healthy === true;

  const completeAnalysis = async (): Promise<EarningsRunSummary | null> => {
    const action = await requestEarningsAnalysis();
    const summary = readRunSummary(action);
    setLastRun(summary);
    setLastRunNote(summary ? null : '任务已完成，但后台未返回逐项汇总');
    void adminApi.workerStatus().then(setWorker).catch(() => undefined);
    return summary;
  };

  const updateSchedule = async () => {
    if (!doc || loading) return;
    const nextEnabled = !enabled;
    setLoading(true);
    try {
      const next = await adminApi.updateRuntimeSettings(doc.version, {
        earningsScheduledAnalysisEnabled: nextEnabled,
      });
      setDoc(next);
      setError(null);
      toast.success(nextEnabled ? '每日财报分析已开启' : '每日财报分析已关闭', '范围固定为未来 30 天');
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 409) {
        toast.error('设置版本已变化', '已重新读取最新设置');
        await load();
      } else {
        const message = errorText(cause);
        setError(message);
        toast.error('保存失败', message);
      }
      return;
    } finally {
      setLoading(false);
    }

    if (!nextEnabled || !taskReady) return;
    setRunning(true);
    try {
      const summary = await completeAnalysis();
      setError(null);
      if (summary) {
        toast.success('首次财报任务检查已完成', summaryText(summary));
      } else {
        toast.info('首次财报任务检查已完成', '后台未返回逐项汇总');
      }
    } catch (cause) {
      const message = errorText(cause);
      setError(message);
      toast.error('自动分析设置已保存，但首次任务失败', message);
    } finally {
      setRunning(false);
    }
  };

  const runNow = async () => {
    if (running) return;
    setRunning(true);
    try {
      const summary = await completeAnalysis();
      if (summary) {
        toast.success('财报任务检查已完成', summaryText(summary));
      } else {
        toast.info('财报任务检查已完成', '后台未返回逐项汇总');
      }
      setError(null);
    } catch (cause) {
      const message = errorText(cause);
      setError(message);
      toast.error('财报分析失败', message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="card-surface overflow-hidden" aria-label="财报分析管理">
      <div className="border-l-2 border-ai-600 px-4 py-3.5">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-ai-50 text-ai-600">
            <Icon name="spark-ai" size={15} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h2 className="text-body-s font-medium text-ink-800">财报分析管理</h2>
              <span className="rounded-pill border border-line px-2 py-0.5 font-mono text-micro text-ink-400">
                未来 30 天
              </span>
            </div>
            <p className="mt-1 text-micro leading-5 text-ink-400">
              每只新增财报只建立一个持久任务；重复执行会跳过同一代码、同一财报日。
            </p>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            onClick={() => void updateSchedule()}
            disabled={!doc || loading || running}
            className={cn(
              'inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-caption transition-colors',
              enabled
                ? 'border-ai-600/30 bg-ai-50 text-ai-600'
                : 'border-line bg-card text-ink-500',
              (!doc || loading || running) && 'cursor-wait opacity-60',
            )}
          >
            <span
              className={cn('size-2 rounded-full', enabled ? 'bg-ai-600' : 'bg-ink-300')}
              aria-hidden="true"
            />
            每日自动分析
          </button>
          <button
            type="button"
            onClick={() => void runNow()}
            disabled={!taskReady || running}
            title={!taskReady ? '财报分析后台任务当前不可用' : '立即分析未来 30 天内尚未建立任务的财报'}
            className={cn(
              'inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-caption font-medium transition-colors',
              taskReady && !running
                ? 'border-line bg-card text-ink-600 hover:border-ai-600/40 hover:text-ai-600'
                : 'cursor-not-allowed border-line bg-card-warm text-ink-300',
            )}
          >
            <Icon name="refresh" size={12} className={running ? 'animate-spin' : ''} />
            {running ? '等待任务检查' : '立即分析新的财报'}
          </button>
        </div>

        <p className="mt-2 font-mono text-micro text-ink-400">
          {running
            ? '正在检查并建立未来 30 天内尚未建立的分析任务…'
            : loading
            ? '读取运行状态…'
            : taskReady
              ? enabled
                ? '自动任务已开启'
                : '自动任务已关闭，仍可手动执行'
              : '后台任务不可用'}
        </p>
        {(lastRun || lastRunNote) && (
          <p className="mt-2 border-t border-line pt-2 text-micro text-ink-500" role="status" aria-live="polite">
            <span className="mr-2 text-ink-300">最近任务检查</span>
            {lastRun ? summaryText(lastRun) : lastRunNote}
          </p>
        )}
        {error && <p className="mt-1 text-micro text-down-700">{error}</p>}
      </div>
    </section>
  );
}
