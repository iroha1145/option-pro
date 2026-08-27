import InfoHint from '@/components/shared/InfoHint';
import { TOOL_HINTS } from './hints';
import Icon, { type IconName } from '@/components/icons';
import { cn } from '@/lib/utils';
import { t } from '../../../i18n/core.ts';
import type { DrawingTool } from './tools.ts';
import type { SyncStatus } from './useDrawingController.ts';

const TOOLS: { id: DrawingTool; icon: IconName; label: string }[] = [
  { id: 'select', icon: 'crosshair', label: t('选择') },
  { id: 'horizontal', icon: 'minus', label: t('水平线') },
  { id: 'segment', icon: 'trend-line', label: t('趋势线') },
  { id: 'ray', icon: 'ray-right', label: t('射线') },
  { id: 'channel', icon: 'channel', label: t('平行通道') },
  { id: 'rectangle', icon: 'rect', label: t('矩形') },
  { id: 'fibonacci', icon: 'fib', label: t('斐波那契') },
  { id: 'text', icon: 'text-note', label: t('文字') },
];

function toolButtonCls(active: boolean): string {
  return cn(
    'inline-flex size-8 min-h-11 min-w-11 items-center justify-center rounded-xs border text-ink-500 outline-none transition-colors duration-fast md:size-8 md:min-h-8 md:min-w-8',
    'focus-visible:ring-2 focus-visible:ring-brand-500/30',
    active
      ? 'border-brand-400 bg-brand-50 text-brand-700 shadow-chip'
      : 'border-line hover:text-ink-700',
  );
}

export default function DrawingToolbar({
  tool,
  onTool,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  autoPatternsEnabled,
  onToggleAuto,
  layersOpen = false,
  onOpenLayers,
  expanded,
  onToggleExpanded,
  syncStatus,
  syncHint = null,
  onRetry,
  onKeepLocal,
  onTakeServer,
  compact = false,
}: {
  tool: DrawingTool;
  onTool: (tool: DrawingTool) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  autoPatternsEnabled: boolean;
  onToggleAuto: () => void;
  layersOpen?: boolean;
  onOpenLayers?: () => void;
  expanded: boolean;
  onToggleExpanded: () => void;
  syncStatus: SyncStatus;
  syncHint?: string | null;
  onRetry: () => void;
  onKeepLocal?: () => void;
  onTakeServer?: () => void;
  compact?: boolean;
}) {
  /* 配额满与「没同步上」是两回事：任务已被丢弃，重试必然再失败，所以这里要
     给出真实原因而不是通用的「未同步 + 重试同步」。 */
  const quotaBlocked = syncStatus === 'unsynced' && syncHint === 'quota';
  const syncLabel =
    syncStatus === 'conflict'
      ? t('绘图冲突：已保留本地版本，请选择')
      : quotaBlocked
        ? t('云端绘图配额已满，本次修改未保存，已恢复云端版本。')
      : syncStatus === 'unsynced' || syncStatus === 'load_failed' || syncStatus === 'write_failed'
        ? t('未同步')
        : syncStatus === 'saving'
          ? t('保存中')
          : syncStatus === 'guest'
            ? t('访客绘图只保存在本机，登录后不会自动合并')
            : t('已同步');
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', compact && 'gap-1')}>
      <span className="mr-1 text-micro font-medium text-ink-500">{t('绘图')}</span>
      {TOOLS.map((item) => (
        /* 悬停按钮本体就出解释：工具条上只有图标，光靠 title 属性既慢又只给
           标签名，用户无从知道「射线」「平行通道」画出来该怎么读。 */
        <InfoHint key={item.id} hint={TOOL_HINTS[item.id]} side="bottom" size={13}>
          <button
            type="button"
            aria-label={item.label}
            aria-pressed={tool === item.id}
            onClick={() => onTool(item.id)}
            className={toolButtonCls(tool === item.id)}
          >
            <Icon name={item.icon} size={15} />
          </button>
        </InfoHint>
      ))}
      <span className="mx-1 h-4 w-px bg-line" aria-hidden />
      <button type="button" aria-label={t('撤销')} disabled={!canUndo} onClick={onUndo} className={cn(toolButtonCls(false), !canUndo && 'opacity-40')}>
        <Icon name="undo" size={15} />
      </button>
      <button type="button" aria-label={t('重做')} disabled={!canRedo} onClick={onRedo} className={cn(toolButtonCls(false), !canRedo && 'opacity-40')}>
        <Icon name="redo" size={15} />
      </button>
      <button
        type="button"
        aria-label={t('算法与图层')}
        aria-pressed={layersOpen || autoPatternsEnabled}
        onClick={() => (onOpenLayers ? onOpenLayers() : onToggleAuto())}
        className={toolButtonCls(layersOpen || autoPatternsEnabled)}
      >
        <Icon name="layers" size={15} />
      </button>
      <button
        type="button"
        aria-label={expanded ? t('收起图表') : t('展开图表')}
        aria-pressed={expanded}
        onClick={onToggleExpanded}
        className={toolButtonCls(expanded)}
      >
        <Icon name={expanded ? 'compress' : 'expand'} size={15} />
      </button>
      <span
        className={cn(
          'ml-1 inline-flex items-center gap-1 text-micro',
          syncStatus === 'unsynced' || syncStatus === 'load_failed' || syncStatus === 'write_failed' || syncStatus === 'conflict' ? 'text-warn-600' : 'text-ink-400',
        )}
        aria-live="polite"
      >
        <span className={cn('size-1.5 rounded-full', syncStatus === 'unsynced' || syncStatus === 'load_failed' || syncStatus === 'write_failed' || syncStatus === 'conflict' ? 'bg-warn-600' : syncStatus === 'saving' ? 'bg-brand-400' : 'bg-up-600')} aria-hidden />
        <span>{syncStatus === 'guest' && compact ? t('未同步') : syncLabel}</span>
        {!quotaBlocked && (syncStatus === 'unsynced' || syncStatus === 'load_failed' || syncStatus === 'write_failed') && (
          <button type="button" className="underline-offset-2 hover:underline" onClick={onRetry} aria-label={t('重试同步')}>
            {t('重试同步')}
          </button>
        )}
        {syncStatus === 'conflict' && (
          <>
            {syncHint === 'conflict' ? <span className="sr-only">{t('绘图冲突：已保留本地版本，请选择')}</span> : null}
            <button type="button" className="underline-offset-2 hover:underline" onClick={onKeepLocal} aria-label={t('保留本地并重试')}>
              {t('保留本地并重试')}
            </button>
            <button type="button" className="underline-offset-2 hover:underline" onClick={onTakeServer} aria-label={t('使用服务器版本')}>
              {t('使用服务器版本')}
            </button>
          </>
        )}
      </span>
    </div>
  );
}

export { TOOLS };
