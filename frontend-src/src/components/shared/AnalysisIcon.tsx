import type { SVGProps } from 'react';
import { cn } from '@/lib/utils';

/** A folded report with a trend line: a readable analysis mark at button size. */
export default function AnalysisIcon({
  size = 16, className, ...props
}: SVGProps<SVGSVGElement> & { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={cn('shrink-0', className)}
      {...props}
    >
      <path d="M13.5 3.5H6.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-9l-6-6Z" />
      <path d="M13.5 3.5v6h6M8 8h2M8 16.5l3-3 2.5 2 3-4" />
    </svg>
  );
}
