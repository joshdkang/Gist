import { formatNumber } from "@/lib/format";
import { isActive } from "@/lib/queries";
import type { RunDetail, Step, StepKind } from "@/lib/types";

const STEP_NAMES: Record<StepKind, string> = {
  discover: "Discover",
  classify: "Classify",
  confirm: "Confirm",
  consolidate: "Consolidate",
  promote: "Promote",
};

function describeStep(step: Step): string {
  switch (step.kind) {
    case "discover":
      return "Discovering themes from a sample of reviews";
    case "classify":
      return `Classifying reviews: ${formatNumber(step.reviews_processed)} done so far`;
    case "confirm":
      return "Checking the new themes against every review";
    case "consolidate":
      return "Merging overlapping emerging topics";
    case "promote":
      return "Promoting emerging topics that reached 4% of reviews";
  }
}

const STATUS_MARK = { running: "●", succeeded: "✓", failed: "✕", skipped: "–" } as const;

/** Shows a queued or running analysis, which step it is on, and a failed run's error. */
export function RunBanner({ run }: { run: RunDetail | null }) {
  if (!run) return null;
  if (run.status === "failed") {
    return (
      <section className="rounded-xl border border-warn-line bg-warn-bg px-5 py-4">
        <p className="font-semibold">The last analysis failed.</p>
        {run.error && <p className="mt-1 text-sm">{run.error}</p>}
        <p className="mt-1 text-sm text-muted">The results below are from before it. A new upload tries again.</p>
      </section>
    );
  }
  if (!isActive(run)) return null;

  const current = run.steps.findLast((step) => step.status === "running");
  const headline =
    run.status === "queued"
      ? "Analysis queued. It starts when a worker is free."
      : current
        ? describeStep(current)
        : "Analysis running: checking what this entity needs";

  return (
    <section
      aria-live="polite"
      className="flex flex-col gap-3 rounded-xl border border-info-line bg-info-bg px-5 py-4"
    >
      <p className="font-semibold">{headline}</p>
      {run.steps.length > 0 && (
        <ol className="flex flex-wrap gap-2 text-[13px]">
          {run.steps.map((step) => (
            <li
              key={step.id}
              className={`rounded-full border bg-surface px-2.5 py-0.5 ${
                step.status === "running" ? "border-pos font-semibold" : "border-info-line text-muted"
              }`}
            >
              {STATUS_MARK[step.status]} {STEP_NAMES[step.kind]} · {step.status}
            </li>
          ))}
        </ol>
      )}
      <p className="text-[13px] text-muted">
        Results below are from the last finished run and update when this one ends. You can leave this page.
      </p>
    </section>
  );
}
