import AnalysisIcon from '@/components/shared/AnalysisIcon';
import { useCallback, useEffect, useState } from 'react';
import { ApiError } from '@/api/client';
import { adminApi, type RuntimeDoc, type WorkerHealth } from '@/api/modules/admin';
import { runtimeApi, type WorkerAction } from '@/api/modules/runtime';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/hooks/useToast';
import Icon from '@/components/icons';
import SoftBadge from '@/components/shared/SoftBadge';
import { cn } from '@/lib/utils';
import { t } from '../../i18n/core.ts';

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.code === 401) return t('登录状态已失效，请重新登录');
  return t('操作未完成，请稍后重试');
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
  return t('范围内 {eligible} · 新安排 {queued} · 已安排 {existing} · 数据无效 {invalid}', { eligible: summary.eligible, queued: summary.queued, existing: summary.existing, invalid: summary.invalid });
}

async function requestEarningsAnalysis(): Promise<WorkerAction> {
  const action = await runtimeApi.workerAction('earnings_analysis');
  if (action.status === 'completed') return action;
  if (!action.requestId) throw new Error(t('分析任务未能启动'));
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
    setLastRunNote(summary ? null : t('检查已完成，暂无明细'));
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
      toast.success(nextEnabled ? t('每日财报分析已开启') : t('每日财报分析已关闭'), t('自动分析未来 5 天内的财报'));
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 409) {
        toast.error(t('设置已更新，请重试'), t('已重新读取最新设置'));
        await load();
      } else {
        const message = errorText(cause);
        setError(message);
        toast.error(t('保存失败'), message);
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
        toast.success(t('已完成首次检查'), summaryText(summary));
      } else {
        toast.info(t('已完成首次检查'), t('暂无检查明细'));
      }
    } catch (cause) {
      const message = errorText(cause);
      setError(message);
      toast.error(t('自动分析设置已保存，但首次任务失败'), message);
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
        toast.success(t('财报检查已完成'), summaryText(summary));
      } else {
        toast.info(t('财报检查已完成'), t('暂无检查明细'));
      }
      setError(null);
    } catch (cause) {
      const message = errorText(cause);
      setError(message);
      toast.error(t('财报分析失败'), message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="card-surface overflow-hidden" aria-label={t("财报分析管理")}>
      {/* v8.1 去左彩条（非引文语义的彩条卡是典型生成式装饰）；AI 归属已由图标章表达 */}
      <div className="px-4 py-3.5">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-ai-50 text-ai-600">
            <AnalysisIcon size={15} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h2 className="text-body-s font-medium text-ink-800">{t('财报分析管理')}</h2>
              <SoftBadge className="font-mono">
                {t('未来 5 天')}
              </SoftBadge>
            </div>
            <p className="mt-1 text-micro leading-5 text-ink-400">
              {t('为未来 5 天内的财报安排分析，同一份财报不会重复安排。')}
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
              'inline-flex h-8 items-center gap-2 rounded-md px-2.5 text-caption font-medium shadow-btn transition-colors',
              enabled
                ? 'bg-ai-600 text-on-accent'
                : 'bg-paper-2 text-ink-600 hover:bg-line',
              (!doc || loading || running) && 'cursor-wait opacity-60',
            )}
          >
            <span
              className={cn('size-2 rounded-full', enabled ? 'bg-white' : 'bg-ink-400')}
              aria-hidden="true"
            />
            {t('每日自动分析')}
          </button>
          <button
            type="button"
            onClick={() => void runNow()}
            disabled={!taskReady || running}
            title={!taskReady ? t('财报分析服务暂不可用') : t('分析未来 5 天内尚未安排分析的财报')}
            className={cn(
              'inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-caption font-medium shadow-btn transition-[filter]',
              taskReady && !running
                ? 'bg-ai-600 text-on-accent hover:brightness-105'
                : 'cursor-not-allowed bg-paper-2 text-ink-400',
            )}
          >
            {running ? <Icon name="refresh" size={14} className="animate-spin" /> : <AnalysisIcon size={14} />}
            {running ? t('正在检查财报…') : t('立即分析新的财报')}
          </button>
        </div>

        <p className="mt-2 font-mono text-micro text-ink-400">
          {running
            ? t('正在为新增财报安排分析…')
            : loading
            ? t('正在读取分析状态…')
            : taskReady
              ? enabled
                ? t('每日自动分析已开启')
                : t('自动分析已关闭，可手动开始')
              : t('分析服务暂不可用')}
        </p>
        {(lastRun || lastRunNote) && (
          <p className="mt-2 border-t border-line pt-2 text-micro text-ink-500" role="status" aria-live="polite">
            <span className="mr-2 text-ink-300">{t('最近检查')}</span>
            {lastRun ? summaryText(lastRun) : lastRunNote}
          </p>
        )}
        {error && <p className="mt-1 text-micro text-down-700">{error}</p>}
      </div>
    </section>
  );
}
