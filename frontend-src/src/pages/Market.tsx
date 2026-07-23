/**
 * §MKT 大盘强弱（/market，从指数 tape ?index= 进入）
 * B1 指数概览 6 卡 · B2 市场状态 · B3 形态六维 · B4 信号解读 · B5 强度分布 · B6 联动卡
 * 轮询：indices+status 60s / 形态+信号+强度 300s（visibility 暂停，usePolling）
 */
import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { marketApi } from '@/api/modules/market';
import { signalsApi } from '@/api/modules/signals';
import { strengthApi } from '@/api/modules/strength';
import { marketPulseApi } from '@/components/market/api';
import { usePolling } from '@/hooks/usePolling';
import { fmtTimeHHMMSS } from '@/lib/format';
import type { MarketSession } from '@/api/types';
import PageHeader from '@/components/shared/PageHeader';
import SessionLED from '@/components/shared/SessionLED';
import IndexCards from '@/components/market/IndexCards';
import StatusCard from '@/components/market/StatusCard';
import RegimePanel, { regimeMean } from '@/components/market/RegimePanel';
import SignalsReading, { type TrendBias } from '@/components/market/SignalsReading';
import BreadthHistogram from '@/components/market/BreadthHistogram';
import LinkCards from '@/components/market/LinkCards';
import SourceNote from '@/components/shared/SourceNote';

const MARKET_TO_SESSION: Record<string, MarketSession> = {
  open: 'regular',
  premarket: 'premarket',
  postmarket: 'afterhours',
  closed: 'closed',
};

const SESSION_LABEL: Record<string, string> = {
  open: '盘中',
  premarket: '盘前',
  postmarket: '盘后',
  closed: '休市',
};

export default function Market() {
  const [searchParams] = useSearchParams();
  const focus = searchParams.get('index');

  /* 60s：指数 + 市场状态 */
  const indicesQ = usePolling(() => marketApi.indices(), 60_000);
  const statusQ = usePolling(() => marketPulseApi.statusDetail(), 60_000);
  /* 300s：形态六维 + 信号 + 强度 */
  const regimeQ = usePolling(() => marketPulseApi.regime(), 300_000);
  const signalsQ = usePolling(() => signalsApi.market(), 300_000);
  const strengthQ = usePolling(() => strengthApi.market(), 300_000);

  const status = statusQ.data;
  const session: MarketSession = MARKET_TO_SESSION[status?.market ?? 'closed'] ?? 'closed';

  /* 趋势偏向：优先六维形态均值，退化为全市场强度均值，并标注推导依据 */
  const mean = useMemo(() => (regimeQ.data ? regimeMean(regimeQ.data) : null), [regimeQ.data]);
  const bias: TrendBias | null = useMemo(() => {
    const classify = (v: number): TrendBias['label'] => (v >= 60 ? '偏多' : v <= 40 ? '偏空' : '中性');
    if (mean !== null) {
      return { label: classify(mean), basis: `推导依据：六维形态均值 ${mean.toFixed(1)}（≥60 偏多 · ≤40 偏空）` };
    }
    if (strengthQ.data) {
      const v = strengthQ.data.avgScore;
      return { label: classify(v), basis: `推导依据：全市场强度均值 ${v.toFixed(1)}（market_regime 未覆盖，近似推导）` };
    }
    return null;
  }, [mean, strengthQ.data]);

  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="MKT"
        eyebrow="MARKET PULSE · INDEX & BREADTH"
        title="大盘强弱"
        description="指数、形态与广度的全景。"
        meta={
          <>
            <SessionLED session={session} label={SESSION_LABEL[status?.market ?? 'closed']} />
            {indicesQ.lastUpdatedAt && (
              <span className="font-mono text-caption text-ink-400 tnum">
                更新 {fmtTimeHHMMSS(indicesQ.lastUpdatedAt)}
              </span>
            )}
          </>
        }
      />

      {/* B1 指数概览 */}
      <section className="mt-6" aria-label="指数概览">
        <p className="eyebrow mb-3">指数概览 · INDEX OVERVIEW（延迟行情）</p>
        <IndexCards
          data={indicesQ.data}
          loading={indicesQ.loading}
          error={indicesQ.error}
          focus={focus}
          onRetry={indicesQ.refresh}
          refreshing={indicesQ.refreshing}
        />
      </section>

      {/* B2 市场状态 + B3 形态六维 */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <StatusCard
            data={status}
            loading={statusQ.loading}
            error={statusQ.error}
            onRetry={statusQ.refresh}
            refreshing={statusQ.refreshing}
          />
        </div>
        <div className="lg:col-span-7">
          <RegimePanel
            data={regimeQ.data}
            loading={regimeQ.loading}
            error={regimeQ.error}
            onRetry={regimeQ.refresh}
            refreshing={regimeQ.refreshing}
          />
        </div>
      </div>

      {/* B4 信号解读 + B5 强度分布 */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="lg:col-span-8">
          <SignalsReading
            signals={signalsQ.data}
            loading={signalsQ.loading}
            error={signalsQ.error}
            onRetry={signalsQ.refresh}
            refreshing={signalsQ.refreshing}
            indices={indicesQ.data}
            strength={strengthQ.data}
            regimeMean={mean}
            status={status}
            bias={bias}
          />
        </div>
        <div className="lg:col-span-4">
          <BreadthHistogram
            data={strengthQ.data}
            loading={strengthQ.loading}
            error={strengthQ.error}
            onRetry={strengthQ.refresh}
            refreshing={strengthQ.refreshing}
          />
        </div>
      </div>

      {/* B6 联动卡 */}
      <section className="mt-8" aria-label="联动视图">
        <p className="eyebrow mb-3">联动视图 · DRILL DOWN</p>
        <LinkCards />
      </section>

      <SourceNote className="mt-8" />
    </div>
  );
}
