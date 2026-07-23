/**
 * B1 列表模式（sectors.md：DataTable 板块/涨跌幅/强度条/成分数/领涨/领跌/IV 均值，行点击同下钻）
 */
import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { fmtPct } from '@/lib/format';
import DataTable, { type Column } from '@/components/shared/DataTable';
import ChangeBadge from '@/components/shared/ChangeBadge';
import StrengthBar from '@/components/shared/StrengthBar';
import Icon from '@/components/icons';
import type { SectorVm } from './model';

interface SectorListProps {
  sectors: SectorVm[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function SectorList({ sectors, selectedId, onSelect }: SectorListProps) {
  const columns = useMemo<Column<SectorVm>[]>(
    () => [
      {
        key: 'name',
        title: '板块',
        sortable: true,
        sortValue: (r) => r.name,
        render: (r) => (
          <span className="flex items-center gap-2.5">
            <span className="flex size-6 items-center justify-center rounded-xs bg-brand-50 text-brand-700">
              <Icon name="layers" size={13} />
            </span>
            <span className="text-body-s font-semibold text-ink-800">{r.name}</span>
            <span className="font-mono text-micro text-ink-300">{r.id.toUpperCase()}</span>
          </span>
        ),
      },
      {
        key: 'changePct',
        title: '涨跌幅',
        align: 'right',
        sortable: true,
        sortValue: (r) => r.changePct ?? -Infinity,
        render: (r) => (r.changePct !== null ? <ChangeBadge value={r.changePct} /> : <span className="font-mono text-ink-300">—</span>),
      },
      {
        key: 'strength',
        title: '强度',
        sortable: true,
        sortValue: (r) => r.strengthScore ?? -1,
        render: (r) =>
          r.strengthScore !== null ? <StrengthBar score={r.strengthScore} width={72} /> : <span className="font-mono text-ink-300">—</span>,
      },
      {
        key: 'count',
        title: '成分数',
        align: 'right',
        sortable: true,
        sortValue: (r) => r.count,
        render: (r) => <span className="font-mono text-data-m text-ink-600 tnum">{r.count}</span>,
      },
      {
        key: 'leader',
        title: '领涨',
        render: (r) =>
          r.leader ? (
            <span className="flex items-center gap-1.5 font-mono text-body-s tnum">
              <Icon name="arrow-up-right" size={12} className="text-up-600" strokeWidth={1.8} />
              <span className="font-semibold text-ink-800">{r.leader.ticker}</span>
              <span className="text-up-700">{fmtPct(r.leader.changePct)}</span>
            </span>
          ) : (
            <span className="font-mono text-ink-300">—</span>
          ),
      },
      {
        key: 'laggard',
        title: '领跌',
        render: (r) =>
          r.laggard ? (
            <span className="flex items-center gap-1.5 font-mono text-body-s tnum">
              <Icon name="arrow-down-right" size={12} className="text-down-600" strokeWidth={1.8} />
              <span className="font-semibold text-ink-800">{r.laggard.ticker}</span>
              <span className="text-down-700">{fmtPct(r.laggard.changePct)}</span>
            </span>
          ) : (
            <span className="font-mono text-ink-300">—</span>
          ),
      },
      {
        key: 'ivAvg',
        title: 'IV 均值',
        align: 'right',
        sortable: true,
        sortValue: (r) => r.ivAvg ?? -1,
        render: (r) =>
          r.ivAvg !== null ? (
            <span className="font-mono text-data-m text-ink-600 tnum">{r.ivAvg.toFixed(1)}%</span>
          ) : (
            <span className="font-mono text-ink-300">—</span>
          ),
      },
    ],
    [],
  );

  return (
    <DataTable
      columns={columns}
      rows={sectors}
      rowKey={(r) => r.id}
      onRowClick={(r) => onSelect(r.id)}
      rowHeight={44}
      rowClassName={(r) => cn(selectedId === r.id && 'bg-brand-50 hover:bg-brand-50')}
    />
  );
}
