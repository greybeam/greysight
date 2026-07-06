type SectionIdleStateProps = {
  // Optional section-specific subtext; defaults to the generic run-analysis CTA.
  message?: string;
};

// Static (no animation) placeholder shown for a Snowflake section before the
// user has started their first analysis run. Deliberately carries NO
// `animate-pulse`/shimmer so an idle dashboard is never mistaken for a loading
// one. Points the user at the "Run analysis" button in the header.
export default function SectionIdleState({ message }: SectionIdleStateProps) {
  return (
    <div
      data-testid="section-idle"
      className="flex min-h-48 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-hairline bg-surface px-6 py-10 text-center"
    >
      <p className="text-sm font-semibold text-slate-200">
        No cached run available.
      </p>
      <p className="text-sm text-slate-400">
        {message ?? 'Select "Run analysis" to load your data.'}
      </p>
    </div>
  );
}
