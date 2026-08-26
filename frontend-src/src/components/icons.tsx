/**
 * 手绘细线图标库（design.md §5）
 * viewBox 24 · stroke 1.6 · round 端点/接合 · currentColor
 * 允许 ≤0.3px 手绘抖动；主体落在 20×20 安全区。
 */
import type { ReactElement, SVGProps } from 'react';

export type IconName =
  | 'radar' | 'crosshair' | 'candle' | 'layers' | 'calendar-spark' | 'bolt' | 'star-line'
  | 'filter-funnel' | 'search' | 'command' | 'clock-ny' | 'bell' | 'sun-bmo' | 'moon-amc'
  | 'arrow-up-right' | 'arrow-down-right' | 'external' | 'chevron-down' | 'chevron-right'
  | 'dots-grid' | 'flame-line' | 'spark-ai' | 'shield' | 'target' | 'flag' | 'x' | 'plus'
  | 'refresh' | 'wallet-gauge' | 'doc-quote' | 'logout' | 'arrow-up' | 'arrow-down' | 'minus'
  | 'check' | 'menu' | 'list' | 'cards' | 'languages'
  | 'trend-line' | 'ray-right' | 'channel' | 'rect' | 'fib' | 'text-note'
  | 'lock' | 'unlock' | 'eye' | 'eye-off' | 'undo' | 'redo' | 'expand' | 'compress';

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
}

