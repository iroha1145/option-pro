/**
 * 路由级错误边界:任何页面渲染崩溃都不再白屏,
 * 显示诚实错误卡(含异常信息)+ 重载按钮,并保留 console 原始堆栈。
 */
import { Component, type ReactNode } from 'react';
import { t } from '../../i18n/core.ts';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    // 保留完整堆栈供排查;边界只负责兜底展示
    console.error('[RouteErrorBoundary]', error, info.componentStack ?? '');
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="mx-auto mt-10 max-w-xl rounded-lg border border-down-600/25 bg-down-50/60 p-6 text-center">
        <p className="font-display text-[18px] font-semibold text-ink-900">{t('页面渲染中断')}</p>
        <p className="mt-2 break-all font-mono text-micro text-ink-500">
          {this.state.error.name}: {this.state.error.message}
        </p>
        <p className="mt-1 text-micro text-ink-400">{t('已拦截为错误卡以避免白屏；重新加载即可恢复，完整堆栈保留在控制台。')}</p>
        <button
          onClick={() => window.location.reload()}
          className="mt-4 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
        >
          {t('重新加载')}
        </button>
      </div>
    );
  }
}
