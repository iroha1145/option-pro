import { useCallback, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  sectorsApi,
  type SectorPeriod,
  type SectorStrengthEnvelope,
} from '@/api/modules/sectors';
import { usePolling } from '@/hooks/usePolling';
import { useShell } from '@/components/Layout';
import { fmtRelative } from '@/lib/format';
import PageHeader from '@/components/shared/PageHeader';
import Segmented from '@/components/shared/Segmented';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonRows } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import HeatMatrix, {
  HeatMatrixSkeleton,
} from '@/components/sectors/HeatMatrix';
import SectorList from '@/components/sectors/SectorList';
import DetailBand from '@/components/sectors/DetailBand';
import IvPanel from '@/components/sectors/IvPanel';
import SideRail from '@/components/sectors/SideRail';
import {
  buildSectorViewModels,
  normalizeIvMeta,
  normalizeIvRow,
} from '@/components/sectors/model';
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
  const ivQ = usePolling(
    () =>
      ivSectorIdValid
        ? sectorsApi.ivRanking(ivSectorIdValid)
        : Promise.resolve(null),
    600_000,
    [ivSectorIdValid],
  );
  const ivRows = useMemo(
    () => (ivQ.data?.rows ?? []).map(normalizeIvRow),
    [ivQ.data],
  );
  const ivMeta = useMemo(() => normalizeIvMeta(ivQ.data), [ivQ.data]);

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
        description={t("比较真实的板块平均收益、平均强度与成分覆盖。")}
        meta={
          <>
            <span className="hidden font-mono text-micro text-ink-400 tnum sm:inline">
              {strengthEnvelope.asOf
                ? `统计截至 ${fmtRelative(strengthEnvelope.asOf)}`
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
        <div
          className="mt-4 flex items-start gap-2 rounded-md border border-warn-600/25 bg-warn-50 px-3 py-2 text-caption text-warn-600"
          role="status"
        >
          <Icon name="flag" size={14} className="mt-0.5 shrink-0" />
          <span>
            {t('板块目录已加载，但收益与强度聚合暂不可用；缺失位置保持为空。')}
          </span>
          <button
            type="button"
            onClick={strengthQ.refresh}
            className="ml-auto shrink-0 font-medium text-warn-600 underline decoration-warn-600/40 underline-offset-2"
          >
            {t('重试')}
          </button>
        </div>
      )}

      <section className="mt-6" aria-label={t("板块总览")}>
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
                  className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                >
                  <Icon name="refresh" size={14} />
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
                  className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                >
                  <Icon name="refresh" size={14} />
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
              transition={{ duration: 0.2 }}
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
        <section className="lg:col-span-7" aria-label={t("IV 横截面排名")}>
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
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                  >
                    <Icon name="refresh" size={14} />
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
                description={t("板块目录没有返回有效编号，未发起 IV 排名请求。")}
                action={
                  <button
                    type="button"
                    onClick={retryOverview}
                    disabled={catalogQ.refreshing || strengthQ.refreshing}
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                  >
                    <Icon name="refresh" size={14} />
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
              error={ivQ.error}
              onRetry={ivQ.refresh}
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
                description={t("板块目录不可用，保持空状态；恢复目录后自动继续。")}
                action={
                  <button
                    type="button"
                    onClick={retryOverview}
                    disabled={catalogQ.refreshing || strengthQ.refreshing}
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                  >
                    <Icon name="refresh" size={14} />
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
                    className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                  >
                    <Icon name="refresh" size={14} />
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
              ivLoading={ivQ.loading}
              onOpenTicker={openTicker}
              onOpenPalette={openPalette}
            />
          )}
        </aside>
      </div>
    </div>
  );
}
