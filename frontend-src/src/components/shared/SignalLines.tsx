/**
 * 信号文字行（对齐日股工作台展开区 StructLine 排版）：
 * 三列网格——指标名（左）/ 读数（右对齐 mono）/ 方向·分值（分值 3ch 右对齐）。
 * 逐行整串右对齐会因读数宽度不一而列位参差，必须分列各自对齐。
 * live 契约带结构化投影（name/value/reading/score）；mock 简签（突破/放量…）
 * 只有 label + at，右侧退回相对时间，不编造读数。
 */
import { Fragment } from 'react';
import type { Signal } from '@/api/types';
import { fmtRelative } from '@/lib/format';

export default function SignalLines({ signals }: { signals: Signal[] }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_max-content_max-content] items-baseline gap-x-3 gap-y-1.5 text-caption">
      {signals.map((s, i) =>
        s.value != null ? (
          <Fragment key={`${s.label}-${i}`}>
            <span className="truncate text-ink-400">{s.name ?? s.label}</span>
            <span className="text-right font-mono text-ink-700 tnum">{Number(s.value.toFixed(2))}</span>
            <span className="text-ink-500">
              {s.reading ? (
                <>
                  {'· '}
                  {s.reading}
                  <span className="ml-1 inline-block w-[3ch] text-right font-mono text-ink-700 tnum">{s.score}</span>
                </>
              ) : null}
            </span>
          </Fragment>
        ) : (
          <Fragment key={`${s.label}-${i}`}>
            <span className="truncate text-ink-400">{s.name ?? s.label}</span>
            <span className="col-span-2 text-right font-mono text-micro text-ink-400 tnum">{fmtRelative(s.at)}</span>
          </Fragment>
        ),
      )}
    </div>
  );
}
