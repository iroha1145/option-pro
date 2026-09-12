/**
 * 期权链（stock-detail.md T3 · UI 重构版）
 * 已知成交量 → 成交关注 → 单份合约列表，默认现价附近，支持类型及异动筛选。
 * 完整术语、精确行权价、独立报价明细；移动端按同一数据重排。
 * owner：「AI 期权解读」（option_alerts 任务 + 轮询 + 确认费用）；visitor 隐藏
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { isMock } from '@/api/client';
import { optionsApi } from '@/api/modules/options';
import { aiJobsApi } from '@/api/modules/ai-jobs';
import { usePolling } from '@/hooks/usePolling';
import { useRetryCountdown } from '@/hooks/useRetryCountdown';
import { useAccess } from '@/hooks/useAccess';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import { SkeletonRows } from '@/components/shared/Skeleton';
import MenuSelect from '@/components/shared/MenuSelect';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtPrice, fmtRelative } from '@/lib/format';
import { OPTION_SUPPORTED_LIST, optionsSupported } from '@/mocks/fixtures2';
import { isDeclaredUnsupported } from '@/lib/optionCapability';
import { useAiJob } from './useAiJob';
import {
  buildOptionAlertEvidence,
  parseOptionAlertResult,
  type OptionAlertResult,
} from './optionAnalysis';
import ChainBrowser from './options/ChainBrowser.tsx';
import SummaryTiles from './options/SummaryTiles.tsx';
import type { OptionChain } from '@/api/types';
import { t } from '../../i18n/core.ts';

const NEW_YORK_DATE = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

/**
 * 到期天数按纽约交易日的日历差计算（GPT-5.6-Pro 审计 P2-33）。
 * 旧实现把「到期日 + T16:00:00」交给 Date 解析，该串没有时区偏移，会按浏览器
 * 本地时区理解：在东京看同一个到期日会少算一天。这里两端都取纽约日历日再相减。
 */
function dte(expiration: string): number {
  const expiryDay = Date.parse(`${expiration}T00:00:00Z`);
  if (!Number.isFinite(expiryDay)) return 0;
  const todayInNewYork = Date.parse(`${NEW_YORK_DATE.format(new Date())}T00:00:00Z`);
  if (!Number.isFinite(todayInNewYork)) return 0;
  return Math.max(0, Math.round((expiryDay - todayInNewYork) / 86_400_000));
}

/** 缺失数值显「—」，不落回 0。 */
const dash = (value: number | null, render: (n: number) => string): string =>
  value === null ? '—' : render(value);

const DIRECTION_META: Record<
  OptionAlertResult['direction'],
  { label: string; className: string }
> = {
  bullish: { label: t('偏多'), className: 'bg-up-50 text-up-700' },
  bearish: { label: t('偏空'), className: 'bg-down-50 text-down-700' },
  mixed: { label: t('多空混合'), className: 'bg-warn-50 text-warn-600' },
  unknown: { label: t('方向未知'), className: 'bg-paper-2 text-ink-500' },
};

const CONFIDENCE_LABEL: Record<OptionAlertResult['confidence'], string> = {
  high: t('证据一致性高'),
  medium: t('证据一致性中等'),
  low: t('证据一致性低'),
};

