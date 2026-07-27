/**
 * 宏观适配详情块：分数 + 顺/逆风 + 正负驱动因素 + 口径说明。
 *
 * 用在选股行展开和个股抽屉里。两处显示的是同一个板块级读数（暴露画像是板块的，
 * 不是个股的），所以措辞刻意说「该板块」，不让人以为这是对这一只票单独算的。
 *
 * 没读到就说没读到：不显示 50，不显示「中性」，也不显示空白。
 */
import InfoHint from '@/components/shared/InfoHint';
import { macroShadowHint } from '@/lib/scoreHints';
import MacroFitBadge from '@/components/shared/MacroFitBadge';
import {
  driverText,
  macroMissingReason,
  type MacroFitDriver,
} from '@/lib/macroFit';
import { t } from '../../i18n/core.ts';

interface Props {
  score: number | null | undefined;
  tailwind?: string | null;
  confidence?: number | null;
  supporting?: MacroFitDriver[];
  opposing?: MacroFitDriver[];
  /** 技术市场适配 − 结构性宏观；正数＝价格跑在环境前面。 */
  technicalGap?: number | null;
  status?: string | null;
  /** 省略标题行，用在已经有自己标题的容器里。 */
  bare?: boolean;
}

export default function MacroFitPanel({
  score,
  tailwind,
  confidence,
  supporting,
  opposing,
  technicalGap,
  status,
  bare = false,
}: Props) {
  const positive = driverText(supporting, t);
  const negative = driverText(opposing, t);
  const hasScore = typeof score === 'number' && Number.isFinite(score);
  return (
    <div>
      {!bare && (
        <p className="eyebrow">
          {t('宏观适配 · MACRO FIT')}
          <InfoHint hint={macroShadowHint()} side="bottom" size={11} className="ml-1" />
        </p>
      )}
      <div className="mt-3 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <MacroFitBadge score={score} tailwind={tailwind} status={status} />
          {hasScore && typeof confidence === 'number' && (
            <span className="font-mono text-micro text-ink-400 tnum">
              {t('置信度')} {Math.round(confidence * 100)}%
            </span>
          )}
        </div>
        {hasScore ? (
          <>
            {positive && (
              <p className="text-caption leading-[18px] text-ink-600">
                <span className="text-up-700">{t('正面')}</span>{t('：')}{positive}
              </p>
            )}
            {negative && (
              <p className="text-caption leading-[18px] text-ink-600">
                <span className="text-down-700">{t('负面')}</span>{t('：')}{negative}
              </p>
            )}
            {!positive && !negative && (
              <p className="text-caption text-ink-400">{t('该板块各宏观因子方向不明显')}</p>
            )}
            {typeof technicalGap === 'number' && Number.isFinite(technicalGap) && (
              <p className="text-micro text-ink-400">
                {t('技术 − 结构性宏观 =')}{' '}
                <span className="font-mono tnum">
                  {technicalGap > 0 ? '+' : ''}{technicalGap.toFixed(1)}
                </span>
                {technicalGap > 20
                  ? t(' · 价格明显跑在环境前面')
                  : technicalGap < -20
                    ? t(' · 宏观先行改善，价格未跟上')
                    : ''}
              </p>
            )}
          </>
        ) : (
          <p className="text-caption text-ink-400">
            {t(macroMissingReason(status) ?? '暂无宏观读数')} {t('· 不按中性计')}
          </p>
        )}
        <p className="text-micro text-ink-300">{t('影子字段 · 不参与排名')}</p>
      </div>
    </div>
  );
}
