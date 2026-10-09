import { useState } from 'react';

/* 2026-10-09：滑块的 layoutId 每个控件实例一个新标识。
   不用 useId：它只由组件在树里的位置决定，换页后新页面同位置的控件拿到同一个标识，
   动画库把它当成同一个滑块，从旧页面的位置滑过来（用户反馈「先出现在附近再移到正确位置」）。 */
let glideSerial = 0;
export function useGlideLayoutId(prefix = 'glide'): string {
  const [id] = useState(() => `${prefix}-${++glideSerial}`);
  return id;
}
