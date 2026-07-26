/**
 * 路由级懒加载占位：Paper 皮肤极简 spinner，保持壳层稳定不闪白。
 *
 * 高度不是随便定的。Lighthouse 的 layout-shifts 审计把 CLS 0.186 归到了
 * <footer>：占位只有 40vh（329px），加上页头与指数带约 340px，页脚正好落在
 * y=670 —— 823px 视口内还看得见。路由 chunk 一到、真实页面挂上，页脚被推到
 * y=5737，这一次可见位移就是该页几乎全部的 CLS，而且每个路由都会发生。
 *
 * 占位撑满一屏，页脚在加载期间就位于折线以下，之后再怎么下移都不计入 CLS。
 * 代价是快速加载时会短暂看到一段空白，但它随即被更高的真实内容取代，
 * 屏幕上不会跳动 —— 这正是想要的结果。
 *
 * **只用于路由级 Suspense。** 抽屉、面板等局部占位用 InlineFallback：那里没有页脚、
 * 也没有要压的位移，整屏高度只会白撑出一大片空白。
 */
export default function PageFallback() {
  return (
    <div
      className="flex min-h-screen items-start justify-center pt-[20vh]"
      role="status"
      aria-label="页面加载中"
    >
      <span
        className="size-5 animate-spin rounded-full border-2 border-line border-t-brand-600"
        aria-hidden="true"
      />
      <span className="ml-2.5 text-caption text-ink-400">加载中…</span>
    </div>
  );
}
