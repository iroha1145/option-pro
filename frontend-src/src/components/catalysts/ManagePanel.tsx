/**
 * 管理设置（owner 专属，移植自旧版 deck 催化剂管理区）
 * 第二版起不再是页面上方一条可折叠的卡，而是新闻页的一个栏目：从栏目行右侧「更多」菜单的
 * 「管理设置」进入（?tab=manage），打开即展开，进入时才读取后台状态与运行设置。
 * 三区：更新数据（消息/日历/来源状态 → /api/catalysts/refresh）
 *      后台任务（重点股票/选股评分/突破雷达 → /api/worker/actions/{type} + worker 健康清单）
 *      运行设置（手动分析/定时分析开关 → /api/runtime-settings，乐观锁 + 恢复上一版）
 * 访客不渲染；所有状态如实呈现（冷却/禁用/版本冲突都给出后端原话）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '@/api/client';
import {
  adminApi,
  type RefreshOperation,
  type RuntimeDoc,
  type WorkerActionType,
  type WorkerHealth,
} from '@/api/modules/admin';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/hooks/useToast';
import { invalidateQueryPaths } from '@/api/queryRegistry';
import { resetMarketReadPrefixes } from '@/api/marketRead';
import { bumpAlgorithmViewGeneration } from '@/lib/algorithmView';
import { Led } from './bits';
import Icon from '@/components/icons';
import Spinner from '@/components/shared/Spinner';
import { cn } from '@/lib/utils';
import Segmented from '@/components/shared/Segmented';
import Switch from '@/components/shared/Switch';
import { fmtRelative } from '@/lib/format';
import { t as __t } from '../../i18n/core.ts';
import TextSwap from '@/components/shared/TextSwap';

const REFRESH_OPS: { op: RefreshOperation; label: string }[] = [
  { op: 'news', label: __t('更新消息') },
  { op: 'calendar', label: __t('更新日历') },
  { op: 'source_health', label: __t('检查来源') },
];

const WORKER_ACTIONS: { action: WorkerActionType; label: string }[] = [
  { action: 'focus_refresh', label: __t('重点股票') },
  { action: 'strength_refresh', label: __t('选股评分') },
  { action: 'breakout_refresh', label: __t('突破雷达') },
];

const TASK_CN: Record<string, string> = {
  breakout: __t('突破扫描'),
  catalyst_sync: __t('消息同步'),
  focus: __t('重点股票'),
  ai_jobs: __t('分析任务'),
  maintenance: __t('系统维护'),
  public_home: __t('公开数据'),
  focus_refresh: __t('重点股票更新'),
  strength_refresh: __t('选股评分更新'),
  breakout_refresh: __t('突破雷达更新'),
  retention: __t('数据保留'),
};

function errText(e: unknown): string {
  if (e instanceof ApiError) {
    const cooldown = e.retryAfter != null ? __t('（{n}s 后可重试）', { n: Math.ceil(e.retryAfter) }) : '';
    return `${e.message}${cooldown}`;
  }
  return e instanceof Error ? e.message : __t('请求失败');
}

function SectionCard({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-line bg-card p-4">
      <p className="text-body-s font-medium text-ink-800">{title}</p>
      <p className="mt-0.5 text-micro text-ink-400">{hint}</p>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function ActionButton({ label, busy, onClick }: { label: string; busy: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={cn(
        'flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-caption font-medium shadow-btn transition-colors duration-fast',
        busy
          ? 'cursor-wait border-brand-400 bg-brand-50 text-brand-700'
          : 'border-line bg-card text-ink-600 hover:border-brand-400 hover:text-brand-600',
      )}
    >
      {busy ? <Spinner size={12} tone="brand" /> : <Icon name="refresh" size={12} />}
      {label}
    </button>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex w-full cursor-pointer items-center justify-between gap-3 rounded-md border border-line bg-card-warm px-3 py-2 text-left transition-colors duration-fast hover:border-brand-400">
      <span className="text-caption text-ink-700">{label}</span>
      <Switch size="sm" checked={value} onToggle={() => onChange(!value)} />
    </label>
  );
}

export default function ManagePanel({ onDataRefreshed }: { onDataRefreshed?: () => void }) {
  const { isOwner } = useAccess();
  const toast = useToast();
  const [busyOp, setBusyOp] = useState<string | null>(null);
  /* 忙碌槽的占用只看这个 ref：更新函数（setState 的函数参数）必须是纯函数，React 可能延后执行它，
     在更新函数里「抢槽」再同步读结果，会让第一次点击被吞掉，槽却已被占住、按钮一直转圈。 */
  const busyRef = useRef<string | null>(null);
  const [worker, setWorker] = useState<WorkerHealth | null>(null);
  const [workerErr, setWorkerErr] = useState<string | null>(null);
  const [doc, setDoc] = useState<RuntimeDoc | null>(null);
  const [docErr, setDocErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<{
    manual: boolean;
    scheduled: boolean;
    radarSortAlgorithm: 'production' | 't1_daily_priority';
  } | null>(null);
  const [saving, setSaving] = useState(false);

  const loadOwnerState = useCallback(async () => {
    adminApi.workerStatus().then(
      (w) => {
        setWorker(w);
        setWorkerErr(null);
      },
      (e) => setWorkerErr(errText(e)),
    );
    adminApi.runtimeSettings().then(
      (d) => {
        setDoc(d);
        setDocErr(null);
        setDraft({
          manual: d.toggles.manualAnalysisEnabled ?? false,
          scheduled: d.toggles.scheduledAnalysisEnabled ?? false,
          radarSortAlgorithm: d.algorithms.radarSortAlgorithm,
        });
      },
      (e) => setDocErr(errText(e)),
    );
  }, []);

  useEffect(() => {
    if (isOwner) void loadOwnerState();
  }, [isOwner, loadOwnerState]);

  const runRefresh = useCallback(
    async (op: RefreshOperation, label: string) => {
      // 单槽忙碌态：并发触发会互相清掉对方的 busy。全面板互斥（一次一件事）
      if (busyRef.current !== null) return;
      busyRef.current = `r-${op}`;
      setBusyOp(`r-${op}`);
      try {
        const t = await adminApi.catalystRefresh(op);
        toast.info(__t('{label}已入队', { label }), t.reason ?? undefined);
        // 轻量轮询（最多 5 次），完成后刷新页面数据
        if (t.requestId) {
          for (let i = 0; i < 5; i += 1) {
            await new Promise((r) => setTimeout(r, 3000));
            try {
              const st = await adminApi.catalystRefreshStatus(t.requestId);
              if (st.status === 'completed') {
                toast.success(__t('{label}已完成', { label }));
                onDataRefreshed?.();
                break;
              }
              if (st.status === 'failed') {
                toast.error(__t('{label}失败', { label }), st.reason ?? undefined);
                break;
              }
            } catch (e) {
              toast.error(__t('{label}状态读取失败', { label }), errText(e));
              break;
            }
          }
        }
      } catch (e) {
        toast.error(__t('{label}未能提交', { label }), errText(e));
      } finally {
        if (busyRef.current === `r-${op}`) {
          busyRef.current = null;
          setBusyOp(null);
        }
      }
    },
    [onDataRefreshed, toast],
  );

  const runWorkerAction = useCallback(
    async (action: WorkerActionType, label: string) => {
      if (busyRef.current !== null) return;
      busyRef.current = `w-${action}`;
      setBusyOp(`w-${action}`);
      try {
        const t = await adminApi.workerAction(action);
        if (t.reason === 'cooldown') toast.info(__t('{label}仍在冷却', { label }), __t('稍后自动执行或重试'));
        else if (t.reason === 'already_running') toast.info(__t('{label}正在执行', { label }), __t('已有任务正在进行'));
        else toast.success(__t('{label}更新已入队', { label }));
        adminApi.workerStatus().then(
          (health) => {
            setWorker(health);
            setWorkerErr(null);
          },
          (e) => {
            setWorkerErr(errText(e));
            toast.error(__t('{label}后台状态读取失败', { label }), errText(e));
          },
        );
      } catch (e) {
        toast.error(__t('{label}未能提交', { label }), errText(e));
      } finally {
        if (busyRef.current === `w-${action}`) {
          busyRef.current = null;
          setBusyOp(null);
        }
      }
    },
    [toast],
  );

  const saveSettings = useCallback(async () => {
    if (!doc || !draft) return;
    setSaving(true);
    try {
      const next = await adminApi.updateRuntimeSettings(
        doc.version,
        {
          manualAnalysisEnabled: draft.manual,
          scheduledAnalysisEnabled: draft.scheduled,
        },
        {
          radarSortAlgorithm: draft.radarSortAlgorithm,
        },
      );
      setDoc(next);
      setDraft({
        manual: next.toggles.manualAnalysisEnabled ?? false,
        scheduled: next.toggles.scheduledAnalysisEnabled ?? false,
        radarSortAlgorithm: next.algorithms.radarSortAlgorithm,
      });
      invalidateQueryPaths(['/breakouts/current', '/breakouts/events'], { reload: true });
      resetMarketReadPrefixes(['/strength/scan']);
      bumpAlgorithmViewGeneration();
      toast.success(__t('运行设置已保存'), __t('版本 v{version}', { version: next.version }));
    } catch (e) {
      if (e instanceof ApiError && (e.bizCode === 'version_conflict' || e.code === 409)) {
        toast.error(__t('设置已在别处修改，请重新核对'), __t('已重新载入最新版本'));
        void loadOwnerState();
      } else {
        toast.error(__t('保存失败'), errText(e));
      }
    } finally {
      setSaving(false);
    }
  }, [doc, draft, loadOwnerState, toast]);

  const rollback = useCallback(async () => {
    if (!doc) return;
    setSaving(true);
    try {
      const history = await adminApi.runtimeHistory();
      const prev = history.filter((h) => !h.current && h.version < doc.version).sort((a, b) => b.version - a.version)[0];
      if (!prev) {
        toast.info(__t('暂无可恢复的旧设置'));
        return;
      }
      const next = await adminApi.rollbackRuntimeSettings(doc.version, prev.version);
      setDoc(next);
      setDraft({
        manual: next.toggles.manualAnalysisEnabled ?? false,
        scheduled: next.toggles.scheduledAnalysisEnabled ?? false,
        radarSortAlgorithm: next.algorithms.radarSortAlgorithm,
      });
      invalidateQueryPaths(['/breakouts/current', '/breakouts/events'], { reload: true });
      resetMarketReadPrefixes(['/strength/scan']);
      bumpAlgorithmViewGeneration();
      toast.success(__t('已恢复到 v{version}', { version: prev.version }), __t('当前版本 v{version}', { version: next.version }));
    } catch (e) {
      toast.error(__t('恢复失败'), errText(e));
    } finally {
      setSaving(false);
    }
  }, [doc, toast]);

  if (!isOwner) return null;

  const dirty = !!doc && !!draft && (
    draft.manual !== (doc.toggles.manualAnalysisEnabled ?? false)
    || draft.scheduled !== (doc.toggles.scheduledAnalysisEnabled ?? false)
    || draft.radarSortAlgorithm !== doc.algorithms.radarSortAlgorithm
  );

  return (
    /* 无障碍名与可见标题、「更多」菜单项同名 */
    <section aria-label={__t('管理设置')} className="card-surface overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
        <h2 className="flex items-center gap-2.5 text-body-s font-medium text-ink-800">
          <Icon name="shield" size={15} className="text-brand-600" />
          {__t('管理设置')}
        </h2>
        {worker && (
          <span className="hidden items-center gap-1.5 tnum text-micro text-ink-400 sm:flex">
            {/* worker 健康是静态状态，不脉冲 */}
            <Led tone={worker.healthy ? 'ok' : 'danger'} className="size-1.5" />
            {__t('后台任务')} {worker.healthy ? __t('正常') : worker.status}
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-3 px-5 py-4 lg:grid-cols-3">
        <SectionCard title={__t("更新数据")} hint={__t("更新所选数据，不消耗分析额度")}>
          <div className="flex flex-wrap gap-2">
            {REFRESH_OPS.map((o) => (
              <ActionButton key={o.op} label={o.label} busy={busyOp === `r-${o.op}`} onClick={() => void runRefresh(o.op, o.label)} />
            ))}
          </div>
        </SectionCard>

        <SectionCard title={__t("后台任务")} hint={__t("任务在后台执行，30 秒内重复点击不会新建任务")}>
          <div className="flex flex-wrap gap-2">
            {WORKER_ACTIONS.map((a) => (
              <ActionButton key={a.action} label={a.label} busy={busyOp === `w-${a.action}`} onClick={() => void runWorkerAction(a.action, a.label)} />
            ))}
          </div>
          {workerErr ? (
            <p className="mt-3 text-micro text-ink-400">{__t('后台状态不可用 ·')} {workerErr}</p>
          ) : worker && worker.tasks.length > 0 ? (
            <ul className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1">
              {worker.tasks.map((t) => (
                <li key={t.name} className="flex items-center gap-1.5 text-micro text-ink-500 tnum">
                  <Led tone={!t.enabled ? 'muted' : t.healthy ? 'ok' : 'danger'} className="size-1.5" />
                  <span className="truncate">{TASK_CN[t.name] ?? t.name}</span>
                  {t.lastSuccessAt && <span className="ml-auto shrink-0 text-ink-400">{fmtRelative(t.lastSuccessAt)}</span>}
                </li>
              ))}
            </ul>
          ) : null}
        </SectionCard>

        <SectionCard title={__t("运行设置")} hint={doc ? __t('设置版本 {version}', { version: doc.version }) : __t('正在读取运行设置')}>
          {docErr ? (
            <p className="text-micro text-ink-400">{__t('运行设置不可用 ·')} {docErr}</p>
          ) : draft ? (
            <div className="space-y-2">
              <Toggle label={__t("手动分析")} value={draft.manual} onChange={(v) => setDraft({ ...draft, manual: v })} />
              <Toggle label={__t("定时分析")} value={draft.scheduled} onChange={(v) => setDraft({ ...draft, scheduled: v })} />
              <div className="rounded-md border border-line bg-card-warm px-3 py-2">
                <span className="mb-1.5 block text-caption text-ink-700">{__t('雷达默认排序')}</span>
                <Segmented<'production' | 't1_daily_priority'>
                  ariaLabel={__t('雷达默认排序')}
                  scrollable
                  options={[
                    { value: 'production', label: __t('原雷达排序') },
                    { value: 't1_daily_priority', label: __t('日线量价优先（试用）') },
                  ]}
                  value={draft.radarSortAlgorithm}
                  onChange={(radarSortAlgorithm) => setDraft({
                    ...draft,
                    radarSortAlgorithm,
                  })}
                />
              </div>
              <p className="text-micro text-ink-400">
                {__t('雷达默认排序只在页面未指定排序、或选了「跟随默认」时生效。')}
              </p>
              <div className="flex items-center justify-end gap-2 pt-1">
                <button
                  onClick={() => void rollback()}
                  disabled={saving}
                  className="control-button"
                >
                  {__t('恢复上一版')}
                </button>
                <button
                  onClick={() => void saveSettings()}
                  disabled={saving || !dirty}
                  className="btn-primary btn-sm"
                >
                  <TextSwap swapKey={saving ? 'busy' : 'idle'}>{saving ? __t('保存中…') : __t('保存设置')}</TextSwap>
                </button>
              </div>
            </div>
          ) : (
            <p className="text-micro text-ink-400">{__t('正在读取…')}</p>
          )}
        </SectionCard>
      </div>
    </section>
  );
}
