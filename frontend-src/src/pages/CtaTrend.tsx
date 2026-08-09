/**
 * §CTA CTA 趋势资金（/cta，从大盘页 B4.5 剥离为独立页面）
 * B1 跨标的总览卡带 · B2 单标的深读（仓位读数/历史/情景曲线/触发阶梯/模型分解）
 * 轮询：ctaTrend 300s（worker 快照只读，日频模型）· regime 300s（仅并排联动解释）
 *
 * 语义纪律：代理估算，不是任何机构的真实仓位披露，不输出美元流量；CTA 读数
 * 不混入 market_regime / Strength 评分，只做并排联动解释。
 */
import { useMemo, useRef, useState } from 'react';
import { marketApi } from '@/api/modules/market';
import { marketPulseApi } from '@/components/market/api';
import { regimeMean } from '@/components/market/RegimePanel';
import { usePolling } from '@/hooks/usePolling';
import { fmtTimeHHMMSS } from '@/lib/format';
import PageHeader from '@/components/shared/PageHeader';
import StaleStrip from '@/components/shared/StaleStrip';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import Icon from '@/components/icons';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import CtaOverviewStrip from '@/components/cta/CtaOverviewStrip';
import CtaDeepDive from '@/components/cta/CtaDeepDive';
import { t } from '../i18n/core.ts';

export default function CtaTrend() {
  /* 300s：CTA 趋势资金（worker 快照只读）+ 形态六维（仅联动说明） */
  const ctaQ = usePolling(() => marketApi.ctaTrend(), 300_000);
  const regimeQ = usePolling(() => marketPulseApi.regime(), 300_000);
  /* 趋势偏向只依据六维 market_regime；接口没有全市场均分时不做近似替代。 */
  const mean = useMemo(() => (regimeQ.data ? regimeMean(regimeQ.data) : null), [regimeQ.data]);

  const [instrument, setInstrument] = useState('sp500');
  const rows = useMemo(() => ctaQ.data?.instruments ?? [], [ctaQ.data]);
  const row = rows.find((item) => item.instrument === instrument) ?? rows[0] ?? null;
  const snapshotMissing = !ctaQ.data && ctaQ.error?.bizCode === 'public_snapshot_unavailable';

  const mainRef = useRef<HTMLDivElement>(null);
  const selectInstrument = (value: string, scroll = false) => {
    setInstrument(value);
    if (scroll) {
      const reduced = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      mainRef.current?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
    }
  };

  return (
    <div>
      <PageHeader
        section="CTA"
        eyebrow="CTA TREND FLOW · PROXY"
        title={t('CTA 趋势资金')}
        description={t('趋势跟踪模型群的机械仓位代理估算——不是任何机构的真实仓位披露，也不输出美元流量。')}
        meta={
          <>
            {ctaQ.data?.method_version && (
              <span className="rounded-pill border border-line bg-paper-2 px-2 py-0.5 font-mono text-micro text-ink-500 tnum">
                {ctaQ.data.method_version}
              </span>
            )}
            {/* 页头时间 = worker 快照落盘时刻（缺失时退 generated_at）。
                此前显示的是浏览器请求完成时刻，会把旧快照误标成「刚更新」
                （GPT-5.6-Pro 审计问题 3）。 */}
            {(ctaQ.data?.snapshot_saved_at ?? ctaQ.data?.generated_at) && (
              <span className="font-mono text-caption text-ink-400 tnum">
                {t('快照 {time}', {
                  time: fmtTimeHHMMSS(
                    new Date((ctaQ.data.snapshot_saved_at ?? ctaQ.data.generated_at)!).getTime(),
                  ),
                })}
              </span>
            )}
          </>
        }
      />

      {ctaQ.loading && !ctaQ.data ? (
        <div className="mt-6 space-y-4" aria-hidden="true">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {[0, 1, 2, 3].map((i) => <SkeletonBlock key={i} className="h-36 w-full" />)}
          </div>
          <SkeletonBlock className="h-72 w-full" />
        </div>
      ) : snapshotMissing ? (
        <p className="mt-6 rounded-md border border-line bg-card-warm px-3 py-4 text-caption text-ink-500">
          {t('CTA 估算快照尚未发布：Worker 完成首次计算后自动出现，无需手动操作')}
        </p>
      ) : ctaQ.error && !ctaQ.data ? (
        <div className="card-surface mt-6">
          <EmptyState
            variant="error"
            title={t('CTA 估算读取失败')}
            action={
              <button
                type="button"
                onClick={() => ctaQ.refresh()}
                disabled={ctaQ.refreshing}
                className="inline-flex items-center gap-1 rounded-md border border-line px-3 py-1.5 text-caption text-ink-600 hover:bg-paper-2 disabled:opacity-60"
              >
                <Icon name="refresh" size={12} />
                {t('重试')}
              </button>
            }
          />
        </div>
      ) : !row || !ctaQ.data ? (
        <div className="card-surface mt-6">
          <EmptyState title={t('暂无数据')} />
        </div>
      ) : (
        <>
          {/* 有旧快照时刷新失败 → 明示陈旧，不清空页面（与首页同一纪律） */}
          {ctaQ.error && (
            <StaleStrip onRetry={() => ctaQ.refresh()} refreshing={ctaQ.refreshing} className="mt-6" />
          )}
          {/* B1 跨标的总览 */}
          <section className="mt-6" aria-label={t('跨标的总览')}>
            <CtaOverviewStrip
              rows={rows}
              selected={row.instrument}
              onSelect={(value) => selectInstrument(value, true)}
            />
          </section>

          {/* B2 单标的深读 */}
          <section className="mt-8" aria-label={t('单标的深读')}>
            <div ref={mainRef} className="scroll-mt-20">
              <CtaDeepDive
                data={ctaQ.data}
                rows={rows}
                row={row}
                onInstrumentChange={(value) => selectInstrument(value)}
                regimeMean={mean}
              />
            </div>
          </section>

          <SourceNote className="mt-8" text={t('代理模型估算 · 仅供研究参考')} />
        </>
      )}
    </div>
  );
}
