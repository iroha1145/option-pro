import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { fmtPct } from '@/lib/format';
import DataTable, { type Column } from '@/components/shared/DataTable';
import StrengthBar from '@/components/shared/StrengthBar';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import Icon from '@/components/icons';
import type { SectorVm } from './model';
import { periodLabel } from './model';

interface SectorListProps {
  sectors: SectorVm[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function SectorList({
  sectors,
  selectedId,
  onSelect,
}: SectorListProps) {
  const columns = useMemo<Column<SectorVm>[]>(
    () => [
      {
        key: 'name',
        title: '板块',
        sortable: true,
        sortValue: (row) => row.name,
        render: (row) => (
          <span className="flex items-center gap-2.5">
            <span className="flex size-6 items-center justify-center rounded-xs bg-brand-50 text-brand-700">
              <Icon name="layers" size={13} />
            </span>
            <span className="text-body-s font-semibold text-ink-800">
              {row.name}
            </span>
          </span>
        ),
      },
      {
        key: 'avgReturn',
        title: '平均收益',
        align: 'right',
        sortable: true,
        sortValue: (row) => row.avgReturn ?? -Infinity,
        render: (row) =>
          row.avgReturn !== null ? (
            <span
              className={cn(
                'font-mono text-data-m font-semibold tnum',
                row.avgReturn >= 0 ? 'text-up-700' : 'text-down-700',
              )}
            >
              {fmtPct(row.avgReturn)}
              <span className="ml-1 text-micro font-normal text-ink-300">
                {periodLabel(row.period)}
              </span>
            </span>
          ) : (
            <span className="font-mono text-ink-300">—</span>
          ),
      },
      {
        key: 'strength',
        title: (
          <>
            平均强度
            <InfoHint hint={SCORE_HINTS.avgStrength} side="bottom" size={11} />
          </>
        ),
        sortable: true,
        sortValue: (row) => row.avgStrength ?? -1,
        render: (row) =>
          row.avgStrength !== null ? (
            <StrengthBar score={row.avgStrength} width={72} />
          ) : (
            <span className="font-mono text-ink-300">—</span>
          ),
      },
      {
        key: 'coverage',
        title: '统计覆盖',
        align: 'right',
        sortable: true,
        sortValue: (row) => row.coveredCount ?? -1,
        render: (row) => (
          <span className="font-mono text-data-m text-ink-600 tnum">
            {row.coveredCount ?? '—'} / {row.memberCount}
          </span>
        ),
      },
      {
        key: 'leaders',
        title: '强度领先',
        render: (row) =>
          row.leaders.length > 0 ? (
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {row.leaders.slice(0, 3).map((leader) => (
                <span
                  key={leader.ticker}
                  className="font-mono text-body-s text-ink-700 tnum"
                >
                  <span className="font-semibold">{leader.ticker}</span>
                  <span className="ml-1 text-ink-400">
                    {leader.score?.toFixed(1) ?? '—'}
                  </span>
                </span>
              ))}
            </span>
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
      rowKey={(row) => row.id}
      onRowClick={(row) => onSelect(row.id)}
      rowHeight={44}
      defaultSort={{ key: 'avgReturn', desc: true }}
      rowClassName={(row) =>
        cn(selectedId === row.id && 'bg-brand-50 hover:bg-brand-50')
      }
      className="[&>table]:min-w-[620px]"
    />
  );
}
