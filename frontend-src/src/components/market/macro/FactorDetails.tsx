/**
 * 因子详情 Accordion — 每个模块一段，展开后按需拉取
 * GET /api/macro/conditions/modules/{module_id}。
 * 桌面表格 / 移动纵向卡片；触发器是原生 <button>，键盘与屏幕阅读器可用。
 */
import { useCallback, useEffect, useState } from 'react';
import { macroApi, type MacroFactor, type MacroModule, type MacroModuleId } from '@/api/modules/macro';
import { ApiError } from '@/api/client';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { SkeletonRows } from '@/components/shared/Skeleton';
import { MACRO_MODULE_HINTS } from '@/lib/scoreHints';
import InfoHint from '@/components/shared/InfoHint';
import { FactorCard, FactorTableRow } from './FactorRow';

interface ModuleState {
  loading: boolean;
  error: string | null;
  factors: MacroFactor[];
}

const EMPTY: ModuleState = { loading: false, error: null, factors: [] };

function FactorTable({ factors }: { factors: MacroFactor[] }) {
  return (
    <div className="hidden md:block">
      <table className="w-full table-fixed border-collapse text-body-s">
        <caption className="sr-only">因子当前值、历史分位与 7 日变化</caption>
        <thead>
          <tr className="text-micro text-ink-400">
            <th scope="col" className="w-[32%] pb-2 text-left font-medium">因子</th>
            <th scope="col" className="w-[18%] pb-2 text-right font-medium">当前值</th>
            <th scope="col" className="w-[12%] pb-2 text-right font-medium">历史分位</th>
            <th scope="col" className="w-[15%] pb-2 text-right font-medium">7日原值变化</th>
            <th scope="col" className="w-[12%] pb-2 text-right font-medium">7日分数变化</th>
            <th scope="col" className="w-[11%] pb-2 text-right font-medium">数据截止</th>
          </tr>
        </thead>
        <tbody>
          {factors.map((factor) => (
            <FactorTableRow key={factor.factorId} factor={factor} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function FactorDetails({ modules }: { modules: MacroModule[] }) {
  const [open, setOpen] = useState<MacroModuleId | null>(null);
  const [states, setStates] = useState<Record<string, ModuleState>>({});

  const load = useCallback(async (moduleId: MacroModuleId) => {
    setStates((previous) => ({
      ...previous,
      [moduleId]: { ...(previous[moduleId] ?? EMPTY), loading: true, error: null },
    }));
    try {
      const detail = await macroApi.module(moduleId);
      setStates((previous) => ({
        ...previous,
        [moduleId]: { loading: false, error: null, factors: detail.factors },
      }));
    } catch (error) {
      setStates((previous) => ({
        ...previous,
        [moduleId]: {
          loading: false,
          error: error instanceof ApiError ? error.message : '因子详情暂不可用',
          factors: previous[moduleId]?.factors ?? [],
        },
      }));
    }
  }, []);

  useEffect(() => {
    if (open && !states[open]) void load(open);
  }, [open, states, load]);

  if (!modules.length) return null;

  return (
    <div className="card-surface divide-y divide-line" aria-label="因子详情">
      {modules.map((module) => {
        const expanded = open === module.moduleId;
        const state = states[module.moduleId] ?? EMPTY;
        const hint = MACRO_MODULE_HINTS[module.moduleId];
        return (
          <div key={module.moduleId}>
            <h3>
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={`macro-factors-${module.moduleId}`}
                onClick={() => setOpen(expanded ? null : module.moduleId)}
                className="flex w-full items-center gap-3 px-4 py-3.5 text-left outline-none transition-colors duration-fast hover:bg-paper-2 focus-visible:bg-paper-2"
              >
                <Icon
                  name="chevron-right"
                  size={14}
                  className={cn(
                    'shrink-0 text-ink-400 transition-transform duration-fast',
                    expanded && 'rotate-90',
                  )}
                />
                <span className="min-w-0 flex-1 truncate text-body-s text-ink-800">
                  {module.nameZh}
                  <span className="ml-2 font-mono text-[10px] uppercase tracking-wider text-ink-400">
                    {module.nameEn}
                  </span>
                </span>
                <span className="shrink-0 font-mono text-data-m text-ink-800 tnum">
                  {typeof module.score === 'number' ? module.score.toFixed(1) : '—'}
                </span>
              </button>
            </h3>
            <div
              id={`macro-factors-${module.moduleId}`}
              hidden={!expanded}
              className="px-4 pb-4"
            >
              {expanded && (
                <>
                  {hint && (
                    <p className="flex items-start gap-1 pb-3 text-micro leading-relaxed text-ink-400">
                      <span>{hint.body}</span>
                      <InfoHint hint={hint} side="bottom" align="start" size={11} />
                    </p>
                  )}
                  {state.loading && state.factors.length === 0 && <SkeletonRows rows={4} />}
                  {state.error && (
                    <p className="py-2 text-body-s text-ink-500">
                      {state.error}
                      <button
                        type="button"
                        onClick={() => void load(module.moduleId)}
                        className="ml-2 text-brand-600 underline underline-offset-2"
                      >
                        重试
                      </button>
                    </p>
                  )}
                  {state.factors.length > 0 && (
                    <>
                      <FactorTable factors={state.factors} />
                      <ul className="md:hidden">
                        {state.factors.map((factor) => (
                          <FactorCard key={factor.factorId} factor={factor} />
                        ))}
                      </ul>
                    </>
                  )}
                  {!state.loading && !state.error && state.factors.length === 0 && (
                    <p className="py-2 text-body-s text-ink-400">该模块暂无因子快照。</p>
                  )}
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
