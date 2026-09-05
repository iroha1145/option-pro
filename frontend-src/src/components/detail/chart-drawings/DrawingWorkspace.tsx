import { useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { useBodyScrollLock } from '@/hooks/useBodyScrollLock';
import { cn } from '@/lib/utils';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import ConfirmDialog from '@/components/catalysts/ConfirmDialog';
import { t } from '../../../i18n/core.ts';
import DrawingInspector from './DrawingInspector.tsx';
import DrawingToolbar from './DrawingToolbar.tsx';
import type { DrawingController } from './useDrawingController.ts';
import { whitelistText } from './schema.ts';

export default function DrawingWorkspace({
  open,
  controller,
  children,
  reducedMotion,
  layersOpen = false,
  onOpenLayers,
  autoPatternsEnabled,
  onToggleAuto,
}: {
  open: boolean;
  controller: DrawingController;
  children: ReactNode;
  reducedMotion: boolean;
  layersOpen?: boolean;
  onOpenLayers?: () => void;
  autoPatternsEnabled?: boolean;
  onToggleAuto?: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  // 房规做法（Drawer / CommandPalette / ConfirmDialog 同一套）：先挂到 DOM 再
  // 下一帧加 is-open，关闭时留到 close 时钟走完——写死 `is-open` 首帧就到位，
  // 进出场两个动画一个都放不出来。reducedMotion 直接不上 t-modal，也就没有时钟。
  const closeMs = readRootDurationMs('--modal-close-dur', 150);
  const phase = useOverlayPhase(open, reducedMotion ? 0 : closeMs);
  const mounted = overlayVisible(open, phase);
  useFocusTrap(panelRef, open);
  useBodyScrollLock(mounted);
  if (!mounted) return <>{children}</>;
  const panel = (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-label={t('绘图工作区')}
      className={cn(
        // bg-paper is the real page token; bg-page is not a color and left
        // the overlay transparent so the stock header/volume showed through.
        'fixed inset-0 z-[70] flex flex-col bg-paper p-3 md:p-4',
        !reducedMotion && 't-modal',
        !reducedMotion && overlayClassName(phase),
      )}
    >
      <DrawingToolbar
        tool={controller.tool}
        onTool={controller.setTool}
        canUndo={controller.canUndo}
        canRedo={controller.canRedo}
        onUndo={controller.undo}
        onRedo={controller.redo}
        autoPatternsEnabled={autoPatternsEnabled ?? controller.autoPatternsEnabled}
        onToggleAuto={onToggleAuto ?? (() => controller.setAutoPatternsEnabled((prev) => !prev))}
        layersOpen={layersOpen}
        onOpenLayers={onOpenLayers}
        expanded={controller.expanded}
        onToggleExpanded={() => controller.setExpanded(false)}
        syncStatus={controller.syncStatus}
        syncHint={controller.syncHint}
        onRetry={controller.retry}
        onKeepLocal={() => void controller.keepLocalConflict()}
        onTakeServer={() => void controller.takeServerConflict()}
      />
      <div className="mt-3 flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-x-hidden md:flex-row">
        <div className="min-h-[240px] min-w-0 flex-1 overflow-hidden rounded-md border border-line bg-card md:min-h-[320px]">
          {children}
        </div>
        <aside className="max-h-[40vh] w-full shrink-0 overflow-x-hidden overflow-y-auto rounded-md border border-line bg-card p-3 md:max-h-none md:w-72">
          <DrawingInspector
            drawing={controller.selected}
            drawings={controller.drawings}
            unresolvedIds={controller.unresolvedIds}
            importError={controller.importError}
            unresolved={Boolean(controller.selected && controller.unresolvedIds.includes(controller.selected.id))}
            onSelect={controller.setSelectedId}
            onStyle={controller.updateStyle}
            onText={(text) => {
              const clean = whitelistText(text);
              if (clean !== null) controller.patchSelected({ text: clean }, true);
            }}
            onLock={() => controller.patchSelected({ locked: !controller.selected?.locked }, true)}
            onHide={() => controller.patchSelected({ hidden: !controller.selected?.hidden }, true)}
            onToggleHidden={(id) => {
              const target = controller.drawings.find((item) => item.id === id);
              if (!target) return;
              controller.patchDrawing(id, { hidden: !target.hidden }, true);
            }}
            onDelete={controller.deleteSelected}
            onDeleteId={controller.deleteDrawing}
            onZ={(delta) => controller.patchSelected({ zOrder: (controller.selected?.zOrder ?? 0) + delta }, true)}
            onExport={() => {
              const blob = new Blob([controller.exportJson()], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const link = document.createElement('a');
              link.href = url;
              link.download = 'chart-drawings.json';
              link.click();
              URL.revokeObjectURL(url);
            }}
            hasRejectedImport={controller.hasRejectedImport}
            onExportRejected={() => {
              const payload = controller.exportRejectedImport();
              if (!payload) return;
              const blob = new Blob([payload], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const link = document.createElement('a');
              link.href = url;
              link.download = 'chart-drawings-unsaved-import.json';
              link.click();
              URL.revokeObjectURL(url);
            }}
            onImportFile={(text) => {
              controller.importFromText(text);
            }}
            onImportLocal={() => controller.importAnonymous()}
            onClear={() => setConfirmClear(true)}
          />
        </aside>
      </div>
      <ConfirmDialog
        open={confirmClear}
        title={t('清除全部手绘')}
        description={t('确认清除当前标的与周期的全部手绘图形？此操作可撤销。')}
        confirmLabel={t('确认清除')}
        danger
        onConfirm={() => {
          controller.clearAll();
          setConfirmClear(false);
        }}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
  return createPortal(panel, document.body);
}