const PATHS: Record<IconName, ReactElement> = {
  /* 雷达：碟形天线 —— 斜切碟面朝右上 + 馈源杆与信号球 + 左下三角支架 */
  radar: (
    <>
      <path d="M7.1 5.5 A7.2 7.2 0 0 0 17.3 15.7 Z" />
      <path d="M12.2 10.6 L16.3 6.5" />
      <circle cx="17.5" cy="5.3" r="1.7" fill="currentColor" stroke="none" />
      <path d="M6.6 17.4 L9.8 20.8 H3.4 Z" />
    </>
  ),
  crosshair: (
    <>
      <circle cx="12" cy="12" r="6.6" />
      <path d="M12 2.8v3.1M12 18.1v3.1M2.8 12h3.1M18.1 12h3.1" />
    </>
  ),
  candle: (
    <>
      <path d="M7.2 3.4v2.6M7.2 15.6v3.4" />
      <rect x="4.7" y="6" width="5" height="9.6" rx="1" />
      <path d="M16.8 5.2v2.2M16.8 16.8v2.6" />
      <rect x="14.3" y="7.4" width="5" height="9.4" rx="1" />
    </>
  ),
  layers: (
    <>
      <path d="M12 3.4 20.4 8 12 12.6 3.6 8 12 3.4Z" />
      <path d="M4.2 12.4 12 16.6l7.8-4.2" />
      <path d="M4.2 16.3 12 20.5l7.8-4.2" />
    </>
  ),
  'calendar-spark': (
    <>
      <rect x="3.6" y="5.2" width="16.8" height="15.2" rx="2" />
      <path d="M3.7 9.6h16.7M8.2 3.2v3.4M15.8 3.2v3.4" />
      <path d="M12 12.1l.9 2 2 .9-2 .9-.9 2-.9-2-2-.9 2-.9.9-2Z" />
    </>
  ),
  bolt: <path d="M13.2 2.9 5.8 13.4h4.9l-1.3 7.7 7.6-10.6h-5l1.2-7.6Z" />,
  'star-line': (
    <path d="M12 3.6l2.6 5.2 5.7.9-4.1 4 1 5.7-5.2-2.7-5.1 2.7.9-5.7-4-4 5.7-.9L12 3.6Z" />
  ),
  'filter-funnel': <path d="M3.8 5.1h16.4l-6.3 7.2v6.2l-3.8 2v-8.2L3.8 5.1Z" />,
  search: (
    <>
      <circle cx="10.8" cy="10.8" r="6.4" />
      <path d="M15.6 15.6 20.4 20.4" />
    </>
  ),
  command: (
    <path d="M8.6 8.6h6.8v6.8H8.6zM8.6 8.6H6.2a2.4 2.4 0 1 1 2.4-2.4v2.4ZM15.4 8.6h2.4a2.4 2.4 0 1 0-2.4-2.4v2.4ZM8.6 15.4H6.2a2.4 2.4 0 1 0 2.4 2.4v-2.4ZM15.4 15.4h2.4a2.4 2.4 0 1 1-2.4 2.4v-2.4Z" />
  ),
  'clock-ny': (
    <>
      <circle cx="12" cy="12" r="8.4" />
      <path d="M12 7.2v5l3.4 2" />
      <circle cx="18.6" cy="5.4" r="1.6" />
    </>
  ),
  bell: (
    <>
      <path d="M6.1 9.4a5.9 5.9 0 0 1 11.8 0c0 4.4 1.4 5.9 1.4 5.9H4.7s1.4-1.5 1.4-5.9" />
      <path d="M10.3 19.4a1.9 1.9 0 0 0 3.4 0" />
    </>
  ),
  'sun-bmo': (
    <>
      <circle cx="12" cy="14.4" r="4.4" />
      <path d="M12 6.4V4.2M6.1 8.1 4.6 6.6M17.9 8.1l1.5-1.5M3.4 14.4H1.9M22.1 14.4h-1.5M4 20.4h16" />
    </>
  ),
  'moon-amc': (
    <>
      <path d="M19.4 13.9A7.6 7.6 0 1 1 10.1 4.6a6.2 6.2 0 0 0 9.3 9.3Z" />
      <path d="M17.6 3.6l.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5 1.3-.5.5-1.3Z" />
    </>
  ),
  'arrow-up-right': <path d="M6.8 17.2 17.2 6.8M8.1 6.6h9.3v9.3" />,
  'arrow-down-right': <path d="M6.8 6.8l10.4 10.4M17.4 8.1v9.3H8.1" />,
  external: (
    <>
      <path d="M13.6 4.2h6.2v6.2" />
      <path d="M19.6 4.4 11 13" />
      <path d="M18.9 13.9v4.7a1.6 1.6 0 0 1-1.6 1.6H5.4a1.6 1.6 0 0 1-1.6-1.6V6.7a1.6 1.6 0 0 1 1.6-1.6h4.7" />
    </>
  ),
  'chevron-down': <path d="m5.8 9.2 6.2 6.1 6.2-6.1" />,
  'chevron-right': <path d="m9.4 5.9 6.1 6.1-6.1 6.2" />,
  'dots-grid': (
    <>
      <circle cx="7.2" cy="7.2" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="12" cy="7.2" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="16.8" cy="7.2" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="7.2" cy="12" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="16.8" cy="12" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="7.2" cy="16.8" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="12" cy="16.8" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="16.8" cy="16.8" r="1.05" fill="currentColor" stroke="none" />
    </>
  ),
  'flame-line': (
    <path d="M12 21c3.9 0 6.4-2.5 6.4-6 0-2.9-1.9-4.9-3.4-6.8-.4 1.1-1.1 1.9-2.2 2.4.3-2.2-.6-5-3-7.1-.1 2.7-1.1 4-2.5 5.4C6 10.3 5.6 12 5.6 15c0 3.5 2.5 6 6.4 6Z" />
  ),
  /* v8.1：弃四角星——「sparkle+AI」是生成式设计的头号刻板组合。
     改「双弧夹核心点」：从信息流中聚焦提炼一个结论，正是本产品 AI 的职责。 */
  'spark-ai': (
    <>
      <path d="M8.2 4.9a9.7 9.7 0 0 0 0 14.2" />
      <path d="M15.8 4.9a9.7 9.7 0 0 1 0 14.2" />
      <circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.2 5 6v5.2c0 4.4 3 7.6 7 9.6 4-2 7-5.2 7-9.6V6l-7-2.8Z" />
      <path d="M8.8 12l2.2 2.2 4.2-4.4" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <circle cx="12" cy="12" r="4.6" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
    </>
  ),
  flag: (
    <>
      <path d="M5.4 21V4.2" />
      <path d="M5.4 4.6c4.6-2 7 1.8 12.4 0v8.8c-5.4 1.8-7.8-2-12.4 0" />
    </>
  ),
  x: <path d="m6.2 6.2 11.6 11.6M17.8 6.2 6.2 17.8" />,
  plus: <path d="M12 5v14M5 12h14" />,
  refresh: (
    <>
      <path d="M19.8 12a7.8 7.8 0 1 1-2.1-5.3" />
      <path d="M19.9 3.4v3.6h-3.6" />
    </>
  ),
  'wallet-gauge': (
    <>
      <path d="M4.4 15.8a8 8 0 1 1 15.2 0" />
      <path d="m12 15.6 3.4-4.6" />
      <path d="M8.2 20.4h7.6" />
    </>
  ),
  'doc-quote': (
    <>
      <path d="M7.2 4.4h9.6a1.8 1.8 0 0 1 1.8 1.8v11.6a1.8 1.8 0 0 1-1.8 1.8H7.2a1.8 1.8 0 0 1-1.8-1.8V6.2a1.8 1.8 0 0 1 1.8-1.8Z" />
      <path d="M9 9.2c-1 .5-1.6 1.3-1.6 2.5h1.9v2.6H7V11c0-1.6.8-2.7 2.3-3.3L9 9.2ZM14.4 9.2c-1 .5-1.6 1.3-1.6 2.5h1.9v2.6h-2.3V11c0-1.6.8-2.7 2.3-3.3l-.3 1.5Z" />
    </>
  ),
  logout: (
    <>
      <path d="M14.6 8.2V6.4a1.8 1.8 0 0 0-1.8-1.8H6.2a1.8 1.8 0 0 0-1.8 1.8v11.2a1.8 1.8 0 0 0 1.8 1.8h6.6a1.8 1.8 0 0 0 1.8-1.8v-1.8" />
      <path d="M9.8 12h10.4M17.2 8.6l3.4 3.4-3.4 3.4" />
    </>
  ),
  'arrow-up': <path d="M12 19V5.2M5.8 11.4 12 5.2l6.2 6.2" />,
  'arrow-down': <path d="M12 5v13.8M5.8 12.6l6.2 6.2 6.2-6.2" />,
  minus: <path d="M5 12h14" />,
  check: <path d="m4.8 12.8 4.6 4.6L19.2 6.6" />,
  menu: <path d="M4 7.2h16M4 12h16M4 16.8h10" />,
  list: <path d="M8.4 6.4h11.2M8.4 12h11.2M8.4 17.6h11.2M4.2 6.4h.9M4.2 12h.9M4.2 17.6h.9" />,
  cards: (
    <>
      <rect x="3.4" y="3.6" width="8.2" height="8.2" rx="1.6" />
      <rect x="12.4" y="3.6" width="8.2" height="8.2" rx="1.6" />
      <rect x="3.4" y="12.6" width="8.2" height="8.2" rx="1.6" />
      <rect x="12.4" y="12.6" width="8.2" height="8.2" rx="1.6" />
    </>
  ),
  /* 地球经纬线：外圈 + 中央经线「透镜」+ 赤道横线，语言切换用 */
  languages: (
    <>
      <circle cx="12" cy="12" r="8.3" />
      <path d="M12 3.75c2.5 2.3 3.9 5.15 3.9 8.25s-1.4 5.95-3.9 8.25c-2.5-2.3-3.9-5.15-3.9-8.25s1.4-5.95 3.9-8.25Z" />
      <path d="M3.9 12h16.2" />
    </>
  ),
  'trend-line': <path d="M4.2 17.6 10 12.2 14.2 15.4 19.8 6.4" />,
  'ray-right': (
    <>
      <path d="M4.4 16.8 12.2 9.4" />
      <path d="M12.2 9.4 20.2 5.6" />
      <circle cx="12.2" cy="9.4" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
  channel: (
    <>
      <path d="M4.2 16.2 19.6 8.4" />
      <path d="M4.6 19.2 20 11.4" />
    </>
  ),
  rect: <rect x="5" y="6.2" width="14" height="11.6" rx="1.4" />,
  fib: (
    <>
      <path d="M4.4 6.2h15.2M4.4 10h15.2M4.4 13.2h15.2M4.4 17.6h15.2" />
    </>
  ),
  'text-note': (
    <>
      <path d="M7.2 5.2h9.6v13.6H7.2z" />
      <path d="M9.2 9.2h5.6M9.2 12.4h5.6M9.2 15.6h3.4" />
    </>
  ),
  lock: (
    <>
      <rect x="6.2" y="10.4" width="11.6" height="8.4" rx="1.4" />
      <path d="M8.4 10.4V8.2a3.6 3.6 0 0 1 7.2 0v2.2" />
    </>
  ),
  unlock: (
    <>
      <rect x="6.2" y="10.4" width="11.6" height="8.4" rx="1.4" />
      <path d="M8.4 10.4V8.2a3.6 3.6 0 0 1 6.6-1.4" />
    </>
  ),
  eye: (
    <>
      <path d="M3.6 12S7.2 6.6 12 6.6 20.4 12 20.4 12 16.8 17.4 12 17.4 3.6 12 3.6 12Z" />
      <circle cx="12" cy="12" r="2.2" />
    </>
  ),
  'eye-off': (
    <>
      <path d="M4 5.2 19.6 18.8M9.2 9.4A4.6 4.6 0 0 0 8 12c0 2.2 1.8 3.8 4 3.8 1 0 1.9-.3 2.6-.9" />
      <path d="M3.6 12S7.2 6.6 12 6.6c1.4 0 2.7.4 3.8 1" />
    </>
  ),
  undo: <path d="M8.2 7.4 4.6 11l3.6 3.6M5.2 11h9.4a4.4 4.4 0 1 1 0 8.8H12" />,
  redo: <path d="M15.8 7.4 19.4 11l-3.6 3.6M18.8 11H9.4a4.4 4.4 0 1 0 0 8.8H12" />,
  expand: (
    <>
      <path d="M8.2 4.4H4.4v3.8M15.8 4.4h3.8v3.8M8.2 19.6H4.4v-3.8M15.8 19.6h3.8v-3.8" />
    </>
  ),
  compress: (
    <>
      <path d="M9.6 4.8v4H5.6M14.4 4.8v4h4M9.6 19.2v-4H5.6M14.4 19.2v-4h4" />
    </>
  ),
};

export function Icon({ name, size = 18, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.45}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {PATHS[name]}
    </svg>
  );
}

export default Icon;
