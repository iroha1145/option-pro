/** 页面占位（stub）：页头带 + 建设提示；由对应页面代理按 design 文档完整实现 */
import PageHeader from '@/components/shared/PageHeader';
import Icon, { type IconName } from '@/components/icons';

interface PageStubProps {
  section: string;
  eyebrow: string;
  title: string;
  description: string;
  icon: IconName;
  hint: string;
}

export default function PageStub({ section, eyebrow, title, description, icon, hint }: PageStubProps) {
  return (
    <div>
      <PageHeader section={section} eyebrow={eyebrow} title={title} description={description} />
      <div className="mt-8 flex flex-col items-center rounded-lg border border-dashed border-line-strong bg-card-warm px-6 py-16 text-center">
        <span className="flex size-14 items-center justify-center rounded-lg border border-line bg-card text-brand-600">
          <Icon name={icon} size={26} />
        </span>
        <h3 className="mt-4 text-h3 text-ink-800">{title} · 建设中</h3>
        <p className="mt-1.5 max-w-[380px] text-body-s text-ink-500">{hint}</p>
        <p className="mt-3 font-mono text-micro text-ink-300">§{section} · 设计文档已就绪，数据层已对接 mock</p>
      </div>
    </div>
  );
}
