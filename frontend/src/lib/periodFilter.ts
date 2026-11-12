import { periodName } from "@/components/TrendChart";
import { INTERVALS } from "@/components/ui";
import { appendRatingFilter, describeRatingFilter } from "@/lib/ratingFilter";
import type { RatingFilter, TrendInterval } from "@/lib/types";

/** Periods chosen on a reviews-over-time chart: their period_start values on one interval. */
export interface PeriodsFilter {
  interval: TrendInterval;
  starts: string[];
}

/**
 * A period filter in a URL: `interval` and each period in a repeated `period`, the parameters the API
 * takes and the theme page reads for its trend, so the filter carries from page to page and to the API.
 */
export function appendPeriodsFilter(params: URLSearchParams, filter: PeriodsFilter | null): URLSearchParams {
  if (filter && filter.starts.length > 0) {
    params.set("interval", filter.interval);
    for (const start of filter.starts) params.append("period", start);
  }
  return params;
}

/** The filter in a page's URL, or null when it names no period or no known interval. */
export function readPeriodsFilter(params: { get(name: string): string | null; getAll(name: string): string[] }) {
  const starts = params.getAll("period");
  const interval = INTERVALS.find((option) => option.key === params.get("interval"))?.key;
  return starts.length > 0 && interval ? ({ interval, starts } satisfies PeriodsFilter) : null;
}

/** "?interval=…&period=…&rating_scale=…&rating=…" for both filters, or "" with neither. */
export function filterQuery(ratingFilter: RatingFilter | null, periodsFilter: PeriodsFilter | null): string {
  const params = appendRatingFilter(appendPeriodsFilter(new URLSearchParams(), periodsFilter), ratingFilter);
  return params.size > 0 ? `?${params}` : "";
}

/** "from 2019 Q1 rated 1": the filters in force, or "" with neither. */
export function describeFilters(ratingFilter: RatingFilter | null, periodsFilter: PeriodsFilter | null): string {
  return [periodsFilter && describePeriodsFilter(periodsFilter), ratingFilter && describeRatingFilter(ratingFilter)]
    .filter(Boolean)
    .join(" ");
}

/** "from 2019 Q1", "from 2019 Q1 and 2019 Q3", or "from 6 quarters, 2018 Q1 to 2019 Q4". */
export function describePeriodsFilter(filter: PeriodsFilter): string {
  const names = [...filter.starts].sort().map((start) => periodName(start, filter.interval));
  if (names.length === 1) return `from ${names[0]}`;
  if (names.length <= 3) return `from ${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  return `from ${names.length} ${filter.interval}s, ${names[0]} to ${names.at(-1)}`;
}
