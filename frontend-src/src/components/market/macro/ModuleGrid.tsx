/**
 * 七模块网格 — 320/390 单列 · sm 两列 · lg 四列；最后一行自然排列，不强求七等分，
 * 也不允许横向滚动。
 */
import ModuleCard from './ModuleCard';
import type { MacroModule } from '@/api/modules/macro';

export default function ModuleGrid({ modules }: { modules: MacroModule[] }) {
  if (!modules.length) {
    return (
      <p className="card-surface p-5 text-body-s text-ink-500">
        暂无模块分数。数据接入后这里会显示七个模块。
      </p>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {modules.map((module, index) => (
        <ModuleCard key={module.moduleId} module={module} index={index} />
      ))}
    </div>
  );
}
