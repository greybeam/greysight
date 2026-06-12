type SectionEmptyStateProps = {
  message: string;
};

export default function SectionEmptyState({ message }: SectionEmptyStateProps) {
  return (
    <div className="flex min-h-32 items-center justify-center rounded-md border border-dashed border-slate-200 bg-slate-50 px-4 py-6">
      <p className="text-sm text-slate-500">{message}</p>
    </div>
  );
}
