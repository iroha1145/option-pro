/**
 * 板块切换条（11 板块；复用分段选择的外观、键盘与减少动态效果处理）
 * 随 B1 热力砖选中联动，也可手动改（作用于 B3 IV 排名面板）
 */
import { useEffect, useRef } from 'react';
import HorizontalScroller from '@/components/shared/HorizontalScroller';
import Segmented from '@/components/shared/Segmented';
import { t } from '../../i18n/core.ts';

interface SectorChipsProps {
  sectors: { id: string; name: string }[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}

export default function SectorChips({ sectors, value, onChange, className }: SectorChipsProps) {
  /* 热力砖联动切板块时，选中 chip 可能在滚动条藏起的 1400px 里：滚入视口，
     否则 tablist 看起来「没有选中任何项」。 */
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    active?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
  }, [value]);
  /* 11 个板块在窄容器里会溢出（实测 2117px 轨道 / 643px 视口，藏掉 1474px），
     而滚动条是隐藏的 —— 和热点带同一个问题：桌面端鼠标无从滚动，也看不出右边
     还有。滚动交给 HorizontalScroller，role="tablist" 留在真正装 tab 的元素上。 */
  return (
    <HorizontalScroller className={className} scrollerClassName="py-0.5">
      <div ref={listRef} className="w-max">
        <Segmented
          options={sectors.map((sector) => ({ value: sector.id, label: sector.name }))}
          value={value}
          onChange={onChange}
          ariaLabel={t('板块切换')}
          scrollable
        />
      </div>
    </HorizontalScroller>
  );
}
