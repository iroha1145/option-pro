import { useRef } from 'react';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { t } from '../../../i18n/core.ts';
import type { ChartDrawing, DrawingKind, DrawingStyle } from './types.ts';

const KIND_LABEL: Record<DrawingKind, string> = {
  horizontal: t('水平线'),
  segment: t('趋势线'),
  ray: t('射线'),
  channel: t('平行通道'),
  rectangle: t('矩形'),
  fibonacci: t('斐波那契'),
  text: t('文字'),
};

const IMPORT_ERROR: Record<string, string> = {
  invalid_json: t('导入失败：JSON 无效'),
  too_many: t('导入失败：数量过多'),
  too_large: t('导入失败：数据无效'),
  illegal_text: t('导入失败：文字不合法'),
  id_conflict: t('导入失败：编号冲突'),
  invalid_boolean: t('导入失败：布尔字段无效'),
  invalid_drawing: t('导入失败：数据无效'),
  corrupt: t('导入失败：数据无效'),
  unsupported_version: t('导入失败：数据无效'),
};

const COLORS: { value: string; name: string }[] = [
  { value: '#2E46E0', name: t('颜色 蓝色') },
  { value: '#0E9F6E', name: t('颜色 绿色') },
  { value: '#E5484D', name: t('颜色 红色') },
  { value: '#E8930C', name: t('颜色 橙色') },
  { value: '#0B7285', name: t('颜色 青色') },
  { value: '#3D4A68', name: t('颜色 墨色') },
];
const WIDTHS: DrawingStyle['width'][] = [1, 2, 3, 4];
const DASHES: { id: DrawingStyle['dash']; label: string }[] = [
  { id: 'solid', label: t('实线') },
  { id: 'dashed', label: t('虚线') },
  { id: 'dotted', label: t('点线') },
];

export default function DrawingInspector({
  drawing,
  drawings = [],
  unresolvedIds = [],
  importError = null,
  unresolved,
  onSelect,
  onStyle,
  onText,
  onLock,
  onHide,
  onToggleHidden,
  onDelete,
  onDeleteId,
  onZ,
  onExport,
  onImportFile,
  onImportLocal,
  onClear,
}: {
  drawing: ChartDrawing | null;
  drawings?: ChartDrawing[];
  unresolvedIds?: string[];
  importError?: string | null;
  unresolved: boolean;
  onSelect?: (id: string) => void;
  onStyle: (style: DrawingStyle) => void;
  onText: (text: string) => void;
  onLock: () => void;
  onHide: () => void;
  onToggleHidden?: (id: string) => void;
  onDelete: () => void;
  onDeleteId?: (id: string) => void;
  onZ: (delta: number) => void;
  onExport: () => void;
  onImportFile: (text: string) => void;
  onImportLocal: () => void;
  onClear: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const listed = [...drawings].sort((a, b) => a.zOrder - b.zOrder);
  return (
    <div className="flex flex-col gap-3 text-caption text-ink-600">
      <p className="text-micro font-medium text-ink-500">{t('对象')}</p>
      {listed.length === 0 ? (
        <p className="text-micro text-ink-400">{t('当前没有手绘图形')}</p>
      ) : (
        <ul className="flex flex-col gap-1" aria-label={t('对象')}>
          {listed.map((item) => {
            const unresolvedItem = unresolvedIds.includes(item.id);
            return (
              <li key={item.id} className="flex items-center gap-1">
                <button
                  type="button"
                  aria-label={t('绘图对象 {name}', { name: KIND_LABEL[item.kind] })}
                  aria-pressed={drawing?.id === item.id}
                  onClick={() => onSelect?.(item.id)}
                  className={cn(
                    'min-w-0 flex-1 rounded-xs border px-2 py-1 text-left text-micro outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
                    drawing?.id === item.id ? 'border-brand-400 bg-brand-50' : 'border-line',
                  )}
                >
                  <span>{KIND_LABEL[item.kind]}</span>
                  {item.hidden ? <span className="ml-1 text-ink-400">{t('已隐藏')}</span> : null}
                  {item.locked ? <span className="ml-1 text-ink-400">{t('已锁定')}</span> : null}
                  {unresolvedItem ? <span className="ml-1 text-warn-600">{t('未解析')}</span> : null}
                  <span className="ml-1 font-mono text-ink-400">{t('层级 {n}', { n: item.zOrder })}</span>
                </button>
                <button
                  type="button"
                  aria-label={item.hidden ? t('显示') : t('隐藏')}
                  onClick={() => onToggleHidden?.(item.id)}
                  className="rounded-xs border border-line px-1.5 py-1 text-micro"
                >
                  <Icon name={item.hidden ? 'eye' : 'eye-off'} size={13} />
                </button>
                <button
                  type="button"
                  aria-label={t('删除图形')}
                  onClick={() => onDeleteId?.(item.id)}
                  className="rounded-xs border border-down-600/40 px-1.5 py-1 text-micro text-down-600"
                >
                  <Icon name="x" size={13} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-micro font-medium text-ink-500">{t('样式')}</p>
      {importError && (
        <p className="rounded-xs border border-warn-600/30 bg-warn-50 px-2 py-1 text-micro text-warn-600" role="alert">
          {IMPORT_ERROR[importError] ?? t('导入失败：数据无效')}
        </p>
      )}
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
                key={color.value}
                type="button"
                aria-label={color.name}
                aria-pressed={drawing.style.color.toUpperCase() === color.value}
                onClick={() => onStyle({ ...drawing.style, color: color.value })}
                className={cn(
                  'size-6 min-h-11 min-w-11 rounded-xs border outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30 md:size-6 md:min-h-6 md:min-w-6',
                  drawing.style.color.toUpperCase() === color.value ? 'border-ink-700' : 'border-line',
                )}
                style={{ background: color.value }}
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
