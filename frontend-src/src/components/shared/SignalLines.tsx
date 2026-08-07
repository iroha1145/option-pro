/**
 * 信号文字行（对齐日股工作台展开区 StructLine 排版）：
 * 指标名靠左（ink-400）、读数靠右（mono ink-700），无卡片描边。
 * live 契约带结构化投影（name/value/reading/score）；mock 简签（突破/放量…）
 * 只有 label + at，右侧退回相对时间，不编造读数。
 */
import type { Signal } from '@/api/types';
import { fmtRelative } from '@/lib/format';

export default function SignalLines({ signals }: { signals: Signal[] }) {
  return (
    <div className="space-y-1.5 text-caption">
      {signals.map((s, i) => (
        <div key={`${s.label}-${i}`} className="flex items-center justify-between gap-3">
          <span className="min-w-0 truncate text-ink-400">{s.name ?? s.label}</span>
          <span className="shrink-0 text-right font-mono text-ink-700 tnum">
            {s.value != null
              ? `${Number(s.value.toFixed(2))}${s.reading ? ` · ${s.reading} ${s.score ?? ''}`.trimEnd() : ''}`
              : fmtRelative(s.at)}
          </span>
        </div>
      ))}
    </div>
  );
}
