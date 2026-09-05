/**
 * 结果行共享单元格：强度分条（首帧显示完整比例）/ 分项微条（§6-5 色阶）/ 催化剂 72h 徽标
 * 桌面表格与移动卡片流共用。
 */
import SoftBadge from '@/components/shared/SoftBadge';
import type { ScreenerRow } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import Icon from '@/components/icons';
import PointerTooltip from '@/components/shared/PointerTooltip';
import { strengthBarClass } from '@/lib/strengthColor';
import {
  screenerStrengthPresentation,
  subscoreDimsOf,
  type CatalystSummary,
} from './types';
import { t } from '../../i18n/core.ts';

/* ---------------- 强度分：Mono 15 600 + 64px 强度条（与移动卡片共用固定分档） ---------------- */
export function ScoreCell({ score }: { score: number; index: number }) {
  const strength = screenerStrengthPresentation(score);
  return (
    <span className="inline-flex items-center gap-2.5" title={`${strength.band} ${strength.label}`}>
      {/* 固定宽度 + 右对齐 + 统一一位小数：分数字符数不一（84 是两位、84.4 是四位）
          会把后面的横条推到各行不同的 x 上，整列看起来歪歪扭扭。tnum 只保证数字等宽，
          管不了字符个数，所以既要定宽也要定小数位（JS 数字 84.0 会打印成 84）。 */}
      <SoftBadge tone={strength.badgeTone} className="metric-value w-[3.25rem] shrink-0 justify-end text-[15px] leading-[20px] font-semibold tnum">
        {score.toFixed(1)}
      </SoftBadge>
      <span
        className="strength-track h-1 w-16 overflow-hidden rounded-pill bg-paper"
        role="progressbar"
        aria-label={t('强度分 {score}，{band} {label}', { score, band: strength.band, label: strength.label })}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={score}
        data-strength-band={strength.band}
        data-strength-tone={strength.tone}
      >
        <span
          className={cn('block h-full origin-left rounded-pill', strength.barClass)}
          style={{ width: `${Math.max(2, Math.min(100, score))}%` }}
        />
      </span>
    </span>
  );
}

/* ----------------------------------------------------------------------------
 * 分项微条：4 段（14×3px 轨道 + 比例填充，§6-5 色阶，hover 毛玻璃 tooltip）
 * 数据源与展开区 BREAKDOWN 同源（subscoreDimsOf：live 契约周期分 / mock 四维），
 * 单项缺失（null）如实空轨道，tooltip 该项显「—」——不再出现整排占位。
 * -------------------------------------------------------------------------- */
export function SubscoreTicks({ row, tipSide = 'top' }: { row: ScreenerRow; tipSide?: 'top' | 'bottom' }) {
  const dims = subscoreDimsOf(row);
  return (
    <PointerTooltip
      label={t('分项强度')}
      side={tipSide}
      width={160}
      className="gap-1"
      contentClassName="p-2.5"
      content={dims.map(({ key, label, value }) => (
        <span key={key} className="flex items-center justify-between gap-3 py-0.5 text-micro">
          <span className="text-ink-500">{label}</span>
          <span className="font-mono text-ink-800 tnum">{value !== null ? value : '—'}</span>
        </span>
      ))}
    >
      {dims.map(({ key, value }) => (
        <span key={key} className="inline-block h-1 w-[15px] overflow-hidden rounded-full bg-paper" aria-hidden="true">
          {value !== null && (
            <span
              className={cn('block h-full rounded-full', strengthBarClass(value))}
              style={{ width: `${Math.max(8, Math.min(100, value))}%` }}
            />
          )}
        </span>
      ))}
    </PointerTooltip>
  );
}

/* ---------------- 催化剂汇总（72h 窗口）：有数显数 · 0 显 0 · 接口失败显「—」 ---------------- */
export function CatalystBadge({ summary, tipSide = 'top' }: { summary: CatalystSummary | undefined; tipSide?: 'top' | 'bottom' }) {
  if (!summary || !summary.loaded) {
    return <span className="skeleton-shimmer inline-block h-5 w-16 rounded-xs" aria-hidden="true" />;
  }
  if (summary.failed) {
    // 批量接口失败：如实「—」（区别于真实 0），不编造计数
    return (
      <SoftBadge title={t("催化剂数据暂不可用")} aria-label={t("催化剂数据不可用")}>
        —
      </SoftBadge>
    );
  }
  if (summary.count === 0) {
    return (
      <SoftBadge aria-label={t("72 小时内无催化剂")}>
        0
      </SoftBadge>
    );
  }
  const net = summary.pos - summary.neg;
  const tone = net > 0 ? 'up' : net < 0 ? 'down' : 'neutral';
  const label = net > 0 ? t('利多') : net < 0 ? t('利空') : t('中性');
  const countText = `${summary.count}${summary.hasMore ? '+' : ''}`;
  return (
    <PointerTooltip
      label={`${t('催化剂 · 72H')} · ${label} ${countText}`}
      side={tipSide}
      width={240}
      contentClassName="p-3"
      content={<>
        <span className="block text-micro text-ink-500">
          {t('72h 窗口 · 利多')} <SoftBadge tone="up">{summary.pos}</SoftBadge>
          {' · '}{t('利空')} <SoftBadge tone="down">{summary.neg}</SoftBadge>
          {summary.pending != null ? (
            <>
              {' · '}{t('待分析')} <span className="font-mono tnum">{summary.pending}</span>
            </>
          ) : (
            <>
              {' · '}{t('中性')} <span className="font-mono tnum">{summary.neu}</span>
            </>
          )}
        </span>
        {summary.latestTitle && (
          <span className="mt-1.5 line-clamp-3 break-words text-caption text-ink-800">
            {summary.latestTitle}
          </span>
        )}
        {summary.latestAt && (
          <span className="mt-0.5 block font-mono text-micro text-ink-400 tnum">{fmtRelative(summary.latestAt)}</span>
        )}
      </>}
    >
      <SoftBadge tone={tone}>
        <Icon name="bolt" size={11} />
        {label}
        <span className="font-mono tnum">{countText}</span>
      </SoftBadge>
    </PointerTooltip>
  );
}