/* ---------------- AI 期权解读（owner） ---------------- */
function AiOptionInsight({
  ticker,
  expiration,
  chain,
}: {
  ticker: string;
  expiration: string | null;
  chain: OptionChain | null;
}) {
  const { isOwner, aiEnabled, aiAvailable } = useAccess();
  const { job, error, queryIssue, starting, start, cancel, resume, reset, adopt } = useAiJob();
  const [confirming, setConfirming] = useState(false);
  const [hydrating, setHydrating] = useState(true);
  /* 提交那一刻的到期日与证据数快照：结果脚注只认它。轮询会刷新 chain、
     父层切换会换 expiration——用渲染期的值标注既成结果，会把已付费的
     解读错误归属到另一个到期周。 */
  const [submitted, setSubmitted] = useState<{
    expiration: string;
    evidenceCount: number;
  } | null>(null);
  /* 链必须与当前 (ticker, expiration) 匹配才可用：切到期日后新链在途时，
     usePolling 仍保留上一条链的数据，直接用它建证据会把旧到期日的合约
     提交成新到期日的任务。 */
  const activeChain =
    chain && chain.ticker === ticker && chain.expiration === expiration
      ? chain
      : null;
  const evidence = useMemo(
    () =>
      activeChain && expiration
        ? buildOptionAlertEvidence(activeChain, expiration, dte(expiration))
        : [],
    [activeChain, expiration],
  );
  const result =
    job?.status === 'succeeded' ? parseOptionAlertResult(job.result) : null;

  useEffect(() => {
    if (!isOwner || !expiration) {
      setHydrating(false);
      return;
    }
    let dead = false;
    setHydrating(true);
    void aiJobsApi
      .getLatestOptionAlerts(ticker, expiration)
      .then((latest) => {
        if (!dead && latest) {
          setSubmitted({ expiration, evidenceCount: 0 });
          adopt(latest);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (!dead) setHydrating(false);
      });
    return () => {
      dead = true;
    };
  }, [adopt, expiration, isOwner, ticker]);

  if (!isOwner) return null;

  const running =
    job &&
    (job.status === 'queued' ||
      job.status === 'in_progress' ||
      job.status === 'running');
  const hasEvidence = Boolean(activeChain && expiration && evidence.length > 0);
  const submitInsight = (force: boolean) => {
    if (!activeChain || !expiration || evidence.length === 0 || !aiAvailable) return;
    setSubmitted({ expiration, evidenceCount: evidence.length });
    void start(() =>
      aiJobsApi.createOptionAlerts({
        tickers: [ticker],
        alerts: evidence,
        force,
        ...(activeChain.spot !== null
          ? { underlyingPrice: activeChain.spot }
          : {}),
        expiration,
      }),
    );
  };
  const canStart = aiAvailable && !job && !starting && !confirming && !hydrating;
  return (
    <div className="mt-4 rounded-md border border-ai-600/25 bg-ai-50 p-3.5">
      <div className="flex items-center justify-between gap-3">
        <p className="flex items-center gap-1.5 text-body-s font-medium text-ink-800">
          <Icon name="spark-ai" size={15} className="text-ai-600" />
          {t('AI 期权解读')}
        </p>
        {canStart && (
          <button
            onClick={() => setConfirming(true)}
            disabled={!hasEvidence}
            title={
              hasEvidence
                ? t('使用当前期权链的 {n} 条异动记录', { n: evidence.length })
                : t('当前期权链暂无符合条件的异动')
            }
            className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-on-accent shadow-btn-hi transition-[filter] duration-fast hover:brightness-105 disabled:cursor-not-allowed disabled:bg-ink-300"
          >
            {hasEvidence ? t('生成解读') : t('暂无异动')}
          </button>
        )}
      </div>
      {!aiAvailable && !job && !hydrating && (
        <p className="mt-2.5 text-caption text-ink-500">
          {aiEnabled ? t('AI 分析暂不可用') : t('AI 分析未启用')}
          {aiEnabled
            ? t('暂时无法生成新分析，已有分析仍可查看。')
            : t('分析生成已关闭，已有分析仍可查看。')}
        </p>
      )}

      {!job && !confirming && chain && evidence.length === 0 && (
        <p className="mt-2.5 text-caption text-ink-500">
          {t('当前到期日没有符合条件的异动，可切换到期日查看。')}
        </p>
      )}

      {!job && (starting || hydrating) && (
        <p className="mt-2.5 text-caption text-ink-500">
          {hydrating ? t('正在读取已有分析…') : t('正在准备解读…')}
        </p>
      )}
      {!job && confirming && (
        <div className="mt-2.5">
          <p className="text-caption text-ink-600">
            {t('分析 {ticker} 当前到期日的 {n} 条异动，消耗 1 次分析额度。', { ticker, n: evidence.length })}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              onClick={() => {
                setConfirming(false);
                submitInsight(false);
              }}
              disabled={starting}
              className="rounded-md bg-ai-600 px-3 py-1.5 text-caption font-medium text-on-accent shadow-btn-hi hover:brightness-105 disabled:cursor-wait disabled:opacity-60"
            >
              {t('生成解读')}
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="rounded-md border border-line-strong px-3 py-1.5 text-caption text-ink-600 shadow-btn hover:bg-paper-2"
            >
              {t('取消')}
            </button>
          </div>
        </div>
      )}

      {running && (
        <div className="mt-2.5">
          <div className="flex items-center justify-between text-caption text-ink-500">
            <span className="flex items-center gap-1.5">
              <span className="size-1.5 animate-led-pulse rounded-full bg-ai-600" />
              {queryIssue === 'paused' || queryIssue === 'blocked' ? t('任务状态待确认') : job.status === 'queued'
                ? t('排队中…')
                : job.progress === null
                  ? t('模型分析中…')
                  : t('解读中 {pct}%', { pct: Math.round(job.progress) })}
            </span>
            <button onClick={() => void cancel()} className="text-ink-400 hover:text-ink-600">{t('取消任务')}</button>
          </div>
          {job.progress !== null && (
            <div className="mt-1.5 h-1 overflow-hidden rounded-pill bg-line">
              <div
                className="h-full rounded-pill bg-ai-600 transition-[width] duration-ui"
                style={{ width: `${job.progress}%` }}
              />
            </div>
          )}
        </div>
      )}

      {error && <p role="status" className="mt-2.5 text-caption text-down-700">
        {error}
        {queryIssue === 'retrying' && <span className="ml-2">{t('正在重新查询原任务')}</span>}
        {(queryIssue === 'paused' || queryIssue === 'blocked') && <button onClick={resume} className="ml-2 font-medium text-ai-600">{t('继续查询原任务')}</button>}
      </p>}

      {job?.status === 'succeeded' && result && (
        <div className="mt-3 border-t border-ai-600/20 pt-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span
              className={cn(
                'rounded-xs px-1.5 py-0.5 text-micro font-medium',
                DIRECTION_META[result.direction].className,
              )}
            >
              {DIRECTION_META[result.direction].label}
            </span>
            <span className="rounded-xs bg-card px-1.5 py-0.5 text-micro text-ink-500">
              {CONFIDENCE_LABEL[result.confidence]}
            </span>
            {result.direction_status === 'unavailable_without_trade_side' && (
              <span className="text-micro text-ink-400">
                {t('无法判断买方还是卖方主动成交')}
              </span>
            )}
          </div>
          <p className="mt-2.5 text-body-s font-medium leading-relaxed text-ink-800">
            {result.summary}
          </p>
          <p className="mt-2 text-body-s leading-relaxed text-ink-600">
            {result.analysis}
          </p>
          {result.key_strikes.length > 0 && (
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
              <span className="text-micro text-ink-400">{t('关键行权价')}</span>
              {result.key_strikes.map((strike) => (
                <span
                  key={strike}
                  className="rounded-xs border border-ai-600/20 bg-card px-1.5 py-0.5 font-mono text-micro text-ink-600"
                >
                  {strike}
                </span>
              ))}
            </div>
          )}
          <p className="mt-2.5 border-t border-ai-600/15 pt-2 text-caption text-warn-600">
            {t('风险说明：')}{result.risk_note}
          </p>
          <p className="mt-2 text-micro text-ink-400">
            {/* 只认提交快照：渲染期的 expiration/evidence 会随切换与轮询漂移 */}
            {t('到期日 {date}', { date: submitted?.expiration ?? expiration ?? '—' })}
            {submitted?.evidenceCount
              ? t(' · 异动记录 {n} 条', { n: submitted.evidenceCount })
              : ''}
          </p>
          {aiAvailable && (
            <button
              onClick={() => {
                reset();
                submitInsight(true);
              }}
              className="mt-2 text-caption font-medium text-ai-600 hover:text-ai-600/80"
            >
              {t('重新生成')}
            </button>
          )}
        </div>
      )}

      {job?.status === 'succeeded' && !result && (
        <div className="mt-3 border-t border-ai-600/20 pt-3">
          <p className="text-caption text-down-700">
            {t('分析已完成，但没有返回可展示的结果。')}
          </p>
          {aiAvailable && (
            <button
              onClick={() => {
                reset();
                submitInsight(true);
              }}
              className="mt-2 text-caption font-medium text-ai-600"
            >
              {t('重新生成')}
            </button>
          )}
        </div>
      )}
      {(job?.status === 'failed' || job?.status === 'cancelled') && (
        <p className="mt-2.5 text-caption text-ink-500">
          {t('任务')}{job.status === 'failed' ? t('失败') : t('已取消')} ·{' '}
          {aiAvailable && (
            <button
              onClick={() => {
                reset();
                submitInsight(true);
              }}
              className="font-medium text-ai-600"
            >
              {t('重试')}
            </button>
          )}
          {job.status === 'failed' && job.errorDetail && (
            /* owner 排障线索（非 owner 后端置空不渲染）：命中的校验规则/字段 */
            <span className="mt-1 block break-all font-mono text-micro text-ink-400">
              {job.errorDetail}
            </span>
          )}
        </p>
      )}
    </div>
  );
}

function UnsupportedIndexOptions() {
  return (
    <EmptyState
      icon="doc-quote"
      title={t('当前数据源未提供该指数的期权数据')}
      className="py-8"
    />
  );
}

/* ---------------- 链主体 ---------------- */
export default function OptionsPanel({ ticker }: { ticker: string }) {
  // 请求抑制发生在挂载层：声明不支持时不挂载带 usePolling 的子树。
  if (isDeclaredUnsupported(ticker)) {
    return <UnsupportedIndexOptions />;
  }
  const supported = !isMock || optionsSupported(ticker);
  if (!supported) {
    return (
      <EmptyState
        icon="doc-quote"
        title={t('该标的暂无期权数据')}
        description={t('支持标的：{list}', { list: OPTION_SUPPORTED_LIST })}
        className="py-8"
      />
    );
  }
  return <LiveOptionsPanel ticker={ticker} />;
}

function LiveOptionsPanel({ ticker }: { ticker: string }) {
  const [expiration, setExpiration] = useState<string | null>(null);
  const forceExpirationsRef = useRef(false);
  const {
    data: expirationRead,
    loading: expLoading,
    error: expError,
    refreshing: expRefreshing,
    refresh: refreshExpirations,
  } = usePolling(
    () => {
      const force = forceExpirationsRef.current;
      forceExpirationsRef.current = false;
      return optionsApi.expirations(ticker, { force });
    },
    null,
    [ticker],
  );
  const expirationDates = expirationRead?.expirations ?? [];
  const exp =
    expiration && expirationDates.includes(expiration)
      ? expiration
      : expirationDates[0] ?? null;
  const {
    data: chain,
    loading: chainLoading,
    error: chainError,
    refreshing: chainRefreshing,
    refresh: refreshChain,
  } = usePolling(
    () => (exp ? optionsApi.chain(ticker, exp) : Promise.resolve(null)),
    null,
    [ticker, exp],
  );
  /* 到期日列表失败才整块空态；单个到期周的链失败只替换表格区域——
     否则一个坏到期日会连带换掉到期日选择，用户失去唯一的逃生控件，
     若坏的恰是 expirations[0]，整个期权页持续不可用。 */
  const providerError = expError ?? chainError;
  const retrySeconds = useRetryCountdown(
    providerError,
    providerError?.retryAfter,
  );
  /* 链必须匹配当前 (ticker, exp) 才能上屏：轮询在切换后仍保留上一条链 */
  const shownChain =
    chain && chain.ticker === ticker && chain.expiration === exp ? chain : null;

  if (expirationRead?.optionsStatus === 'unsupported_by_provider') {
    return <UnsupportedIndexOptions />;
  }

  if (expLoading) return <SkeletonRows rows={6} />;
  if (expError) {
    const loginExpired = expError.code === 401;
    const rateLimited = expError.code === 429;
    const preparing = expError.bizCode === 'public_option_snapshot_pending';
    const retrying = expRefreshing || chainRefreshing;
    return (
      <EmptyState
        icon="doc-quote"
        title={
          loginExpired
            ? t('登录状态已失效')
            : rateLimited
              ? t('期权链请求较频繁')
              : preparing ? t('期权数据准备中') : t('期权数据暂不可用')
        }
        description={
          loginExpired
            ? t('请重新登录后查看期权数据')
            : `${preparing ? t('期权数据正在更新，请稍后重试') : t('期权数据暂时获取不到')}${
                retrySeconds > 0 ? t(' · {n} 秒后可重试', { n: retrySeconds }) : ''
              }`
        }
        action={
          loginExpired ? null : (
            <button
              type="button"
              onClick={() => {
                if (expError.code === 400) {
                  setExpiration(null);
                  forceExpirationsRef.current = true;
                  refreshExpirations();
                  return;
                }
                refreshExpirations();
                if (exp) refreshChain();
              }}
              disabled={retrySeconds > 0 || retrying}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-on-accent transition-[filter,opacity] hover:brightness-105 disabled:cursor-wait disabled:opacity-60"
            >
              <Icon name="refresh" size={14} />
              {retrying ? t('正在重试') : retrySeconds > 0 ? t('{n} 秒后重试', { n: retrySeconds }) : t('重试')}
            </button>
          )
        }
        variant="error"
        className="py-8"
      />
    );
  }
  const expList = Array.from(new Set(expirationDates));
  if (expList.length === 0) {
    const canRetry = expirationRead?.retryable !== false;
    return (
      <EmptyState
        icon="doc-quote"
        title={t("暂无到期日数据")}
        description={t("暂未获取到该标的的期权到期日")}
        action={
          canRetry ? (
            <button
              type="button"
              onClick={() => {
                forceExpirationsRef.current = true;
                refreshExpirations({ force: true });
              }}
              disabled={expRefreshing}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-on-accent shadow-btn-hi transition-[filter,opacity] hover:brightness-105"
            >
              <Icon name="refresh" size={14} />
              {expRefreshing ? t('正在重试') : t('重新获取')}
            </button>
          ) : null
        }
        className="py-8"
      />
    );
  }

  return (
    <div>
      {/* 到期日下拉 + DTE */}
      <div className="flex flex-wrap items-center gap-3">
        <MenuSelect
          ariaLabel={t('选择到期日')}
          value={exp ?? expList[0]}
          onChange={setExpiration}
          options={expList.map((x) => ({ value: x, label: t('{date} · {days} 天后到期', { date: x, days: dte(x) }) }))}
          triggerClassName="h-9 border-line-strong pl-3 text-ink-800"
        />
        {shownChain && (
          <p className="text-micro text-ink-400">
            {t('标的价')}{' '}
            <span className="font-mono text-ink-600 tnum">
              {dash(shownChain.spot, (n) => fmtPrice(n))}
            </span>
            {shownChain.spot === null && (
              <span className="ml-1.5 text-ink-400">{t('· 标的现价不可用')}</span>
            )}
          </p>
        )}
      </div>
      {shownChain && (
        <SourceNote
          className="mt-2"
          text={`${t('延迟行情')}${
            shownChain.asOf ? t(' · 数据获取于 {time}', { time: fmtRelative(shownChain.asOf) }) : ''
          }${shownChain.stale ? t(' · 暂未刷新，显示最近一次结果') : ''}`}
        />
      )}

      {shownChain && <SummaryTiles chain={shownChain} />}
      {chainError && shownChain && (
        <p role="status" className="mt-3 rounded-md border border-warn-600/20 bg-warn-50 p-3 text-caption text-warn-700">
          {t('更新失败，显示上次期权数据。')}
        </p>
      )}
      {chainError && !shownChain ? (
          <div className="flex flex-col items-center gap-2.5 px-4 py-10 text-center">
            <p className="text-body-s font-medium text-ink-700">{chainError.bizCode === 'public_option_snapshot_pending' ? t('期权数据准备中') : t('该到期日的期权链暂不可用')}</p>
            <p className="text-caption text-ink-400">
              {chainError.bizCode === 'public_option_snapshot_pending' ? t('期权数据正在更新，请稍后重试') : t('可切换到其他到期日查看')}
              {retrySeconds > 0 ? t(' · {n} 秒后可重试', { n: retrySeconds }) : ''}
            </p>
            <button
              type="button"
              onClick={() => refreshChain()}
              disabled={retrySeconds > 0 || chainRefreshing}
              className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-3 py-1.5 text-caption text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600 disabled:cursor-wait disabled:opacity-60"
            >
              <Icon name="refresh" size={13} />
              {chainRefreshing ? t('正在重试') : t('重试该到期日')}
            </button>
          </div>
      ) : chainLoading || !shownChain || !exp ? (
        <div className="mt-4"><SkeletonRows rows={6} /></div>
      ) : (
        <ChainBrowser key={`chain:${ticker}-${exp}`} chain={shownChain} />
      )}

      {/* key 强制重挂：切标的/到期日后旧 job（含已生成的付费解读）不得残留，
          否则正文是 8/21 的解读、脚注却标着 9/18，且「生成解读」入口被 job
          占位不再渲染。与同级 ChainBrowser 的 key 使用不同前缀，避免重名
          导致 React 在切换时遗留旧链；返回 null 的组件也占用其 key。 */}
      <AiOptionInsight key={`insight:${ticker}-${exp ?? 'none'}`} ticker={ticker} expiration={exp} chain={shownChain} />
    </div>
  );
}
