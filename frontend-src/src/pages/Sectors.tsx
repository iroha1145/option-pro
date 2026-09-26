import { useCallback, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  sectorsApi,
  type SectorPeriod,
  type SectorStrengthEnvelope,
} from '@/api/modules/sectors';
import { usePolling } from '@/hooks/usePolling';
import { useShell } from '@/hooks/useShell';
import { fmtRelative, fmtTimeHHMMSS } from '@/lib/format';
import { DUR_UI } from '@/lib/motion';
import PageHeader from '@/components/shared/PageHeader';
import Segmented from '@/components/shared/Segmented';
import EmptyState from '@/components/shared/EmptyState';
import StatusNotice from '@/components/shared/StatusNotice';
import { SkeletonRows } from '@/components/shared/Skeleton';
import { BusyIcon } from '@/components/shared/IconSwap';
import HeatMatrix, {
  HeatMatrixSkeleton,
} from '@/components/sectors/HeatMatrix';
import SectorList from '@/components/sectors/SectorList';
import DetailBand from '@/components/sectors/DetailBand';
import IvPanel from '@/components/sectors/IvPanel';
import SideRail from '@/components/sectors/SideRail';
import { useSectorIvRefresh } from '@/components/sectors/useSectorIvRefresh';
import {
  buildSectorViewModels,
  normalizeIvMeta,
  normalizeIvRow,
} from '@/components/sectors/model';
import { pageRegionProps } from '@/lib/pageRegion';
import { t } from '../i18n/core.ts';

function emptyStrength(period: SectorPeriod): SectorStrengthEnvelope {
  return {
    asOf: null,
    period,
    periodDays: null,
    sectors: [],
    count: 0,
    cached: false,
    snapshotSource: null,
    stale: false,
    sourceStatus: null,
    staleReason: null,
  };
}

