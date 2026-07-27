import * as React from "react"

const MOBILE_BREAKPOINT = 768

export function useIsMobile() {
  // 同步取初值（审计 2.4.4）：以前初值是 undefined、effect 里才写真值，手机上
  // 首帧一律按桌面渲染，K 线等按此取高的布局会在 effect 后跳一次（60px CLS）。
  // SPA 无 SSR，直接读 matchMedia 是安全的。
  const [isMobile, setIsMobile] = React.useState<boolean>(() =>
    typeof window !== "undefined"
      ? window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`).matches
      : false,
  )

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    }
    mql.addEventListener("change", onChange)
    setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    return () => mql.removeEventListener("change", onChange)
  }, [])

  return isMobile
}
