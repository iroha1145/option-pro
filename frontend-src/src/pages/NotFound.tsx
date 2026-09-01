/**
 * 404（GPT-5.6-Pro 审计 P3-6）。
 *
 * 通配路由此前直接 <Navigate to="/watchlist" replace />：输错地址、旧链接失效、
 * 部署缺页都会被静默改写成自选页，用户看不出发生了什么，自动化测试也会因此
 * 掩盖真实的路由问题。这里如实显示地址不存在，并给出返回入口。
 */
import { Link, useLocation } from 'react-router';
import EmptyState from '@/components/shared/EmptyState';
import { t } from '../i18n/core.ts';

export default function NotFound() {
  const location = useLocation();
  return (
    <EmptyState
      icon="search"
      title={t("页面不存在")}
      description={t('没有找到 {path} 对应的页面。链接可能已失效或地址输入有误。', { path: location.pathname })}
      action={
        <Link
          to="/"
          className="btn-primary"
        >
          {t('返回首页')}
        </Link>
      }
      /* 占位是整屏高的（见 PageFallback）。这一页如果只有几百像素，页脚会从
         折线以下反向弹上来 —— 换了个方向的同一种位移。撑到同样高度即可。 */
      className="min-h-[70vh] justify-center py-16"
    />
  );
}
