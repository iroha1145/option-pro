/**
 * B2.5 管理面板（owner 专属，移植自旧版 deck 催化剂管理区）
 * 三区：数据刷新（新闻/日历/源健康 → /api/catalysts/refresh）
 *      后台任务（焦点池/强势/突破 → /api/worker/actions/{type} + worker 健康清单）
 *      运行设置（手动分析/定时分析开关 → /api/runtime-settings，乐观锁 + 回滚）
 * 访客不渲染；所有状态如实呈现（冷却/禁用/版本冲突都给出后端原话）。
 */
import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import {
  adminApi,
  type RefreshOperation,
  type RuntimeDoc,
  type WorkerActionType,
  type WorkerHealth,
} from '@/api/modules/admin';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { Led } from './bits';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import { t as __t } from '../../i18n/core.ts';

const REFRESH_OPS: { op: RefreshOperation; label: string }[] = [
  { op: 'news', label: __t('新闻流') },
  { op: 'calendar', label: __t('经济日历') },
  { op: 'source_health', label: __t('源健康') },
];

const WORKER_ACTIONS: { action: WorkerActionType; label: string }[] = [
  { action: 'focus_refresh', label: __t('焦点股票池') },
  { action: 'strength_refresh', label: __t('强势雷达') },
  { action: 'breakout_refresh', label: __t('突破雷达') },
];

const TASK_CN: Record<string, string> = {
  breakout: __t('突破扫描'),
  catalyst_sync: __t('催化剂同步'),
  focus: __t('焦点池'),
  ai_jobs: __t('AI 任务'),
  maintenance: __t('维护'),
  public_home: __t('公共快照'),
  focus_refresh: __t('焦点刷新'),
  strength_refresh: __t('强势刷新'),
  breakout_refresh: __t('突破刷新'),
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
          ? 'cursor-wait border-brand-400 bg-brand-50 text-brand-600'
          : 'border-line bg-card text-ink-600 hover:border-brand-400 hover:text-brand-600',
      )}
    >
      {busy ? <span className="size-3 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" aria-hidden="true" /> : <Icon name="refresh" size={12} />}
      {label}
    </button>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      className="flex w-full items-center justify-between gap-3 rounded-md border border-line bg-card-warm px-3 py-2 text-left transition-colors duration-fast hover:border-brand-400"
    >
      <span className="text-caption text-ink-700">{label}</span>
      <span
        className={cn(
          'relative h-4 w-7 shrink-0 rounded-pill shadow-track transition-colors duration-fast',
          value ? 'bg-brand-600' : 'bg-ink-300',
        )}
        aria-hidden="true"
      >
        <span
          className={cn(
            'absolute top-0.5 size-3 rounded-full bg-white shadow-knob transition-[left] duration-fast',
            value ? 'left-[14px]' : 'left-0.5',
          )}
        />
      </span>
    </button>
  );
}

