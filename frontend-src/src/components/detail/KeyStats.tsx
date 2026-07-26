/**
 * 关键数据 definition list（stock-detail.md S3 侧栏）
 * 开盘/最高/最低/52周高低/市值/PE 等，Mono 行间发丝线；52 周区间标尺
 * live 契约缺失字段（运行时 null）如实显「—」；52 周区间缺失时隐藏标尺（留空优于编造）
 */
import { fmtCompact, fmtPrice } from '@/lib/format';
import type { StockDetail } from '@/api/types';
import { t } from '../../i18n/core.ts';

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const priceOr = (v: number | null | undefined): string => (isNum(v) ? fmtPrice(v) : '—');
const compactOr = (v: number | null | undefined): string => (isNum(v) ? fmtCompact(v) : '—');

export default function KeyStats({ detail }: { detail: StockDetail }) {
  const rows: [string, string][] = [
    [t('今开'), priceOr(detail.open)],
    [t('昨收'), priceOr(detail.prevClose)],
    [t('最高'), priceOr(detail.high)],
    [t('最低'), priceOr(detail.low)],
    [t('成交量'), compactOr(detail.volume)],
    [t('均量'), compactOr(detail.avgVolume)],
    [t('市值'), isNum(detail.marketCap) ? `$${fmtCompact(detail.marketCap)}` : '—'],
    [t('市盈率'), isNum(detail.pe) ? detail.pe.toFixed(1) : '—'],
    [t('IV 百分位'), isNum(detail.ivPercentile) ? `${detail.ivPercentile}%` : '—'],
  ];
  const r52 = detail.range52w;
  const has52 = Array.isArray(r52) && isNum(r52[0]) && isNum(r52[1]);
  const pos = has52 ? Math.min(100, Math.max(2, ((detail.price - r52[0]) / Math.max(1e-9, r52[1] - r52[0])) * 100)) : 0;

  return (
    <div className="card-surface p-5">
      <p className="eyebrow">KEY STATS</p>
      <h3 className="mt-1.5 text-h3 text-ink-900">{t('关键数据')}</h3>
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
          <span>{t('52 周区间')}</span>
          <span className="font-mono tnum">{has52 ? `${fmtPrice(r52[0])} — ${fmtPrice(r52[1])}` : '—'}</span>
        </div>
        {has52 && (
          <div className="relative mt-2 h-1 rounded-pill bg-line" role="presentation">
            <div className="h-full rounded-pill bg-brand-100" style={{ width: '100%' }} />
            <span
              className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-brand-600 shadow-sh-1"
              style={{ left: `${pos}%` }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
