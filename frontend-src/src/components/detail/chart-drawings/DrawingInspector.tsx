import { useRef } from 'react';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { t } from '../../../i18n/core.ts';
import type { ChartDrawing, DrawingStyle } from './types.ts';

const COLORS = ['#2E46E0', '#0E9F6E', '#E5484D', '#E8930C', '#0B7285', '#3D4A68'];
const WIDTHS: DrawingStyle['width'][] = [1, 2, 3, 4];
const DASHES: { id: DrawingStyle['dash']; label: string }[] = [
  { id: 'solid', label: t('实线') },
  { id: 'dashed', label: t('虚线') },
  { id: 'dotted', label: t('点线') },
];

export default function DrawingInspector({
  drawing,
  unresolved,
  onStyle,
  onText,
  onLock,
  onHide,
  onDelete,
  onZ,
  onExport,
  onImportFile,
  onImportLocal,
  onClear,
}: {
  drawing: ChartDrawing | null;
  unresolved: boolean;
  onStyle: (style: DrawingStyle) => void;
  onText: (text: string) => void;
  onLock: () => void;
  onHide: () => void;
  onDelete: () => void;
  onZ: (delta: number) => void;
  onExport: () => void;
  onImportFile: (text: string) => void;
  onImportLocal: () => void;
  onClear: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  return (
    <div className="flex flex-col gap-3 text-caption text-ink-600">
      <p className="text-micro font-medium text-ink-500">{t('样式')}</p>
      {unresolved && (
        <p className="rounded-xs border border-warn-600/30 bg-warn-50 px-2 py-1 text-micro text-warn-600">
          {t('锚点无法解析（数据已更新）')}
        </p>
      )}
      {drawing ? (
        <>
          <div className="flex flex-wrap gap-1" role="group" aria-label={t('颜色')}>
            {COLORS.map((color) => (
              <button
                key={color}
                type="button"
                aria-label={t('颜色')}
                aria-pressed={drawing.style.color.toUpperCase() === color}
                onClick={() => onStyle({ ...drawing.style, color })}
                className={cn(
                  'size-6 rounded-xs border outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
                  drawing.style.color.toUpperCase() === color ? 'border-ink-700' : 'border-line',
                )}
                style={{ background: color }}
              />
            ))}
          </div>
          <div className="flex gap-1" role="group" aria-label={t('线宽')}>
            {WIDTHS.map((width) => (
              <button
                key={width}
                type="button"
                aria-label={t('线宽 {n}', { n: width })}
                aria-pressed={drawing.style.width === width}
                onClick={() => onStyle({ ...drawing.style, width })}
                className={cn(
                  'rounded-xs border px-2 py-0.5 font-mono text-micro outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
                  drawing.style.width === width ? 'border-brand-400 bg-brand-50' : 'border-line',
                )}
              >
                {width}
              </button>
            ))}
          </div>
          <div className="flex gap-1" role="group" aria-label={t('线型')}>
            {DASHES.map((dash) => (
              <button
                key={dash.id}
                type="button"
                aria-label={dash.label}
                aria-pressed={drawing.style.dash === dash.id}
                onClick={() => onStyle({ ...drawing.style, dash: dash.id })}
                className={cn(
                  'rounded-xs border px-2 py-0.5 text-micro outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
                  drawing.style.dash === dash.id ? 'border-brand-400 bg-brand-50' : 'border-line',
                )}
              >
                {dash.label}
              </button>
            ))}
          </div>
          {(drawing.kind === 'channel' || drawing.kind === 'rectangle') && (
            <label className="flex items-center gap-2 text-micro">
              <span>{t('填充透明度')}</span>
              <input
                type="range"
                min={0}
                max={0.4}
                step={0.02}
                value={drawing.style.fillOpacity ?? 0}
                onChange={(event) => onStyle({ ...drawing.style, fillOpacity: Number(event.target.value) })}
                className="flex-1"
              />
            </label>
          )}
          {drawing.kind === 'text' && (
            <label className="flex flex-col gap-1 text-micro">
              <span>{t('文字注释')}</span>
              <textarea
                maxLength={240}
                value={drawing.text ?? ''}
                onChange={(event) => onText(event.target.value)}
                className="min-h-[72px] rounded-xs border border-line bg-card px-2 py-1 text-caption outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
              />
            </label>
          )}
          <div className="flex flex-wrap gap-1">
            <button type="button" aria-label={drawing.locked ? t('解锁') : t('锁定')} aria-pressed={drawing.locked} onClick={onLock} className="rounded-xs border border-line px-2 py-1 text-micro">
              <Icon name={drawing.locked ? 'unlock' : 'lock'} size={13} className="mr-1 inline" />
              {drawing.locked ? t('解锁') : t('锁定')}
            </button>
            <button type="button" aria-label={drawing.hidden ? t('显示') : t('隐藏')} aria-pressed={drawing.hidden} onClick={onHide} className="rounded-xs border border-line px-2 py-1 text-micro">
              <Icon name={drawing.hidden ? 'eye' : 'eye-off'} size={13} className="mr-1 inline" />
              {drawing.hidden ? t('显示') : t('隐藏')}
            </button>
            <button type="button" aria-label={t('上移一层')} onClick={() => onZ(1)} className="rounded-xs border border-line px-2 py-1 text-micro">{t('上移一层')}</button>
            <button type="button" aria-label={t('下移一层')} onClick={() => onZ(-1)} className="rounded-xs border border-line px-2 py-1 text-micro">{t('下移一层')}</button>
            <button type="button" aria-label={t('删除图形')} onClick={onDelete} className="rounded-xs border border-down-600/40 px-2 py-1 text-micro text-down-600">{t('删除图形')}</button>
          </div>
        </>
      ) : (
        <p className="text-micro text-ink-400">{t('当前没有手绘图形')}</p>
      )}
      <div className="flex flex-wrap gap-1 border-t border-line pt-2">
        <button type="button" onClick={onExport} className="rounded-xs border border-line px-2 py-1 text-micro">{t('导出 JSON')}</button>
        <button type="button" onClick={() => fileRef.current?.click()} className="rounded-xs border border-line px-2 py-1 text-micro">{t('导入 JSON')}</button>
        <button type="button" onClick={onImportLocal} className="rounded-xs border border-line px-2 py-1 text-micro">{t('导入本机绘图')}</button>
        <button type="button" onClick={onClear} className="rounded-xs border border-down-600/40 px-2 py-1 text-micro text-down-600">{t('清除全部手绘')}</button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json"
          className="hidden"
          onChange={async (event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (!file) return;
            onImportFile(await file.text());
          }}
        />
      </div>
    </div>
  );
}
