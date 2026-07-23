/**
 * 关键数据 definition list（stock-detail.md S3 侧栏）
 * 开盘/最高/最低/52周高低/市值/PE 等，Mono 行间发丝线；52 周区间标尺
 */
import { fmtCompact, fmtPrice } from '@/lib/format';
import type { StockDetail } from '@/api/types';

export default function KeyStats({ detail }: { detail: StockDetail }) {
  const rows: [string, string][] = [
    ['今开', fmtPrice(detail.open)],
    ['昨收', fmtPrice(detail.prevClose)],
    ['最高', fmtPrice(detail.high)],
    ['最低', fmtPrice(detail.low)],
    ['成交量', fmtCompact(detail.volume)],
    ['均量', fmtCompact(detail.avgVolume)],
    ['市值', `$${fmtCompact(detail.marketCap)}`],
    ['市盈率', detail.pe == null ? '—' : detail.pe.toFixed(1)],
    ['IV 百分位', `${detail.ivPercentile}%`],
  ];
  const [lo52, hi52] = detail.range52w;
  const pos = Math.min(100, Math.max(2, ((detail.price - lo52) / Math.max(1e-9, hi52 - lo52)) * 100));

  return (
    <div className="card-surface p-5">
      <p className="eyebrow">KEY STATS</p>
      <h3 className="mt-1.5 text-h3 text-ink-900">关键数据</h3>
      <dl className="mt-3 divide-y divide-line">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between py-2">
            <dt className="text-body-s text-ink-400">{k}</dt>
            <dd className="font-mono text-body-s text-ink-800 tnum">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-3 border-t border-line pt-3">
        <div className="flex items-center justify-between text-micro text-ink-400">
          <span>52 周区间</span>
          <span className="font-mono tnum">
            {fmtPrice(lo52)} — {fmtPrice(hi52)}
          </span>
        </div>
        <div className="relative mt-2 h-1 rounded-pill bg-line" role="presentation">
          <div className="h-full rounded-pill bg-brand-100" style={{ width: '100%' }} />
          <span
            className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-brand-600 shadow-sh-1"
            style={{ left: `${pos}%` }}
          />
        </div>
      </div>
    </div>
  );
}
