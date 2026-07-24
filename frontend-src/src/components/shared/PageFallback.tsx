/** 路由级懒加载占位：Paper 皮肤极简 spinner，保持壳层稳定不闪白 */
export default function PageFallback() {
  return (
    <div
      className="flex min-h-[40vh] items-center justify-center"
      role="status"
      aria-label="页面加载中"
    >
      <span
        className="size-5 animate-spin rounded-full border-2 border-line border-t-brand-600"
        aria-hidden="true"
      />
    </div>
  );
}
