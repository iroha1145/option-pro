/**
 * 结果行共享单元格：强度分条（grow-bar 错峰）/ 分项微条（§6-5 色阶）/ 催化剂 72h 徽标
 * 桌面表格与移动卡片流共用。
 */
import { motion } from 'framer-motion';
import type { ScreenerRow } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import Icon from '@/components/icons';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import {
  screenerStrengthPresentation,
  subscoreDimsOf,
  type CatalystSummary,
} from './types';
import { t } from '../../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

/* ---------------- 强度分：Mono 15 600 + 64px 强度条（与移动卡片共用固定分档） ---------------- */
export function ScoreCell({ score, index }: { score: number; index: number }) {
  const strength = screenerStrengthPresentation(score);
  return (
    <span className="inline-flex items-center gap-2.5" title={`${strength.band} ${strength.label}`}>
      {/* 固定宽度 + 右对齐 + 统一一位小数：分数字符数不一（84 是两位、84.4 是四位）
          会把后面的横条推到各行不同的 x 上，整列看起来歪歪扭扭。tnum 只保证数字等宽，
          管不了字符个数，所以既要定宽也要定小数位（JS 数字 84.0 会打印成 84）。 */}
      <span
        className={cn(
          'w-[3.25rem] shrink-0 text-right font-mono text-[15px] leading-[20px] font-semibold tnum',
          strength.textClass,
        )}
      >
        {score.toFixed(1)}
      </span>
      <span
        className="h-1 w-16 overflow-hidden rounded-pill bg-line"
        role="progressbar"
        aria-label={`强度分 ${score}，${strength.band} ${strength.label}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={score}
        data-strength-band={strength.band}
        data-strength-tone={strength.tone}
      >
        <motion.span
          className={cn('block h-full origin-left rounded-pill', strength.barClass)}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ duration: 0.7, ease: EASE_PAPER, delay: 0.15 + index * 0.03 }}
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
export function SubscoreTicks({ row }: { row: ScreenerRow }) {
  const dims = subscoreDimsOf(row);
  return (
    <span className="group relative inline-flex items-center gap-1" aria-label={t("分项强度")}>
      {dims.map(({ key, value }) => (
        <span key={key} className="inline-block h-[3px] w-[14px] overflow-hidden rounded-full bg-line" aria-hidden="true">
          {value !== null && (
            <span
              className={cn('block h-full rounded-full', strengthBarClass(value))}
              style={{ width: `${Math.max(8, Math.min(100, value))}%` }}
            />
          )}
        </span>
      ))}
      <span className="glass pointer-events-none absolute -top-2 left-1/2 z-20 hidden w-40 -translate-x-1/2 -translate-y-full rounded-md border border-line p-2.5 shadow-sh-2 group-hover:block">
        {dims.map(({ key, label, value }) => (
          <span key={key} className="flex items-center justify-between py-0.5 text-micro">
            <span className="text-ink-500">{label}</span>
            <span className="font-mono text-ink-800 tnum">{value !== null ? value : '—'}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

/* ---------------- 催化剂汇总（72h 窗口）：有数显数 · 0 显 0 · 接口失败显「—」 ---------------- */
export function CatalystBadge({ summary }: { summary: CatalystSummary | undefined }) {
  if (!summary || !summary.loaded) {
    return <span className="skeleton-shimmer inline-block h-5 w-16 rounded-xs" aria-hidden="true" />;
  }
  if (summary.failed) {
    // 批量接口失败：如实「—」（区别于真实 0），不编造计数
    return (
      <span className="font-mono text-caption text-ink-300 tnum" title={t("催化剂数据暂不可用")} aria-label={t("催化剂数据不可用")}>
        —
      </span>
    );
  }
  if (summary.count === 0) {
    return (
      <span className="font-mono text-caption text-ink-300 tnum" aria-label={t("72 小时内无催化剂")}>
        0
      </span>
    );
  }
  const net = summary.pos - summary.neg;
  const tone = net > 0 ? 'text-up-700 bg-up-50' : net < 0 ? 'text-down-700 bg-down-50' : 'text-ink-500 bg-card-warm';
  const label = net > 0 ? t('利多') : net < 0 ? t('利空') : t('中性');
  const countText = `${summary.count}${summary.hasMore ? '+' : ''}`;
  return (
    <span className="group relative inline-flex">
      <span className={cn('inline-flex items-center gap-1 rounded-xs px-1.5 py-0.5 text-micro font-medium leading-[16px]', tone)}>
        <Icon name="bolt" size={11} />
        {label}
        <span className="font-mono tnum">{countText}</span>
      </span>
      <span className="glass pointer-events-none absolute -top-2 right-0 z-20 hidden w-60 -translate-y-full rounded-md border border-line p-3 shadow-sh-2 group-hover:block">
        <span className="block text-micro text-ink-500">
          {t('72h 窗口 · 利多')} <span className="font-mono text-up-700 tnum">{summary.pos}</span>
          {' · '}{t('利空')} <span className="font-mono text-down-700 tnum">{summary.neg}</span>
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
          <span className="mt-1.5 block truncate text-caption text-ink-800" title={summary.latestTitle}>
            {summary.latestTitle}
          </span>
        )}
        {summary.latestAt && (
          <span className="mt-0.5 block font-mono text-micro text-ink-400 tnum">{fmtRelative(summary.latestAt)}</span>
        )}
      </span>
    </span>
  );
}
