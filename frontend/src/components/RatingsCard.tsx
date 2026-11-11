import { card } from "@/components/ui";
import { formatNumber, formatPercent } from "@/lib/format";
import type { RatingFilter, RatingScale, RatingScaleSummary, RatingSummary } from "@/lib/types";

const trim = (value: number) => Number(value.toFixed(1)).toString();

function describeScale(scale: RatingScale): string {
  switch (scale.type) {
    case "numeric":
      return `Rated ${trim(scale.min)}–${trim(scale.max)}`;
    case "binary":
      return "Rated positive or negative";
    case "labels":
      return `Rated ${scale.labels[0]} to ${scale.labels[scale.labels.length - 1]}`;
  }
}

/** The middle label of a label scale; an average of label positions would mean little. */
function medianLabel(summary: RatingScaleSummary): string {
  const lowestFirst = [...summary.buckets].reverse();
  let seen = 0;
  for (const bucket of lowestFirst) {
    seen += bucket.count;
    if (seen * 2 >= summary.count) return bucket.label;
  }
  return lowestFirst[lowestFirst.length - 1].label;
}

/** Five stars filled to the average, for a 5-point scale, where stars are what readers expect. */
function Stars({ average, max }: { average: number; max: number }) {
  const percent = Math.max(0, Math.min(100, (average / max) * 100));
  return (
    <span className="relative w-fit text-xl leading-none" aria-hidden="true">
      <span className="text-line-strong">★★★★★</span>
      <span className="absolute inset-0 overflow-hidden whitespace-nowrap text-fg" style={{ width: `${percent}%` }}>
        ★★★★★
      </span>
    </span>
  );
}

function Headline({ summary }: { summary: RatingScaleSummary }) {
  const { scale, average } = summary;
  if (scale.type === "numeric") {
    const fivePoint = scale.max === 5 && (scale.min === 0 || scale.min === 1);
    return (
      <div className="flex flex-col gap-1.5">
        <p>
          <span className="font-display text-[40px] leading-none font-semibold">{average.toFixed(1)}</span>{" "}
          <span className="text-muted">out of {trim(scale.max)}</span>
        </p>
        {fivePoint && <Stars average={average} max={5} />}
      </div>
    );
  }
  if (scale.type === "binary") {
    return (
      <p>
        <span className="font-display text-[40px] leading-none font-semibold">{formatPercent(average, 1)}</span>{" "}
        <span className="text-muted">positive</span>
      </p>
    );
  }
  return (
    <p className="flex flex-col">
      <span className="font-display text-[32px] leading-tight font-semibold">{medianLabel(summary)}</span>
      <span className="text-muted">median rating</span>
    </p>
  );
}

const sameScale = (a: RatingScale, b: RatingScale) => JSON.stringify(a) === JSON.stringify(b);

/** One row per bucket. Each row is a toggle: the chosen buckets filter the themes below. */
function Distribution({
  summary,
  chosen,
  onToggle,
}: {
  summary: RatingScaleSummary;
  chosen: string[];
  onToggle: (label: string) => void;
}) {
  return (
    <div className="grid grid-cols-[minmax(2.5rem,max-content)_minmax(0,1fr)_3rem] items-center gap-x-3 gap-y-1">
      {summary.buckets.map((bucket) => {
        const percent = formatPercent(bucket.count, summary.count);
        const selected = chosen.includes(bucket.label);
        const dimmed = chosen.length > 0 && !selected;
        return (
          <button
            key={bucket.label}
            type="button"
            aria-pressed={selected}
            aria-label={`${bucket.label}: ${percent} (${formatNumber(bucket.count)} reviews)`}
            disabled={bucket.count === 0}
            onClick={() => onToggle(bucket.label)}
            className={`col-span-3 grid grid-cols-subgrid items-center rounded-md px-1.5 py-1 text-left text-sm hover:bg-subtle disabled:cursor-default disabled:hover:bg-transparent ${
              selected ? "bg-subtle ring-2 ring-primary" : ""
            } ${dimmed ? "opacity-45" : ""}`}
          >
            <span className="max-w-40 truncate" title={bucket.label}>
              {bucket.label}
            </span>
            <span className="h-3 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
              <span
                className="block h-full rounded-full bg-primary"
                style={{ width: summary.count ? `${(bucket.count * 100) / summary.count}%` : 0 }}
              />
            </span>
            <span className="text-right text-muted" title={`${formatNumber(bucket.count)} reviews`}>
              {percent}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** The ratings at a glance: the average, how many reviews gave one, and how they spread. */
export function RatingsCard({
  ratings,
  filter,
  onFilterChange,
  title = "Ratings",
  hints = { idle: "Select a rating to see its themes.", active: "Themes below count only the selected ratings." },
}: {
  ratings: RatingSummary;
  filter: RatingFilter | null;
  onFilterChange: (filter: RatingFilter | null) => void;
  title?: string;
  /** What selecting a bar does, before and after one is selected. */
  hints?: { idle: string; active: string };
}) {
  if (ratings.scales.length === 0) return null;
  const unrated = ratings.total_reviews - ratings.rated_reviews;
  const several = ratings.scales.length > 1;

  // Buckets are chosen on one scale at a time: choosing on another scale starts over.
  function toggle(scale: RatingScale, label: string) {
    const current = filter && sameScale(filter.scale, scale) ? filter.labels : [];
    const labels = current.includes(label) ? current.filter((item) => item !== label) : [...current, label];
    onFilterChange(labels.length ? { scale, labels } : null);
  }
  return (
    <section className={`${card} flex flex-col gap-5 px-5 py-4`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="text-sm text-muted">
            {filter ? hints.active : hints.idle}
            {unrated > 0 &&
              ` ${formatNumber(unrated)} of ${formatNumber(ratings.total_reviews)} reviews have no rating.`}
          </p>
        </div>
        {filter && (
          <button type="button" onClick={() => onFilterChange(null)} className="py-1 text-sm text-link hover:underline">
            Clear selection
          </button>
        )}
      </div>
      {ratings.scales.map((summary) => (
        <div
          key={JSON.stringify(summary.scale)}
          className="grid gap-5 md:grid-cols-[220px_minmax(0,480px)] md:gap-10"
        >
          <div className="flex flex-col gap-2">
            {several && <p className="text-sm font-semibold">{describeScale(summary.scale)}</p>}
            <Headline summary={summary} />
            <p className="text-sm text-muted">
              {formatNumber(summary.count)} {summary.count === 1 ? "rating" : "ratings"}
            </p>
          </div>
          <Distribution
            summary={summary}
            chosen={filter && sameScale(filter.scale, summary.scale) ? filter.labels : []}
            onToggle={(label) => toggle(summary.scale, label)}
          />
        </div>
      ))}
    </section>
  );
}
