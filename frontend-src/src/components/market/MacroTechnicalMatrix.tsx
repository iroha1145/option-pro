/**
 * 技术形态 × 结构性宏观的二维状态（增量任务 Phase 1）。
 *
 * 为什么是二维而不是一个混合分：技术强而宏观基础脆弱、宏观先行改善而价格没跟上，
 * 这两种情况才是值得看的，混成一个数字正好把它们藏掉。
 *
 * 两个输入都来自后端已有的确定性计算：
 *   - 技术侧 = 市场形态六维的综合均值（RegimePanel 同源）
 *   - 宏观侧 = /macro-conditions 的 structural_score（流动性/融资/国债/利率）
 * 信用与风险不计入结构性宏观：它们和技术形态读的是同一批工具，再算一次等于同一个
 * 信号计两次权。这个取舍在后端 linkage.STRUCTURAL_MODULES 有一份定义，前端不重算。
 *
 * 这张卡不改变任何评分，也不参与突破的六状态分类。
 */
import InfoHint from '@/components/shared/InfoHint';
import { cn } from '@/lib/utils';
import {
  MACRO_QUADRANT_HINT,
  MACRO_QUADRANT_LABEL,
  MACRO_QUADRANT_NOTE,
  macroGap,
  macroQuadrant,
} from '@/lib/macroFit';
import { t } from '../../i18n/core.ts';

/**
 * 为什么排除信用与风险，写在界面上而不是只写在代码注释里：一个「结构性宏观」的
 * 数字如果不说清它少了哪两块，读者会以为它是宏观的全部。
 */
const MATRIX_HINT = {
  title: t('技术 × 结构性宏观'),
  body:
    t('技术侧为市场形态六维均值；宏观侧为结构性宏观（流动性、融资、国债、利率）的加权均值。'),
  // 整句作单一 msgid：英/日与中文语序不同，分段 t() 拼接必然错乱，只能整句翻译。
  note:
    t('信用与风险不计入结构性宏观：它们与技术形态读的是同一批工具（HYG/LQD/KRE/VIX/SPY-TLT/IWM-SPY），再算一次等于同一个信号计两次权。此卡仅展示，不改变任何评分，也不参与突破的六状态分类。')
    + t(MACRO_QUADRANT_NOTE),
};

interface Props {
  /** 市场形态六维综合均值 */
  technical: number | null;
  /** 结构性宏观分 */
  structural: number | null;
  structuralModules?: string[];
  className?: string;
}

const MODULE_LABEL: Record<string, string> = {
  liquidity: t('流动性'),
  funding: t('融资'),
  treasury: t('国债'),
  rates: t('利率'),
};

export default function MacroTechnicalMatrix({
  technical,
  structural,
  structuralModules = [],
  className,
}: Props) {
  const quadrant = macroQuadrant(technical, structural);
  const gap = macroGap(technical, structural);
  const moduleText = structuralModules
    .map((id) => MODULE_LABEL[id] ?? id)
    .join(' · ');

  return (
    <div className={cn('card-surface p-5', className)}>
      <p className="eyebrow">
        {t('技术 × 结构性宏观 · TECHNICAL × MACRO')}
        <InfoHint hint={MATRIX_HINT} side="bottom" size={11} className="ml-1" />
      </p>
      <h3 className="mt-1.5 text-h3 text-ink-900">
        {quadrant ? t(MACRO_QUADRANT_LABEL[quadrant]) : t('暂无二维读数')}
      </h3>
      {quadrant ? (
        <>
          <p className="mt-1 text-body-s text-ink-500">{t(MACRO_QUADRANT_HINT[quadrant])}</p>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2">
            <div>
              <dt className="text-micro text-ink-400">{t('技术形态')}</dt>
              <dd className="font-mono text-data-l text-ink-900 tnum">
                {technical?.toFixed(1)}
              </dd>
            </div>
            <div>
              <dt className="text-micro text-ink-400">{t('结构性宏观')}</dt>
              <dd className="font-mono text-data-l text-ink-900 tnum">
                {structural?.toFixed(1)}
              </dd>
            </div>
          </dl>
          {gap !== null && (
            <p className="mt-3 border-t border-line pt-3 text-caption text-ink-500">
              {t('差值')}{' '}
              <span
                className={cn(
                  'font-mono tnum',
                  gap > 20 ? 'text-warn-600' : gap < -20 ? 'text-brand-700' : 'text-ink-700',
                )}
              >
                {gap > 0 ? '+' : ''}{gap.toFixed(1)}
              </span>
              {gap > 20
                ? t(' · 价格明显跑在环境前面')
                : gap < -20
                  ? t(' · 宏观环境领先价格改善')
                  : t(' · 两者大致同步')}
            </p>
          )}
        </>
      ) : (
        /* 任一侧缺失就不给状态。凑一个出来会把「没数据」说成一个判断。 */
        <p className="mt-1 text-body-s text-ink-400">
          {technical === null && structural === null
            ? t('市场形态与宏观快照都暂不可用')
            : technical === null
              ? t('市场形态六维暂不可用')
              : t('宏观快照暂不可用')}
          {t(' · 不按中性计')}
        </p>
      )}
      {moduleText && (
        <p className="mt-2 text-micro text-ink-300">{t('结构性宏观 =')} {moduleText}</p>
      )}
      {/* 两套分界线不一样，说出来，免得读者拿象限和顺风/逆风对不上。 */}
      <p className="mt-1 text-micro text-ink-300">{t(MACRO_QUADRANT_NOTE)}</p>
    </div>
  );
}
