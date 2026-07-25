/**
 * 404（GPT-5.6-Pro 审计 P3-6）。
 *
 * 通配路由此前直接 <Navigate to="/watchlist" replace />：输错地址、旧链接失效、
 * 部署缺页都会被静默改写成自选页，用户看不出发生了什么，自动化测试也会因此
 * 掩盖真实的路由问题。这里如实显示地址不存在，并给出返回入口。
 */
import { Link, useLocation } from 'react-router';
import EmptyState from '@/components/shared/EmptyState';

export default function NotFound() {
  const location = useLocation();
  return (
    <EmptyState
      icon="search"
      title="页面不存在"
      description={`没有找到 ${location.pathname} 对应的页面。链接可能已失效或地址输入有误。`}
      action={
        <Link
          to="/watchlist"
          className="inline-flex min-h-11 items-center justify-center rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
        >
          返回自选
        </Link>
      }
      className="py-16"
    />
  );
}
