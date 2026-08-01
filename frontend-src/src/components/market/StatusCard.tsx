/**
 * B2 市场状态卡
 * SessionLED+时段 · NY 时钟（秒级）· 下一开/收盘倒计时 · 时段说明
 * 字段对齐 contract market/status：market/phase/holiday/next_open/next_close，缺字段显示「—」
 */
import type { ApiError } from '@/api/client';
import type { MarketSession } from '@/api/types';
import type { MarketStatusDetail } from './api';
import { useNow } from '@/hooks/useNow';
import { fmtCountdown, fmtNyTime } from '@/lib/format';
import SessionLED from '@/components/shared/SessionLED';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonCard } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { t } from '../../i18n/core.ts';

const MARKET_TO_SESSION: Record<MarketStatusDetail['market'], MarketSession> = {
  open: 'regular',
  premarket: 'premarket',
  postmarket: 'afterhours',
  closed: 'closed',
};

const MARKET_LABEL: Record<MarketStatusDetail['market'], string> = {
  open: t('盘中'),
  premarket: t('盘前'),
  postmarket: t('盘后'),
  closed: t('休市'),
};

const PHASE_LABEL: Record<string, string> = {
  regular: t('常规交易时段 · 9:30–16:00 ET'),
  'pre-market': t('盘前交易 · 流动性较薄，报价点差偏大'),
  premarket: t('盘前交易 · 流动性较薄，报价点差偏大'),
  'after-hours': t('盘后交易 · 留意财报与公告驱动'),
  postmarket: t('盘后交易 · 留意财报与公告驱动'),
  overnight: t('隔夜休市 · 等待下一交易时段'),
  weekend: t('周末休市 · 等待下一个交易日'),
  holiday: t('节假日休市'),
};

function CountdownRow({ label, at, now }: { label: string; at: string | null; now: number }) {
  return (
    <div className="flex items-center justify-between border-t border-line py-2.5">
      <span className="text-caption text-ink-500">{label}</span>
      <span className="font-mono text-data-m text-brand-600 tnum" suppressHydrationWarning>
        {at ? fmtCountdown(at, now) : '—'}
      </span>
    </div>
  );
}

export default function StatusCard({
  data,
  loading,
  error,
  onRetry,
  refreshing,
}: {
  data: MarketStatusDetail | null;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  refreshing: boolean;
}) {
  const now = useNow(1000);

  if (loading) return <SkeletonCard className="h-full" />;
  if (error) {
    return (
      <div className="card-surface h-full">
        <EmptyState
          variant="error"
          icon="doc-quote"
          title={error.code === 503 ? t('数据暂不可用') : t('加载失败')}
          description={error.code === 503 ? t('暂无市场状态数据') : error.message}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105 disabled:opacity-60"
            >
              {refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
              {t('重试')}
            </button>
          }
        />
      </div>
    );
  }
  if (!data) return null;

  const session = MARKET_TO_SESSION[data.market] ?? 'closed';
  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <section
      className="card-surface flex h-full flex-col p-5"
      aria-label={t("市场状态")}
    >
      <div className="flex items-start justify-between">
        <p className="eyebrow">{t('市场状态 · MARKET STATUS')}</p>
        <Icon name="clock-ny" size={18} className="text-ink-400" />
      </div>
      <div className="mt-4 flex items-center gap-2.5">
        <SessionLED session={session} showLabel={false} />
        <span className="font-display text-[20px] leading-[26px] text-ink-900">{MARKET_LABEL[data.market]}</span>
      </div>
      <div className="mt-3">
        <p className="font-mono text-data-xl text-ink-900 tnum" suppressHydrationWarning>
          {fmtNyTime(new Date(now))}
        </p>
        <p className="mt-1 text-micro text-ink-400">{t('纽约时间（ET）· 每秒更新')}</p>
      </div>
      <div className="mt-4">
        <CountdownRow label={t("距下一开盘")} at={data.next_open} now={now} />
        <CountdownRow label={t("距下一收盘")} at={data.next_close} now={now} />
        <div className="flex items-center justify-between border-y border-line py-2.5">
          <span className="text-caption text-ink-500">{t('节假日')}</span>
          <span className="font-mono text-data-m text-ink-600 tnum">{data.holiday ?? '—'}</span>
        </div>
      </div>
      <p className="mt-3 text-caption text-ink-500">
        {data.phase ? (PHASE_LABEL[data.phase] ?? data.phase) : '—'}
      </p>
    </section>
  );
}
