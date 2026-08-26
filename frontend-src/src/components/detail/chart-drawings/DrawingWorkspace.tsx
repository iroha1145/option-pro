import { useRef, useState, type ReactNode } from 'react';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { cn } from '@/lib/utils';
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
}: {
  open: boolean;
  controller: DrawingController;
  children: ReactNode;
  reducedMotion: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  useFocusTrap(panelRef, open);
  if (!open) return <>{children}</>;
  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="true"
      aria-label={t('绘图工作区')}
      className={cn(
        'fixed inset-0 z-[70] flex flex-col bg-page p-3 md:p-4',
        reducedMotion ? '' : 't-modal is-open',
      )}
    >
      <DrawingToolbar
        tool={controller.tool}
        onTool={controller.setTool}
        canUndo={controller.canUndo}
        canRedo={controller.canRedo}
        onUndo={controller.undo}
        onRedo={controller.redo}
        autoPatternsEnabled={controller.autoPatternsEnabled}
        onToggleAuto={() => controller.setAutoPatternsEnabled((prev) => !prev)}
        expanded={controller.expanded}
        onToggleExpanded={() => controller.setExpanded(false)}
        syncStatus={controller.syncStatus}
        onRetry={controller.retry}
      />
      <div className="mt-3 flex min-h-0 flex-1 flex-col gap-3 md:flex-row">
        <div className="min-h-0 min-w-0 flex-1 overflow-hidden rounded-md border border-line bg-card">
          {children}
        </div>
        <aside className="w-full shrink-0 overflow-auto rounded-md border border-line bg-card p-3 md:w-72">
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
              if (clean) controller.patchSelected({ text: clean }, true);
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
}
