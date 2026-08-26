import { t } from '../../../i18n/core.ts';
import type { ChartDrawing } from './types.ts';

export default function DrawingContextMenu({
  drawing,
  open,
  x,
  y,
  onLock,
  onHide,
  onDelete,
  onClose,
}: {
  drawing: ChartDrawing | null;
  open: boolean;
  x: number;
  y: number;
  onLock: () => void;
  onHide: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  if (!open || !drawing) return null;
  return (
    <div
      role="menu"
      aria-label={t('对象')}
      className="fixed z-[80] min-w-[160px] rounded-md border border-line bg-card p-1 shadow-sh-2"
      style={{ left: x, top: y }}
    >
      <button type="button" role="menuitem" className="block w-full rounded-xs px-2 py-1 text-left text-caption hover:bg-brand-50" onClick={() => { onLock(); onClose(); }}>
        {drawing.locked ? t('解锁') : t('锁定')}
      </button>
      <button type="button" role="menuitem" className="block w-full rounded-xs px-2 py-1 text-left text-caption hover:bg-brand-50" onClick={() => { onHide(); onClose(); }}>
        {drawing.hidden ? t('显示') : t('隐藏')}
      </button>
      <button type="button" role="menuitem" className="block w-full rounded-xs px-2 py-1 text-left text-caption text-down-600 hover:bg-down-50" onClick={() => { onDelete(); onClose(); }}>
        {t('删除图形')}
      </button>
    </div>
  );
}
