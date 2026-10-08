/**
 * §MKT 美股概况（/market，从指数 tape ?index= 进入）
 * B1 指数概览（美股指数与其他市场分组） · B2 市场状态 · B3 形态六维 · B4 宏观环境 · B5 信号解读
 * 除 B1 的其他市场一组外，整页读数都只算美股：纽约时段、SPY 等美股 ETF、美国宏观数据。
 * 2026-10-08 第二版导航：行业表现与 CTA 趋势由页头上方的二级标签进入，原 CTA 引导卡与
 * 底部联动卡（板块透视、突破雷达）只剩跳转作用，已删除；突破雷达入口移到信号解读卡里。
 * 轮询：indices+status 60s / 形态+信号 300s / 宏观 15min（visibility 暂停，usePolling）
 */
import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { useShell } from '@/hooks/useShell';
import { marketApi } from '@/api/modules/market';
import { signalsApi } from '@/api/modules/signals';
import { marketPulseApi } from '@/components/market/api';
import { usePolling } from '@/hooks/usePolling';
import { fmtTimeHHMMSS } from '@/lib/format';
import { MARKET_LABEL, MARKET_TO_SESSION } from '@/lib/marketSession';
import type { MarketSession } from '@/api/types';
import PageHeader from '@/components/shared/PageHeader';
import SessionLED from '@/components/shared/SessionLED';
import IndexCards from '@/components/market/IndexCards';
import StatusCard from '@/components/market/StatusCard';
import RegimePanel from '@/components/market/RegimePanel';
import { regimeMean } from '@/lib/regime';
import SignalsReading, { type TrendBias } from '@/components/market/SignalsReading';
import MacroConditionsPanel from '@/components/market/macro/MacroConditionsPanel';
import { pageRegionProps } from '@/lib/pageRegion';
import { t } from '../i18n/core.ts';

export default function Market() {
  const [searchParams] = useSearchParams();
  const focus = searchParams.get('index');
  /* 指数走 stocks/{^GSPC} 全套端点，所以抽屉里能看 K 线与技术信号——那是本页
     唯一真有「按指数」数据的地方（页面其余面板都是全市场读数）。 */
  const { openTicker } = useShell();

  /* 60s：指数 + 市场状态 */
  const indicesQ = usePolling(() => marketApi.indices(), 60_000);
  const statusQ = usePolling(() => marketPulseApi.statusDetail(), 60_000);
  /* 300s：形态六维 + 信号（CTA 趋势资金已剥离为独立页 /cta） */
  const regimeQ = usePolling(() => marketPulseApi.regime(), 300_000);
  const signalsQ = usePolling(() => signalsApi.market(), 300_000);

  const status = statusQ.data;
  /* 时段读不到时显示「时段未知」，不落回「休市」（审计 P2-9）：加载中或状态接口
     异常都会被误读成真实休市，而休市会改变用户对盘中信号的解读。 */
  const session: MarketSession | null =
    status?.market ? MARKET_TO_SESSION[status.market] ?? null : null;

  /* 趋势偏向只依据六维 market_regime；接口没有全市场均分时不做近似替代。 */
  const mean = useMemo(() => (regimeQ.data ? regimeMean(regimeQ.data) : null), [regimeQ.data]);
  const bias: TrendBias | null = useMemo(() => {
    const classify = (v: number): TrendBias['label'] => (v >= 60 ? '偏多' : v <= 40 ? '偏空' : '中性');
    if (mean !== null) {
      return { label: classify(mean), basis: t('推导依据：六项均分 {mean}（≥60 偏多 · ≤40 偏空）', { mean: mean.toFixed(1) }) };
    }
    return null;
  }, [mean]);
  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="market"
        title={t('美股概况')}
        meta={
          <>
            {session ? (
              <SessionLED session={session} label={status?.market ? MARKET_LABEL[status.market] : undefined} />
            ) : (
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full bg-ink-300" aria-hidden="true" />
                <span className="text-caption text-ink-400">
                  {statusQ.loading ? t('正在读取交易时段…') : t('时段未知')}
                </span>
              </span>
            )}
            {indicesQ.lastUpdatedAt && (
              <span className="text-caption text-ink-400 tnum">
                {t('更新')} {fmtTimeHHMMSS(indicesQ.lastUpdatedAt)}
              </span>
            )}
          </>
        }
      />

      {/* B1 指数概览 */}
      <section
        className="mt-6"
        aria-label={t("市场指数")}
        {...pageRegionProps(
          'market-indices',
          indicesQ.loading
            ? 'loading'
            : indicesQ.error
              ? 'error'
              : !indicesQ.data?.length
                ? 'empty'
                : 'content',
        )}
      >
        <IndexCards
          data={indicesQ.data}
          loading={indicesQ.loading}
          error={indicesQ.error}
          focus={focus}
          onRetry={() => indicesQ.refresh()}
          refreshing={indicesQ.refreshing}
          onOpen={openTicker}
        />
      </section>

      {/* B2 市场状态 + B3 形态六维 */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <StatusCard
            data={status}
            loading={statusQ.loading}
            error={statusQ.error}
            onRetry={() => statusQ.refresh()}
            refreshing={statusQ.refreshing}
          />
        </div>
        <div className="lg:col-span-7">
          <RegimePanel
            data={regimeQ.data}
            loading={regimeQ.loading}
            error={regimeQ.error}
            onRetry={() => regimeQ.refresh()}
            refreshing={regimeQ.refreshing}
          />
        </div>
      </div>

      {/* B4 宏观环境（Optix 宏观环境 · 展示与研究用，不进入正式股票评分） */}
      <section className="mt-8" aria-label={t("宏观环境")}>
        {/* 技术侧分数由这里传下去：本页已经有形态六维均值，面板不必为一张展示卡
            再拉一次 /strength/market。 */}
        <MacroConditionsPanel technicalScore={mean} />
      </section>

      {/* B5 信号解读 */}
      <div className="mt-8">
        <SignalsReading
          signals={signalsQ.data}
          loading={signalsQ.loading}
          error={signalsQ.error}
          onRetry={() => signalsQ.refresh()}
          refreshing={signalsQ.refreshing}
          indices={indicesQ.data}
          regimeMean={mean}
          status={status}
          bias={bias}
        />
      </div>
    </div>
  );
}
