import { Component, type ReactNode } from 'react';
import { t } from '../../i18n/core.ts';

interface Props {
  children: ReactNode;
  onRetry?: () => void;
}

interface State {
  error: Error | null;
}

/**
 * 只兜住 EPS 图表懒加载失败，不把财报列表/分析区卸掉。
 * 重试由父组件换一个新的 lazy() 工厂，并用新的模块 URL 绕开浏览器对失败模块的缓存。
 */
export default class ChartLoadErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error('[ChartLoadErrorBoundary]', error, info.componentStack ?? '');
  }

  private retry = () => {
    this.setState({ error: null });
    this.props.onRetry?.();
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section
        className="card-surface p-5"
        style={{ minHeight: 320 }}
        data-eps-chart-error=""
        aria-label={t('EPS 图表加载失败')}
      >
        <p className="text-caption text-ink-600">{t('EPS 图表加载失败')}</p>
        <p className="mt-1 text-micro text-ink-400">{t('列表与分析仍可查看。可单独重试图表。')}</p>
        <button
          type="button"
          onClick={this.retry}
          className="mt-3 rounded-md border border-line px-3 py-1.5 text-caption text-ink-600 hover:border-brand-400 hover:text-brand-600"
        >
          {t('重试图表')}
        </button>
      </section>
    );
  }
}
