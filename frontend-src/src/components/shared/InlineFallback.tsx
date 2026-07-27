/**
 * 局部懒加载占位（抽屉、面板内部）。
 *
 * 和 PageFallback 分开是因为两者要解决的是相反的问题。PageFallback 必须撑满一屏，
 * 否则页脚会在路由 chunk 到达前落在折线以上，真实页面挂上时那次下移就是整页的 CLS。
 * 抽屉里没有页脚，也没有那个位移可言 —— 把整屏高度搬进抽屉只会先撑出一大片空白，
 * 还可能让抽屉内部多出一条没必要的滚动条。
 */
import { t } from '../../i18n/core.ts';

export default function InlineFallback() {
  return (
    <div
      className="flex items-center justify-center py-16"
      role="status"
      aria-label={t('加载中')}
    >
      <span
        className="size-5 animate-spin rounded-full border-2 border-line border-t-brand-600"
        aria-hidden="true"
      />
      <span className="ml-2.5 text-caption text-ink-400">{t('加载中…')}</span>
    </div>
  );
}
