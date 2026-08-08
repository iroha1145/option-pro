/**
 * 技术指标卡（个股整页右栏 · 与日股工作台同款六指标）
 * 数据源 /stocks/{t}/technical 的 technicals 段；缺失如实显「—」。
 */
import InfoHint from '@/components/shared/InfoHint';
import { TECHNICAL_HINTS } from '@/lib/structureHints';
import type { ScoreHint } from '@/lib/scoreHints';
import type { TechnicalStructure } from '@/api/types';
import { t } from '../../i18n/core.ts';

function MiniStat({ label, value, hint }: { label: string; value: string; hint?: ScoreHint }) {
  return (
    <div className="rounded-md bg-paper-2 px-1.5 py-1.5 text-center">
      <div className="truncate font-mono text-body-s font-medium text-ink-900 tnum">{value}</div>
      <div className="flex items-center justify-center gap-0.5 text-micro text-ink-400">
        <span className="truncate" title={label}>{label}</span>
        {hint && <InfoHint hint={hint} size={11} side="bottom" />}
      </div>
    </div>
  );
}

const signedPct = (v: number | null, digits: number): string =>
  v === null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(digits)}%`;

export default function TechnicalPanel({ technical }: { technical: TechnicalStructure | null }) {
  if (!technical) return <p className="mt-3 text-caption text-ink-400">{t('暂无数据')}</p>;
  const tech = technical.technicals;
  return (
    <dl className="mt-3 grid grid-cols-3 gap-1.5">
      <MiniStat
        label={t('RSI 14')}
        value={tech.rsi14 !== null ? tech.rsi14.toFixed(1) : '—'}
        hint={TECHNICAL_HINTS.rsi14}
      />
      {/* 柱体位置（零轴上/下）与柱体变化是两回事：柱体在零轴下时「变化为正」
          只是空头动能衰减。主数显示柱体变化，标签行标注柱体当前正负。 */}
      <MiniStat
        label={
          tech.macd.histogram_pct !== null
            ? t('MACD 柱变化（柱{sign}）', { sign: tech.macd.histogram_pct >= 0 ? '+' : '−' })
            : t('MACD 柱变化')
        }
        value={signedPct(tech.macd.direction_pct, 2)}
        hint={TECHNICAL_HINTS.macd}
      />
      <MiniStat
        label={t('趋势效率')}
        value={tech.trend_efficiency_63d !== null ? tech.trend_efficiency_63d.toFixed(2) : '—'}
        hint={TECHNICAL_HINTS.trend_efficiency}
      />
      <MiniStat
        label={t('MA50 斜率')}
        value={signedPct(tech.ma50_slope_pct_21d, 1)}
        hint={TECHNICAL_HINTS.ma50_slope}
      />
      <MiniStat
        label={t('区间位置')}
        value={tech.range_position_60d !== null ? `${Math.round(tech.range_position_60d * 100)}%` : '—'}
        hint={TECHNICAL_HINTS.range_position}
      />
      {/* 原名「波动稳定」易被读成 0-100 的稳定度评分；它其实是日收益标准差。 */}
      <MiniStat
        label={t('20 日波动率')}
        value={tech.return_stability_20d !== null ? `${(tech.return_stability_20d * 100).toFixed(1)}%` : '—'}
        hint={TECHNICAL_HINTS.return_stability}
      />
    </dl>
  );
}
