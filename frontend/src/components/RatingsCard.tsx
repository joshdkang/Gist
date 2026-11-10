import { card } from "@/components/ui";
import { formatNumber, formatPercent } from "@/lib/format";
import type { RatingScale, RatingScaleSummary, RatingSummary } from "@/lib/types";

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

function Distribution({ summary }: { summary: RatingScaleSummary }) {
  return (
    <ul className="grid grid-cols-[minmax(2.5rem,max-content)_minmax(0,1fr)_3rem] items-center gap-x-3 gap-y-2">
      {summary.buckets.map((bucket) => {
        const percent = formatPercent(bucket.count, summary.count);
        return (
          <li key={bucket.label} className="contents text-sm">
            <span className="max-w-40 truncate" title={bucket.label}>
              {bucket.label}
            </span>
            <span
              className="h-3 overflow-hidden rounded-full bg-subtle"
              role="img"
              aria-label={`${bucket.label}: ${percent} (${formatNumber(bucket.count)} reviews)`}
            >
              <span
                className="block h-full rounded-full bg-primary"
                style={{ width: summary.count ? `${(bucket.count * 100) / summary.count}%` : 0 }}
              />
            </span>
            <span className="text-right text-muted" title={`${formatNumber(bucket.count)} reviews`}>
              {percent}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** The ratings at a glance: the average, how many reviews gave one, and how they spread. */
export function RatingsCard({ ratings }: { ratings: RatingSummary }) {
  if (ratings.scales.length === 0) return null;
  const unrated = ratings.total_reviews - ratings.rated_reviews;
  const several = ratings.scales.length > 1;
  return (
    <section className={`${card} flex flex-col gap-5 px-5 py-4`}>
      <div>
        <h2 className="text-lg font-semibold">Ratings</h2>
        {unrated > 0 && (
          <p className="text-sm text-muted">
            {formatNumber(unrated)} of {formatNumber(ratings.total_reviews)} reviews have no rating.
          </p>
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
          <Distribution summary={summary} />
        </div>
      ))}
    </section>
  );
}
