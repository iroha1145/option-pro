import type { AiBudgetSnapshot } from './types.ts';
import { asRec, pickB, pickN, pickS } from './live.ts';
import { t } from '../i18n/core.ts';

export function normalizeAiBudgetSnapshot(raw: unknown): AiBudgetSnapshot | null {
  const row = asRec(raw);
  const dailyBudgetUsd = pickN(row, 'daily_budget_usd');
  if (dailyBudgetUsd === null || dailyBudgetUsd < 0) return null;
  return {
    dailyBudgetUsd,
    budgetUsedUsd: pickN(row, 'budget_used_usd'),
    budgetRemainingUsd: pickN(row, 'budget_remaining_usd'),
    dollarBudgetAvailable: pickB(row, 'dollar_budget_available'),
    budgetEnforced: pickB(row, 'budget_enforced'),
    budgetBasis: dailyBudgetUsd > 0 || row.budget_basis === 'shared_usd' ? 'shared_usd' : 'tokens',
    budgetResetAt: pickS(row, 'budget_reset_at'),
    accountingStartAt: pickS(row, 'accounting_start_at'),
    dailyTokenLimit: pickN(row, 'daily_token_limit'),
    tokenBudgetUsedTokens: pickN(row, 'token_budget_used_tokens'),
  };
}

/** Missing amounts stay missing; reported estimates never become an actual bill. */
export function sharedAiBudgetText(budget: AiBudgetSnapshot | null | undefined): { summary: string; note: string } | null {
  if (!budget || !Number.isFinite(budget.dailyBudgetUsd) || budget.dailyBudgetUsd <= 0) return null;
  const amount = (value: number | null) => value === null || !Number.isFinite(value) ? '—' : value.toFixed(2);
  if (budget.budgetEnforced === false) {
    return {
      summary: t('共享日预算参考 {limit} 美元 · 估算及预留 {used}', {
        limit: amount(budget.dailyBudgetUsd), used: amount(budget.budgetUsedUsd),
      }),
      note: t('所有模型共用，仅统计费用，超过参考金额仍继续；东京 09:00 重置'),
    };
  }
  return {
    summary: t('共享日预算 {limit} 美元 · 估算及预留 {used} · 剩余 {remaining}', {
      limit: amount(budget.dailyBudgetUsd), used: amount(budget.budgetUsedUsd), remaining: amount(budget.budgetRemainingUsd),
    }),
    note: t('所有模型共用，含未知任务预留；东京 09:00 重置'),
  };
}
