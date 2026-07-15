import type { ReactNode } from "react";

type SectionEmptyStateProps = {
  message: ReactNode;
};

export default function SectionEmptyState({ message }: SectionEmptyStateProps) {
  return (
    <div className="flex min-h-32 items-center justify-center rounded-md border border-dashed border-hairline bg-surface px-4 py-6">
      <p className="text-sm text-slate-400">{message}</p>
    </div>
  );
}
