/**
 * 七模块分数与因子详情 —— 七个模块只在这里出现一次。
 *
 * 每个模块是一行摘要：名称、历史分位条与分数、7 日分数变化、有效因子数、数据截止；
 * 有效因子不足、不出分的模块在行下直接说明门槛（不按 50 补齐）。点开一行再按需拉取
 * GET /api/macro/conditions/modules/{module_id}：桌面表格 / 移动纵向卡片。
 * 此前模块卡网格与这份列表各列一遍同样的七个模块和分数，读者要对照两处。
 * 触发器是原生 <button>，键盘与屏幕阅读器可用。
 */
import { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { macroApi, type MacroFactor, type MacroModule, type MacroModuleId } from '@/api/modules/macro';
import { ApiError } from '@/api/client';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { EASE_PAPER, GROW_X } from '@/lib/motion';
import { strengthBarClass } from '@/lib/strengthColor';
import { SkeletonRows } from '@/components/shared/Skeleton';
import ChangeBadge from '@/components/shared/ChangeBadge';
import { MACRO_MODULE_HINTS } from '@/lib/scoreHints';
import InfoHint from '@/components/shared/InfoHint';
import { FactorCard, FactorTableRow } from './FactorRow';
import { t } from '../../../i18n/core.ts';

interface ModuleState {
  loading: boolean;
  error: string | null;
  factors: MacroFactor[];
}

const EMPTY: ModuleState = { loading: false, error: null, factors: [] };

/* 摘要行的列：展开箭头 · 名称 · 分数 · 分位条 · 7 日变化 · 有效因子（与面板不同的截止日）。
   手机上收成三行：名称与分数 / 分位条 / 其余读数。 */
const ROW_GRID =
  'grid grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 md:grid-cols-[14px_minmax(8rem,11rem)_3.75rem_minmax(6rem,1fr)_5.5rem_7.5rem] md:gap-x-4';

/* 展开内容、门槛说明与名称列对齐：px-4 + 箭头 14px + 列距 */
const DETAIL_INSET = 'pl-[42px] md:pl-[46px]';

function hasScore(module: MacroModule): module is MacroModule & { score: number } {
  return typeof module.score === 'number' && Number.isFinite(module.score);
}

function FactorTable({ factors }: { factors: MacroFactor[] }) {
  return (
    <div className="hidden md:block">
      <table className="w-full table-fixed border-collapse text-body-s">
        <caption className="sr-only">{t('因子当前值、历史分位与 7 日变化')}</caption>
        <thead>
          <tr className="text-micro text-ink-400">
            <th scope="col" className="w-[32%] pb-2 text-left font-medium">{t('因子')}</th>
            <th scope="col" className="w-[18%] pb-2 text-right font-medium">{t('当前值')}</th>
            <th scope="col" className="w-[12%] pb-2 text-right font-medium">{t('历史分位')}</th>
            <th scope="col" className="w-[15%] pb-2 text-right font-medium">{t('7 日原值变化')}</th>
            <th scope="col" className="w-[12%] pb-2 text-right font-medium">{t('7 日分数变化')}</th>
            <th scope="col" className="w-[11%] pb-2 text-right font-medium">{t('数据截止')}</th>
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

function ModuleSummary({
  module,
  index,
  expanded,
  panelDataThrough,
}: {
  module: MacroModule;
  index: number;
  expanded: boolean;
  panelDataThrough: string | null;
}) {
  const scored = hasScore(module);
  /* 七个模块通常与面板同一天截止，标题行已经写着「数据截止」；只有落后或
     超前于面板的模块才单独写出自己的日期，免得七行重复同一个日期。 */
  const ownDate =
    module.dataThrough === null || module.dataThrough !== panelDataThrough
      ? module.dataThrough ?? '—'
      : null;
  return (
    <>
      <Icon
        name="chevron-right"
        size={14}
        className={cn(
          'col-start-1 row-start-1 shrink-0 text-ink-400 transition-transform duration-fast',
          expanded && 'rotate-90',
        )}
      />
      <span className="col-start-2 row-start-1 min-w-0 truncate text-body-s font-medium text-ink-800">{t(module.nameZh)}</span>
      <span className="col-start-3 row-start-1 text-right text-data-l font-medium text-ink-900 tnum">
        {scored ? module.score.toFixed(1) : '—'}
      </span>
      {/* 分位条：中线 50 是 5 年历史的中位，条长过线即高于历史中位 */}
      <span
        className="relative col-span-2 col-start-2 row-start-2 block h-1 md:col-span-1 md:col-start-4 md:row-start-1"
        role="presentation"
      >
        <span className="absolute inset-0 overflow-hidden rounded-pill bg-line">
          {scored && (
            <motion.span
              className={cn('block h-full origin-left rounded-pill', strengthBarClass(module.score))}
              style={{ width: `${Math.max(2, Math.min(100, module.score))}%` }}
              variants={GROW_X}
              transition={{ duration: 0.7, ease: EASE_PAPER, delay: index * 0.04 }}
            />
          )}
        </span>
        <span className="absolute -inset-y-1 left-1/2 w-px bg-line-strong" />
      </span>
      <span className="col-span-2 col-start-2 row-start-3 flex flex-wrap items-center gap-x-3 gap-y-1 md:contents">
        <span className="md:col-start-5 md:row-start-1">
          <ChangeBadge value={module.scoreChange7d} size="sm" format="points" />
        </span>
        <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-micro text-ink-500 md:col-start-6 md:row-start-1 md:justify-end md:text-right">
          <span className="tnum">
            {module.validFactorCount === null
              ? t('有效因子 —')
              : t('有效因子 {valid}/{total}', { valid: module.validFactorCount, total: module.totalFactorCount ?? '—' })}
          </span>
          {ownDate !== null && (
            <span className="font-mono text-ink-500 tnum">
              {t('截止')} {ownDate}
            </span>
          )}
        </span>
      </span>
    </>
  );
}

/**
 * @param snapshotKey 当前宏观快照的标识（dataThrough / asOf）。
 *
 * 因子详情此前只要成功写进本地 states 就永不失效（审计 P2-28）：父级宏观快照
 * 更新后，展开的详情仍是上一份快照算出来的。缓存键必须包含快照标识。
 */
export default function FactorDetails({
  modules,
  snapshotKey = '',
  dataThrough = null,
}: {
  modules: MacroModule[];
  snapshotKey?: string;
  /** 面板的数据截止日；与之相同的模块不再逐行重复日期。 */
  dataThrough?: string | null;
}) {
  const [open, setOpen] = useState<MacroModuleId | null>(null);
  const [states, setStates] = useState<Record<string, ModuleState>>({});

  /* 快照一变就丢弃全部详情缓存，让下一次展开重新取。 */
  const [cachedSnapshot, setCachedSnapshot] = useState(snapshotKey);
  if (cachedSnapshot !== snapshotKey) {
    setCachedSnapshot(snapshotKey);
    setStates({});
  }

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
          error: error instanceof ApiError ? error.message : t('因子详情暂不可用'),
          factors: previous[moduleId]?.factors ?? [],
        },
      }));
    }
  }, []);

  useEffect(() => {
    if (open && !states[open]) void load(open);
  }, [open, states, load]);

  if (!modules.length) {
    return (
      <p className="card-surface p-5 text-body-s text-ink-500">
        {t('暂无模块分数。数据接入后这里会显示七个模块。')}
      </p>
    );
  }

  return (
    <motion.div
      className="card-surface divide-y divide-line"
      aria-label={t("因子详情")}
      initial="hidden"
      whileInView="shown"
      viewport={{ once: true, amount: 0.2 }}
    >
      {modules.map((module, index) => {
        const expanded = open === module.moduleId;
        const state = states[module.moduleId] ?? EMPTY;
        const hint = MACRO_MODULE_HINTS[module.moduleId];
        return (
          <article
            key={module.moduleId}
            aria-label={t('{name} 模块', { name: t(module.nameZh) })}
          >
            <h3>
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={`macro-factors-${module.moduleId}`}
                onClick={() => setOpen(expanded ? null : module.moduleId)}
                className={cn(
                  'w-full px-4 py-3 text-left outline-none transition-colors duration-fast hover:bg-paper-2 focus-visible:bg-paper-2',
                  ROW_GRID,
                )}
              >
                <ModuleSummary module={module} index={index} expanded={expanded} panelDataThrough={dataThrough} />
              </button>
            </h3>
            {!hasScore(module) && (
              <p className={cn('-mt-1 pb-3 pr-4 text-micro leading-relaxed text-ink-500', DETAIL_INSET)}>
                {t('有效因子不足')} {module.minimumValidFactors ?? ''} {t('个门槛，本模块不出分（不按 50 补齐）。')}
              </p>
            )}
            <div
              id={`macro-factors-${module.moduleId}`}
              hidden={!expanded}
              className={cn('pb-4 pr-4', DETAIL_INSET)}
            >
              {expanded && (
                <>
                  {hint && (
                    <p className="flex items-start gap-1 pb-3 text-micro leading-relaxed text-ink-500">
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
                        className="control-button ml-2"
                      >
                        {t('重试')}
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
                    <p className="py-2 text-body-s text-ink-500">{t('该类指标暂无数据。')}</p>
                  )}
                </>
              )}
            </div>
          </article>
        );
      })}
    </motion.div>
  );
}
