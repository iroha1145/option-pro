/**
 * 顶级错误边界（GPT-5.6-Pro 审计 P1-09）。
 *
 * RouteErrorBoundary 只包住 <Outlet />，因此它捕不到路由之外、却始终挂载的东西：
 * AccessProvider、全局命令面板、全局股票抽屉、Toast。这些位置一旦抛异常就是整站
 * 白屏 —— 一个损坏的 localStorage 值即可触发。这一层放在最外面兜底。
 *
 * 与路由边界的区别：这里不假设 Layout 的样式类可用（崩溃可能就发生在 Layout 之前），
 * 所以用内联样式渲染，保证任何情况下都能显示出来。
 */
import { Component, type ReactNode } from 'react';
import { t } from '../../i18n/core.ts';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error('[AppErrorBoundary]', error, info.componentStack ?? '');
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          margin: '10vh auto 0',
          maxWidth: '34rem',
          padding: '1.5rem',
          borderRadius: '12px',
          border: '1px solid rgba(200, 60, 60, .25)',
          background: '#fffaf9',
          color: '#1b1b1f',
          fontFamily:
            'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
          textAlign: 'center',
        }}
      >
        <p style={{ fontSize: '18px', fontWeight: 600 }}>{t('应用启动中断')}</p>
        <p
          style={{
            marginTop: '.5rem',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontSize: '12px',
            color: '#6b6b76',
            wordBreak: 'break-all',
          }}
        >
          {error.name}: {error.message}
        </p>
        <p style={{ marginTop: '.25rem', fontSize: '12px', color: '#8a8a95' }}>
          {t('已拦截为错误卡以避免白屏；完整堆栈保留在控制台。若反复出现，可清除本站的本地存储后重试。')}
        </p>
        <div
          style={{
            marginTop: '1rem',
            display: 'flex',
            gap: '.5rem',
            justifyContent: 'center',
          }}
        >
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              borderRadius: '8px',
              border: 'none',
              background: '#2e46e0',
              color: '#fff',
              padding: '.5rem 1rem',
              fontSize: '13px',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            {t('重新加载')}
          </button>
          <button
            type="button"
            onClick={() => {
              try {
                localStorage.clear();
              } catch {
                /* 存储不可用时忽略 */
              }
              window.location.reload();
            }}
            style={{
              borderRadius: '8px',
              border: '1px solid #d8d8de',
              background: '#fff',
              color: '#4a4a55',
              padding: '.5rem 1rem',
              fontSize: '13px',
              cursor: 'pointer',
            }}
          >
            {t('清除本地存储并重载')}
          </button>
        </div>
      </div>
    );
  }
}
