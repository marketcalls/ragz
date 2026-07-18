// STUB(phase2-quotas): hardcoded sample values until the quotas module lands.
// Visibly labeled so nobody mistakes it for live data.
export function UsageMeter() {
  return (
    <span
      className="text-[12px] tabular-nums text-muted"
      title="Sample data — usage tracking arrives with Phase 2 quotas"
    >
      12.3k / 100k tokens (sample)
    </span>
  );
}
