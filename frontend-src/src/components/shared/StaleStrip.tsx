/**
 * 陈旧数据条（共享）：轮询失败但仍有上一份成功数据时，明示「陈旧 + 重试」，
 * 绝不清空已有内容（GPT-5.6-Pro 审计：指数带 / CTA 带 / 聚合状态卡此前
 * 只有列表卡执行这条纪律，其余区块静默显示旧数据）。
 */
import { cn } from '@/lib/utils';
import { t } from '../../i18n/core.ts';

export default function StaleStrip({
  onRetry,
  refreshing,
  className,
  label,
}: {
  onRetry: () => void;
  refreshing: boolean;
  className?: string;
  /** 默认「刷新失败…」；聚合卡可换成「部分读数刷新失败…」 */
  label?: string;
}) {
  return (
    <p
      className={cn(
        'flex items-center justify-between gap-2 rounded-sm bg-warn-50 px-2.5 py-1.5 text-micro text-warn-700',
        className,
      )}
    >
      {label ?? t('刷新失败，显示上次成功的结果')}
      <button
        onClick={onRetry}
        disabled={refreshing}
        className="shrink-0 font-medium underline-offset-2 hover:underline disabled:opacity-60"
      >
        {t('重试')}
      </button>
    </p>
  );
}
