/** Footer：发丝线 + 来源注（design.md §0 原则 1 / §7 Layout） */
import SourceNote from '@/components/shared/SourceNote';

export default function Footer() {
  return (
    <footer className="mx-auto mt-16 w-full max-w-shell px-4 pb-[calc(6rem+env(safe-area-inset-bottom))] md:px-8 md:pb-10">
      <div className="flex flex-col items-start gap-3 min-[360px]:flex-row min-[360px]:flex-wrap min-[360px]:items-center min-[360px]:justify-between">
        <SourceNote
          className="w-full min-[360px]:w-auto min-[360px]:min-w-0 min-[360px]:flex-1"
          text="来源：Optix Research · 延迟行情，不构成投资建议"
        />
        <p className="max-w-full break-words font-mono text-micro text-ink-300">
          OPTIX PRO · PAPER TERMINAL v2
        </p>
      </div>
    </footer>
  );
}
