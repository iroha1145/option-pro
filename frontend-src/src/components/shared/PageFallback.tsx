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
 * **只用于路由级 Suspense。** 抽屉、面板等局部占位不要复用整屏高度，
 * 否则会先撑出空白并多出一条没必要的滚动条。
 */
import MatrixLoader from '@/components/shared/MatrixLoader';
import { t } from '../../i18n/core.ts';
export default function PageFallback() {
  return (
    <div
      className="flex min-h-screen items-start justify-center pt-[20vh]"
      role="status"
      aria-label={t("页面加载中")}
    >
      {/* transitions.dev 31 点阵扫描：分包通常几百毫秒就到，转圈在这个时长里
          只会一闪而过、显得焦躁；点阵是「纸面终端」语汇里更安静的等待信号。 */}
      <span className="flex items-center">
        <MatrixLoader variant="scan" />
      </span>
      <span className="ml-2.5 font-mono text-caption text-ink-400">{t('加载中…')}</span>
    </div>
  );
}