export default function ManagePanel({ onDataRefreshed }: { onDataRefreshed?: () => void }) {
  const { isOwner } = useAccess();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busyOp, setBusyOp] = useState<string | null>(null);
  const [worker, setWorker] = useState<WorkerHealth | null>(null);
  const [workerErr, setWorkerErr] = useState<string | null>(null);
  const [doc, setDoc] = useState<RuntimeDoc | null>(null);
  const [docErr, setDocErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ manual: boolean; scheduled: boolean } | null>(null);
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
        setDraft({ manual: d.toggles.manualAnalysisEnabled ?? false, scheduled: d.toggles.scheduledAnalysisEnabled ?? false });
      },
      (e) => setDocErr(errText(e)),
    );
  }, []);

  useEffect(() => {
    if (open && isOwner) void loadOwnerState();
  }, [open, isOwner, loadOwnerState]);

  const runRefresh = useCallback(
    async (op: RefreshOperation, label: string) => {
      // 单槽忙碌态：并发触发会互相清掉对方的 busy。全面板互斥（一次一件事）
      let claimed = false;
      setBusyOp((current) => {
        if (current !== null) return current;
        claimed = true;
        return `r-${op}`;
      });
      if (!claimed) return;
      try {
        const t = await adminApi.catalystRefresh(op);
        toast.info(__t('{label}刷新已入队', { label }), t.reason ?? undefined);
        // 轻量轮询（最多 5 次），完成后刷新页面数据
        if (t.requestId) {
          for (let i = 0; i < 5; i += 1) {
            await new Promise((r) => setTimeout(r, 3000));
            try {
              const st = await adminApi.catalystRefreshStatus(t.requestId);
              if (st.status === 'completed') {
                toast.success(__t('{label}刷新完成', { label }));
                onDataRefreshed?.();
                break;
              }
              if (st.status === 'failed') {
                toast.error(__t('{label}刷新失败', { label }), st.reason ?? undefined);
                break;
              }
            } catch {
              break; // 状态端点不可用时不阻塞
            }
          }
        }
      } catch (e) {
        toast.error(__t('{label}刷新未受理', { label }), errText(e));
      } finally {
        setBusyOp((current) => (current === `r-${op}` ? null : current));
      }
    },
    [onDataRefreshed, toast],
  );

  const runWorkerAction = useCallback(
    async (action: WorkerActionType, label: string) => {
      let claimed = false;
      setBusyOp((current) => {
        if (current !== null) return current;
        claimed = true;
        return `w-${action}`;
      });
      if (!claimed) return;
      try {
        const t = await adminApi.workerAction(action);
        if (t.reason === 'cooldown') toast.info(__t('{label}仍在冷却', { label }), __t('稍后自动执行或重试'));
        else if (t.reason === 'already_running') toast.info(__t('{label}正在执行', { label }), __t('复用现有任务'));
        else toast.success(__t('{label}刷新已入队', { label }));
        adminApi.workerStatus().then(setWorker, () => undefined);
      } catch (e) {
        toast.error(__t('{label}未受理', { label }), errText(e));
      } finally {
        setBusyOp((current) => (current === `w-${action}` ? null : current));
      }
    },
    [toast],
  );

  const saveSettings = useCallback(async () => {
    if (!doc || !draft) return;
    setSaving(true);
    try {
      const next = await adminApi.updateRuntimeSettings(doc.version, {
        manualAnalysisEnabled: draft.manual,
        scheduledAnalysisEnabled: draft.scheduled,
      });
      setDoc(next);
      setDraft({ manual: next.toggles.manualAnalysisEnabled ?? false, scheduled: next.toggles.scheduledAnalysisEnabled ?? false });
      toast.success(__t('运行设置已保存'), __t('版本 v{version}', { version: next.version }));
    } catch (e) {
      if (e instanceof ApiError && (e.bizCode === 'version_conflict' || e.code === 409)) {
        toast.error(__t('设置已被其他会话修改'), __t('已重新载入最新版本'));
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
        toast.info(__t('没有可回滚的历史版本'));
        return;
      }
      const next = await adminApi.rollbackRuntimeSettings(doc.version, prev.version);
      setDoc(next);
      setDraft({ manual: next.toggles.manualAnalysisEnabled ?? false, scheduled: next.toggles.scheduledAnalysisEnabled ?? false });
      toast.success(__t('已回滚到 v{version}', { version: prev.version }), __t('当前版本 v{version}', { version: next.version }));
    } catch (e) {
      toast.error(__t('回滚失败'), errText(e));
    } finally {
      setSaving(false);
    }
  }, [doc, toast]);

  if (!isOwner) return null;

  const dirty = !!doc && !!draft && (draft.manual !== (doc.toggles.manualAnalysisEnabled ?? false) || draft.scheduled !== (doc.toggles.scheduledAnalysisEnabled ?? false));

  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <section
      aria-label={__t("催化剂管理面板")}
      className="card-surface mt-6 overflow-hidden"
    >
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left transition-colors duration-fast hover:bg-paper-2/60"
      >
        <span className="flex items-center gap-2.5">
          <Icon name="shield" size={15} className="text-brand-600" />
          <span>
            <span className="eyebrow block">OWNER CONSOLE</span>
            <span className="text-body-s font-medium text-ink-800">{__t('管理面板 · 数据刷新 / 后台任务 / 运行设置')}</span>
          </span>
        </span>
        <span className="flex items-center gap-2.5">
          {worker && (
            <span className="hidden items-center gap-1.5 font-mono text-micro text-ink-400 sm:flex">
              {/* worker 健康是静态状态，不脉冲 */}
              <Led tone={worker.healthy ? 'up' : 'down'} className="size-1.5" />
              worker {worker.healthy ? __t('正常') : worker.status}
            </span>
          )}
          <Icon name="chevron-down" size={15} className={cn('text-ink-400 transition-transform duration-ui', open && 'rotate-180')} />
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="grid grid-cols-1 gap-3 border-t border-line px-5 py-4 lg:grid-cols-3">
              <SectionCard title={__t("数据刷新")} hint={__t("只重拉所选数据，不触发模型任务，不占用 AI 预算")}>
                <div className="flex flex-wrap gap-2">
                  {REFRESH_OPS.map((o) => (
                    <ActionButton key={o.op} label={o.label} busy={busyOp === `r-${o.op}`} onClick={() => void runRefresh(o.op, o.label)} />
                  ))}
                </div>
              </SectionCard>

              <SectionCard title={__t("后台任务")} hint={__t("进入统一后台队列；重复点击复用现有任务（30s 冷却）")}>
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
                      <li key={t.name} className="flex items-center gap-1.5 font-mono text-micro text-ink-500 tnum">
                        <Led tone={!t.enabled ? 'muted' : t.healthy ? 'up' : 'down'} className="size-1.5" />
                        <span className="truncate">{TASK_CN[t.name] ?? t.name}</span>
                        {t.lastSuccessAt && <span className="ml-auto shrink-0 text-ink-300">{fmtRelative(t.lastSuccessAt)}</span>}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </SectionCard>

              <SectionCard title={__t("运行设置")} hint={doc ? __t('版本 v{version} · 乐观锁保护', { version: doc.version }) : __t('正在读取运行设置')}>
                {docErr ? (
                  <p className="text-micro text-ink-400">{__t('运行设置不可用 ·')} {docErr}</p>
                ) : draft ? (
                  <div className="space-y-2">
                    <Toggle label={__t("允许手动分析")} value={draft.manual} onChange={(v) => setDraft({ ...draft, manual: v })} />
                    <Toggle label={__t("定时分析")} value={draft.scheduled} onChange={(v) => setDraft({ ...draft, scheduled: v })} />
                    <div className="flex items-center justify-end gap-2 pt-1">
                      <button
                        onClick={() => void rollback()}
                        disabled={saving}
                        className="rounded-md border border-line bg-card px-2.5 py-1.5 text-caption text-ink-600 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:opacity-50"
                      >
                        {__t('回滚上一版')}
                      </button>
                      <button
                        onClick={() => void saveSettings()}
                        disabled={saving || !dirty}
                        className={cn(
                          'rounded-md px-3 py-1.5 text-caption font-medium text-white shadow-btn-hi transition-[filter] duration-fast',
                          dirty ? 'bg-brand-600 hover:brightness-105' : 'bg-ink-300',
                        )}
                      >
                        {saving ? __t('保存中…') : __t('保存设置')}
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="text-micro text-ink-400">{__t('正在读取…')}</p>
                )}
              </SectionCard>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
