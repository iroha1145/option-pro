/**
 * §04 板块透视（sectors.md 完整实现）
 * B0 页头带（§04 + 热力/列表 Segmented + 数据时间戳）
 * B1 板块热力矩阵（11 砖，§1.7 连续色阶，hover 毛玻璃 tooltip，点击下钻）
 *    / 列表模式（DataTable）· 200ms 交叉淡入
 * B2 板块详情带（选中时 accordion 展开：成分 mini 表 + 30 日点阵趋势 + 三项对照）
 * B3 IV 排名面板（chips 联动 / 排序 / 过期横幅 / 降级徽标 / 503 空态）
 * B4 右栏（IV 热力 9 宫格 / 波动率洞察 / 板块相关性）
 * 轮询 600s · 骨架 / 空态 / 503 · 移动端适配
 */
import { useCallback, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { sectorsApi } from '@/api/modules/sectors';
import { usePolling } from '@/hooks/usePolling';
import { useShell } from '@/components/Layout';
import { fmtTimeHHMMSS } from '@/lib/format';
import PageHeader from '@/components/shared/PageHeader';
import Segmented from '@/components/shared/Segmented';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonRows } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import HeatMatrix, { HeatMatrixSkeleton } from '@/components/sectors/HeatMatrix';
import SectorList from '@/components/sectors/SectorList';
import DetailBand from '@/components/sectors/DetailBand';
import IvPanel from '@/components/sectors/IvPanel';
import SideRail from '@/components/sectors/SideRail';
import { normalizeIvRow, normalizeSector } from '@/components/sectors/model';

export default function Sectors() {
  const { openTicker, openPalette } = useShell();
  const [view, setView] = useState<'heat' | 'list'>('heat');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ivSectorId, setIvSectorId] = useState('semi'); // 默认半导体（成分最齐）

  /* 数据：sectors 600s 轮询（api-contract §0-7）；iv-ranking 随板块切换重取 */
  const sectorsQ = usePolling(() => sectorsApi.list(), 600_000);
  const sectors = useMemo(() => (sectorsQ.data ?? []).map(normalizeSector), [sectorsQ.data]);

  /* live 模式下若所选板块不存在于列表，渲染期回退到第一个板块（避免 effect 级联） */
  const ivSectorIdValid = sectors.length > 0 && !sectors.some((s) => s.id === ivSectorId) ? sectors[0].id : ivSectorId;
  const ivQ = usePolling(() => sectorsApi.ivRanking(ivSectorIdValid), 600_000, [ivSectorIdValid]);
  const ivRows = useMemo(() => (ivQ.data ?? []).map(normalizeIvRow), [ivQ.data]);

  const selected = useMemo(() => sectors.find((s) => s.id === selectedId) ?? null, [sectors, selectedId]);
  const ivSector = useMemo(() => sectors.find((s) => s.id === ivSectorIdValid) ?? null, [sectors, ivSectorIdValid]);

  /* 热力砖 / 列表行点击：选中下钻（再次点击取消），并联动 B3 pills */
  const onToggleSelect = useCallback((id: string) => {
    setSelectedId((prev) => {
      const next = prev === id ? null : id;
      if (next) setIvSectorId(next);
      return next;
    });
  }, []);

  const retrySectors = sectorsQ.refresh;

  return (
    <div>
      {/* B0 页头带 */}
      <PageHeader
        section="04"
        eyebrow="SECTORS · IV RANKING"
        title="板块透视"
        description="钱在哪个板块，波动率便在哪里定价。"
        meta={
          <>
            <span className="hidden font-mono text-micro text-ink-400 tnum sm:inline" suppressHydrationWarning>
              {sectorsQ.lastUpdatedAt ? `数据更新 ${fmtTimeHHMMSS(sectorsQ.lastUpdatedAt)}` : '数据更新 —'}
            </span>
            <Segmented
              options={[
                { value: 'heat', label: '热力' },
                { value: 'list', label: '列表' },
              ]}
              value={view}
              onChange={setView}
            />
          </>
        }
      />

      {/* B1 板块热力矩阵 / 列表（200ms 交叉淡入） */}
      <section className="mt-6" aria-label="板块总览">
        {sectorsQ.loading ? (
          <div className="card-surface p-4 md:p-6">
            <HeatMatrixSkeleton />
          </div>
        ) : sectorsQ.error ? (
          <div className="card-surface">
            <EmptyState
              variant="error"
              image="/empty-chart.svg"
              title={sectorsQ.error.code === 503 ? '板块快照暂不可用' : '板块数据加载失败'}
              description={sectorsQ.error.code === 503 ? '接口未覆盖此能力，留空而非编造' : sectorsQ.error.message}
              action={
                <button
                  type="button"
                  onClick={retrySectors}
                  disabled={sectorsQ.refreshing}
                  className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
                >
                  {sectorsQ.refreshing ? (
                    <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                  ) : (
                    <Icon name="refresh" size={14} />
                  )}
                  重试
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
                  <HeatMatrix sectors={sectors} selectedId={selectedId} onSelect={onToggleSelect} />
                </div>
              ) : (
                <SectorList sectors={sectors} selectedId={selectedId} onSelect={onToggleSelect} />
              )}
            </motion.div>
          </AnimatePresence>
        )}
      </section>

      {/* B2 板块详情带（选中时出现，accordion 320ms） */}
      <AnimatePresence initial={false}>
        {selected && !sectorsQ.error && <DetailBand sector={selected} onOpenTicker={openTicker} />}
      </AnimatePresence>

      {/* B3 + B4 */}
      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <section className="lg:col-span-7" aria-label="IV 排名面板">
          {sectorsQ.error ? (
            <div className="card-surface">
              <EmptyState
                variant="error"
                icon="doc-quote"
                title="IV 排名联动暂停"
                description="板块快照不可用，排名面板暂不渲染"
              />
            </div>
          ) : sectorsQ.loading ? (
            <div className="card-surface p-4 md:p-6">
              <SkeletonRows rows={6} />
            </div>
          ) : (
            <IvPanel
              sectors={sectors.map((s) => ({ id: s.id, name: s.name }))}
              sectorId={ivSectorIdValid}
              onSectorChange={setIvSectorId}
              data={ivRows}
              loading={ivQ.loading}
              error={ivQ.error}
              onRetry={ivQ.refresh}
              onOpenTicker={openTicker}
            />
          )}
        </section>

        <aside className="lg:col-span-5" aria-label="板块侧栏">
          {sectorsQ.error ? (
            <div className="card-surface">
              <EmptyState variant="error" icon="doc-quote" title="侧栏暂不可用" description="板块快照不可用，留空而非编造" />
            </div>
          ) : (
            <SideRail
              sector={ivSector}
              rows={ivRows}
              loading={sectorsQ.loading}
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
