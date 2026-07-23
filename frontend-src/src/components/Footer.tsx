/** Footer：发丝线 + 来源注（design.md §0 原则 1 / §7 Layout） */
import SourceNote from '@/components/shared/SourceNote';

export default function Footer() {
  return (
    <footer className="mx-auto mt-16 w-full max-w-shell px-4 pb-[calc(6rem+env(safe-area-inset-bottom))] md:px-8 md:pb-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SourceNote className="flex-1" text="来源：Optix Research · 延迟行情，不构成投资建议" />
        <p className="font-mono text-micro text-ink-300">OPTIX PRO · PAPER TERMINAL v2</p>
      </div>
    </footer>
  );
}
