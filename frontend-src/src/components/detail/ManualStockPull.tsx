import { useRef, useState } from 'react';
import { ApiError } from '@/api/client';
import { stocksApi } from '@/api/modules/stocks';
import type { StockPullResource, StockPullResult } from '@/api/types';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { t } from '../../i18n/core.ts';

const RESOURCE_LABELS: Array<{
  key: keyof StockPullResult['resources'];
  label: string;
}> = [
  { key: 'overview', label: t('基础行情') },
  { key: 'dailyChart', label: t('日线') },
  { key: 'signals', label: t('技术信号') },
];

function resourceSummary(resource: StockPullResource): string {
  if (resource.status === 'failed') return t('拉取失败');
  if (resource.status === 'unavailable') return t('暂无数据');
  if (!resource.persisted) return t('已拉取，保存失败');
  if (resource.barCount !== undefined) return t('{n} 根', { n: resource.barCount });
  if (resource.metricCount !== undefined) return t('{n} 项', { n: resource.metricCount });
  return resource.provider || t('已更新');
}

export default function ManualStockPull({
  ticker,
  onPulled,
  compact = false,
  minimal = false,
  className,
}: {
  ticker: string;
  onPulled?: (result: StockPullResult) => void;
  compact?: boolean;
  /** 卡片内嵌 CTA：只渲染按钮与错误行（成功后由 onPulled 整页刷新，无需进度明细） */
  minimal?: boolean;
  className?: string;
}) {
  const latestTickerRef = useRef(ticker);
  latestTickerRef.current = ticker;
  const requestSequenceRef = useRef(0);
  const [state, setState] = useState<{
    ticker: string;
    running: boolean;
    result: StockPullResult | null;
    error: string | null;
  }>({
    ticker,
    running: false,
    result: null,
    error: null,
  });
  const visibleState = state.ticker === ticker
    ? state
    : { ticker, running: false, result: null, error: null };
  const { running, result, error } = visibleState;

  const pull = async () => {
    if (running) return;
    const requestedTicker = ticker;
    const requestSequence = requestSequenceRef.current + 1;
    requestSequenceRef.current = requestSequence;
    setState({
      ticker: requestedTicker,
      running: true,
      result: null,
      error: null,
    });
    try {
      const next = await stocksApi.pull(requestedTicker);
      if (
        requestSequenceRef.current !== requestSequence
        || latestTickerRef.current !== requestedTicker
      ) return;
      setState({
        ticker: requestedTicker,
        running: false,
        result: next,
        error: null,
      });
      onPulled?.(next);
    } catch (cause) {
      if (
        requestSequenceRef.current !== requestSequence
        || latestTickerRef.current !== requestedTicker
      ) return;
      setState({
        ticker: requestedTicker,
        running: false,
        result: null,
        error: cause instanceof ApiError
          ? cause.bizCode === 'owner_login_required'
            ? t('拉取需要 Owner 登录；访客只读已保存的快照')
            : `${cause.message}${cause.retryAfter ? t(' · {n} 秒后可重试', { n: cause.retryAfter }) : ''}`
          : t('拉取失败，请稍后重试'),
      });
    } finally {
      if (
        requestSequenceRef.current === requestSequence
        && latestTickerRef.current === requestedTicker
      ) {
        setState((current) => (
          current.ticker === requestedTicker
            ? { ...current, running: false }
            : current
        ));
      }
    }
  };

  const availableCount = result
    ? RESOURCE_LABELS.filter(({ key }) => result.resources[key].status === 'available').length
    : 0;

  if (minimal) {
    return (
      <div className={cn('space-y-1.5', className)}>
        <button
          type="button"
          onClick={() => void pull()}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-md bg-brand-600 px-3 py-1.5 text-caption font-medium text-white shadow-btn-hi transition-[filter,opacity] duration-fast hover:brightness-105 disabled:cursor-wait disabled:opacity-70"
        >
          {running ? (
            <span className="size-3 animate-spin rounded-full border-2 border-white/35 border-t-white" aria-hidden="true" />
          ) : (
            <Icon name="refresh" size={12} />
          )}
          {running ? t('正在拉取真实数据') : t('拉取并分析')}
        </button>
        {error && (
          <p role="alert" className="text-caption text-down-700">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className={cn(compact ? 'space-y-2' : 'rounded-md border border-brand-100 bg-brand-50/45 p-3', className)}>
      <button
        type="button"
        onClick={() => void pull()}
        disabled={running}
        className={cn(
          'inline-flex min-h-11 w-full items-center justify-center gap-2 whitespace-normal rounded-md bg-brand-600 px-3 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter,opacity] duration-fast hover:brightness-105 disabled:cursor-wait disabled:opacity-70 sm:w-auto',
        )}
      >
        {running ? (
          <span className="size-3.5 animate-spin rounded-full border-2 border-white/35 border-t-white" aria-hidden="true" />
        ) : (
          <Icon name="refresh" size={13} />
        )}
        {running ? t('正在拉取真实数据') : result ? t('重新拉取') : t('拉取真实数据')}
      </button>

      {!running && !result && !error && (
        <p className="text-micro text-ink-500">
          {t('获取该股票的最新价格、日线与技术指标')}
        </p>
      )}

      {running && (
        <div role="status" aria-live="polite">
          <div className="mt-2 h-1.5 overflow-hidden rounded-pill bg-brand-100">
            <span className="block h-full w-full animate-pulse rounded-pill bg-brand-500" />
          </div>
          <p className="mt-1.5 text-micro text-ink-500">{t('正在更新基础行情、日线与技术信号 · 共 3 项')}</p>
        </div>
      )}

      {error && (
        <p role="alert" className="text-caption text-down-700">
          {error}
        </p>
      )}

      {result && !running && (
        <div role="status" aria-live="polite" className="space-y-1.5">
          <p className={cn('text-caption font-medium', result.status === 'completed' ? 'text-up-700' : 'text-warn-600')}>
            {t('已完成')} {availableCount}/3
            {result.persistenceStatus === 'failed' ? t(' · 服务器保存失败，重启后可能失效') : ''}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {RESOURCE_LABELS.map(({ key, label }) => {
              const resource = result.resources[key];
              const ok = resource.status === 'available' && resource.persisted;
              return (
                <span
                  key={key}
                  className={cn(
                    'rounded-xs border px-1.5 py-0.5 text-micro',
                    ok
                      ? 'border-up-600/20 bg-up-50 text-up-700'
                      : 'border-warn-600/25 bg-warn-50 text-warn-600',
                  )}
                >
                  {label} · {resourceSummary(resource)}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