export default function Sectors() {
  const { openTicker, openPalette } = useShell();
  const [view, setView] = useState<'heat' | 'list'>('heat');
  const [period, setPeriod] = useState<SectorPeriod>('3mo');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ivSectorId, setIvSectorId] = useState<string | null>(null);

  const catalogQ = usePolling(() => sectorsApi.list(), 600_000);
  const strengthQ = usePolling(
    () => sectorsApi.strength(period),
    600_000,
    [period],
  );
  const strengthEnvelope = strengthQ.data ?? emptyStrength(period);
  const sectors = useMemo(
    () => buildSectorViewModels(catalogQ.data ?? [], strengthEnvelope),
    [catalogQ.data, strengthEnvelope],
  );

  // IV 路径只接受后端目录中的完整 sector id。目录未到达前不发请求，
  // 避免旧占位符（如 semi）先触发 404 并污染首屏状态。
  const catalogIds = useMemo(
    () => new Set((catalogQ.data ?? []).map((sector) => sector.id)),
    [catalogQ.data],
  );
  const ivSectorIdValid =
    ivSectorId && catalogIds.has(ivSectorId)
      ? ivSectorId
      : (catalogQ.data?.[0]?.id ?? null);
  /* IV 使用独立的任务读取流：正常每 10 分钟确认一次，后台任务 queued/running
     时缩短到 3 秒；按板块世代丢弃晚到响应，避免切换后串台。 */
  const ivQ = useSectorIvRefresh(ivSectorIdValid);
  const ivData = ivQ.data;
  const ivRows = useMemo(
    () => (ivData?.rows ?? []).map(normalizeIvRow),
    [ivData],
  );
  const ivMeta = useMemo(() => normalizeIvMeta(ivData), [ivData]);

  const selected = useMemo(
    () => sectors.find((sector) => sector.id === selectedId) ?? null,
    [sectors, selectedId],
  );
  const ivSector = useMemo(
    () => sectors.find((sector) => sector.id === ivSectorIdValid) ?? null,
    [sectors, ivSectorIdValid],
  );

  const onToggleSelect = useCallback((id: string) => {
    setSelectedId((previous) => {
      const next = previous === id ? null : id;
      if (next) setIvSectorId(next);
      return next;
    });
  }, []);

  const retryOverview = () => {
    catalogQ.refresh();
    strengthQ.refresh();
  };
  const overviewLoading =
    catalogQ.loading || (strengthQ.loading && strengthQ.data === null);

  return (
    <div>
      <PageHeader
        section="04"
        eyebrow="SECTORS · LIVE AGGREGATES"
        title={t("板块透视")}
        description={t("比较各板块的平均涨跌幅、个股强度与数据覆盖情况。")}
        meta={
          <>
            <span className="hidden font-mono text-micro text-ink-400 tnum sm:inline">
              {strengthEnvelope.asOf
                ? t('统计截至 {time}', { time: fmtRelative(strengthEnvelope.asOf) })
                : t('统计时间 —')}
            </span>
            <Segmented
              options={[
                { value: 'heat', label: t('热力') },
                { value: 'list', label: t('列表') },
              ]}
              value={view}
              onChange={setView}
            />
          </>
        }
      />

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-y border-line py-3">
        <div>
          <p className="text-caption font-medium text-ink-700">{t('收益统计周期')}</p>
          <p className="text-micro text-ink-400">
            {t('数值由板块成分股汇总得出')}
          </p>
        </div>
        <Segmented
          options={[
            { value: '1mo', label: t('1 个月') },
            { value: '3mo', label: t('3 个月') },
            { value: '6mo', label: t('6 个月') },
          ]}
          value={period}
          onChange={setPeriod}
        />
      </div>

      {strengthQ.error && !catalogQ.error && (
        <StatusNotice
          className="mt-4"
          action={
            <button
              type="button"
              onClick={() => strengthQ.refresh()}
              className="min-h-9 rounded-md px-2 text-caption font-medium text-brand-600 hover:bg-brand-50"
            >
              {t('重试')}
            </button>
          }
        >
          <span>
            {/* 横幅必须与页面事实一致（审计 2.2.10）：usePolling 失败不清空旧数据，
              * 有旧快照时下面显示的是上次成功的数值，要说「已过期」而不是「留空」。 */}
            {strengthQ.data
              ? t('板块强度更新失败，当前显示上次结果（已过期{time}）。', {
                  time: strengthQ.lastUpdatedAt
                    ? ` · ${fmtTimeHHMMSS(strengthQ.lastUpdatedAt)}`
                    : '',
                })
              : t('板块列表已加载，涨跌幅与强度数据暂不可用。')}
          </span>
        </StatusNotice>
      )}
      {!strengthQ.error && strengthEnvelope.stale && (
        <StatusNotice className="mt-4">
          {t('部分板块数据已过期，请结合各项数据日期查看。')}
        </StatusNotice>
      )}

      <section
        className="mt-6"
        aria-label={t("板块总览")}
        {...pageRegionProps(
          'sectors',
          overviewLoading
            ? 'loading'
            : catalogQ.error
              ? 'error'
              : sectors.length === 0
                ? 'empty'
                : 'content',
        )}
      >
        {overviewLoading ? (
          <div className="card-surface p-4 md:p-6">
            <HeatMatrixSkeleton />
          </div>
        ) : catalogQ.error ? (
          <div className="card-surface">
            <EmptyState
              variant="error"
              image="/empty-chart.svg"
              title={t("板块目录加载失败")}
              description={catalogQ.error.message}
              action={
                <button
                  type="button"
                  onClick={retryOverview}
                  disabled={catalogQ.refreshing || strengthQ.refreshing}
                  aria-busy={catalogQ.refreshing || strengthQ.refreshing}
                  className="btn-primary"
                >
                  <BusyIcon busy={catalogQ.refreshing || strengthQ.refreshing} size={14} tone="on-accent" />
                  {t('重试')}
                </button>
              }
            />
          </div>
        ) : sectors.length === 0 ? (
          <div className="card-surface">
            <EmptyState
              image="/empty-chart.svg"
              title={t("暂无板块目录")}
              description={t("板块目录暂时为空，重试可重新拉取。")}
              action={
                <button
                  type="button"
                  onClick={retryOverview}
                  disabled={catalogQ.refreshing || strengthQ.refreshing}
                  aria-busy={catalogQ.refreshing || strengthQ.refreshing}
                  className="btn-primary"
                >
                  <BusyIcon busy={catalogQ.refreshing || strengthQ.refreshing} size={14} tone="on-accent" />
                  {t('重试')}
                </button>
              }
            />
          </div>
        ) : (
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={view}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: DUR_UI }}
            >
              {view === 'heat' ? (
                <div className="card-surface p-4 md:p-6">
                  <HeatMatrix
                    sectors={sectors}
                    selectedId={selectedId}
                    onSelect={onToggleSelect}
                  />
                </div>
              ) : (
                <SectorList
                  sectors={sectors}
                  selectedId={selectedId}
                  onSelect={onToggleSelect}
                />
              )}
            </motion.div>
          </AnimatePresence>
        )}
      </section>

      <AnimatePresence initial={false}>
        {selected && !catalogQ.error && (
          <DetailBand sector={selected} onOpenTicker={openTicker} />
        )}
      </AnimatePresence>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <section className="lg:col-span-7" aria-label={t("当前隐含波动率排名")}>
          {catalogQ.error ? (
            <div className="card-surface">
              <EmptyState
                variant="error"
                icon="doc-quote"
                title={t("IV 排名联动暂停")}
                description={t("板块目录不可用，无法确定查询范围；恢复目录后自动继续。")}
                action={
                  <button
                    type="button"
                    onClick={retryOverview}
                    disabled={catalogQ.refreshing || strengthQ.refreshing}
                    aria-busy={catalogQ.refreshing || strengthQ.refreshing}
                    className="btn-primary"
                  >
                    <BusyIcon busy={catalogQ.refreshing || strengthQ.refreshing} size={14} tone="on-accent" />
                    {t('重试')}
                  </button>
                }
              />
            </div>
          ) : catalogQ.loading ? (
            <div className="card-surface p-4 md:p-6">
              <SkeletonRows rows={6} />
            </div>
          ) : !ivSectorIdValid ? (
            <div className="card-surface">
              <EmptyState
                image="/empty-chart.svg"
                title={t("暂无可查询的板块")}
                description={t("板块信息不完整，暂无法查询隐含波动率排名。")}
                action={
                  <button
                    type="button"
                    onClick={retryOverview}
                    disabled={catalogQ.refreshing || strengthQ.refreshing}
                    aria-busy={catalogQ.refreshing || strengthQ.refreshing}
                    className="btn-primary"
                  >
                    <BusyIcon busy={catalogQ.refreshing || strengthQ.refreshing} size={14} tone="on-accent" />
                    {t('重试')}
                  </button>
                }
              />
            </div>
          ) : (
            <IvPanel
              sectors={sectors.map((sector) => ({
                id: sector.id,
                name: sector.name,
              }))}
              sectorId={ivSectorIdValid}
              onSectorChange={setIvSectorId}
              data={ivRows}
              meta={ivMeta}
              loading={ivQ.loading}
              refreshing={ivQ.refreshing}
              error={ivQ.readError}
              refresh={ivQ.refresh}
              submitting={ivQ.submitting}
              actionError={ivQ.actionError}
              onRetry={ivQ.retryRead}
              onRefresh={ivQ.requestRefresh}
              onOpenTicker={openTicker}
            />
          )}
        </section>

        <aside className="lg:col-span-5" aria-label={t("板块 IV 数据")}>
          {catalogQ.error ? (
            <div className="card-surface">
              <EmptyState
                variant="error"
                icon="doc-quote"
                title={t("IV 数据暂不可用")}
                description={t("板块信息暂不可用，恢复后将自动更新。")}
                action={
                  <button
                    type="button"
                    onClick={retryOverview}
                    disabled={catalogQ.refreshing || strengthQ.refreshing}
                    aria-busy={catalogQ.refreshing || strengthQ.refreshing}
                    className="btn-primary"
                  >
                    <BusyIcon busy={catalogQ.refreshing || strengthQ.refreshing} size={14} tone="on-accent" />
                    {t('重试')}
                  </button>
                }
              />
            </div>
          ) : !catalogQ.loading && !ivSectorIdValid ? (
            <div className="card-surface">
              <EmptyState
                image="/empty-chart.svg"
                title={t("暂无板块 IV 数据")}
                description={t("等待板块目录提供有效查询范围，可重试拉取目录。")}
                action={
                  <button
                    type="button"
                    onClick={retryOverview}
                    disabled={catalogQ.refreshing || strengthQ.refreshing}
                    aria-busy={catalogQ.refreshing || strengthQ.refreshing}
                    className="btn-primary"
                  >
                    <BusyIcon busy={catalogQ.refreshing || strengthQ.refreshing} size={14} tone="on-accent" />
                    {t('重试')}
                  </button>
                }
              />
            </div>
          ) : (
            <SideRail
              sector={ivSector}
              rows={ivRows}
              meta={ivMeta}
              loading={catalogQ.loading}
              ivLoading={ivQ.loading || (!ivData && !ivQ.readError)}
              ivError={!!ivQ.readError && !ivData}
              onIvRetry={ivQ.retryRead}
              onOpenTicker={openTicker}
              onOpenPalette={openPalette}
            />
          )}
        </aside>
      </div>
    </div>
  );
}
