import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

/* tailwind.config.js 的自定义字阶不在 tailwind-merge 的默认字号表里，会被当成文字颜色：
   cn('text-micro', 'text-ink-400') 只剩颜色，字号退回继承值。登记后两组互不覆盖。 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{
        text: ["display-xl", "display-l", "display-m", "h2", "h3", "body", "body-s", "caption", "eyebrow", "data-xxl", "data-xl", "data-l", "data-m", "micro"],
      }],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** 导航高亮：根路径精确匹配；其余按路径段边界，避免 `/cta` 误亮 `/catalysts`。 */
export function isNavPathActive(pathname: string, path: string): boolean {
  if (path === '/') return pathname === '/'
  return pathname === path || pathname.startsWith(`${path}/`)
}
