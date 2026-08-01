/**
 * /stock/:ticker 深链整页形态（stock-detail.md S3）
 * PageHeader 页头带（眉题 STOCK · $T）+ 返回按钮（历史回退，无历史时回自选）+ 与抽屉共用的 StockDrawerBody（layout="page"）
 * 代码不存在 → 组件内 404 空态「返回自选」
 */
import { useNavigate, useParams } from 'react-router';
import StockDrawerBody from '@/components/StockDrawerBody';
import PageHeader from '@/components/shared/PageHeader';
import Icon from '@/components/icons';
import { t as __t } from '../i18n/core.ts';

export default function StockDetail() {
  const { ticker = '' } = useParams();
  const t = ticker.toUpperCase();
  const navigate = useNavigate();

  /* 返回：优先浏览器历史回退（回到雷达/板块/财报等来源页），无入站历史时回自选。
     用 react-router 写入 history.state 的 idx 判断是否有可回退的站内历史（history.length 在新标签页不可靠）。 */
  const goBack = () => {
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    if (idx > 0) navigate(-1);
    else navigate('/watchlist', { replace: true });
  };

  return (
    <div>
      <PageHeader
        section="STK"
        eyebrow={`STOCK · $${t}`}
        title={t}
        description={__t("行情、技术信号、期权链与相关新闻的全上下文视图 · 数据延迟 15 分钟")}
        meta={
          <button
            type="button"
            onClick={goBack}
            className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-card px-3 py-1.5 text-caption font-medium text-ink-600 shadow-btn transition-colors duration-fast hover:bg-paper-2 hover:text-ink-800"
          >
            <Icon name="chevron-right" size={14} className="rotate-180" />
            {__t('返回')}
          </button>
        }
      />
      <div className="mt-8">
        <StockDrawerBody ticker={t} layout="page" />
      </div>
    </div>
  );
}
