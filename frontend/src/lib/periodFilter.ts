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

/** A search in a URL: `q`, the parameter the API takes. */
export function appendSearch(params: URLSearchParams, search: string | null): URLSearchParams {
  if (search) params.set("q", search);
  return params;
}

/** The search in a page's URL, or null when there is none. */
export function readSearch(params: { get(name: string): string | null }): string | null {
  return params.get("q")?.trim() || null;
}

/** 'mentioning "valet"': a search, as the filter descriptions name it. */
export const describeSearch = (search: string) => `mentioning “${search}”`;

/** "?interval=…&period=…&rating_scale=…&rating=…&q=…" for the filters given, or "" with none. */
export function filterQuery(
  ratingFilter: RatingFilter | null,
  periodsFilter: PeriodsFilter | null,
  search: string | null = null,
): string {
  const params = appendSearch(
    appendRatingFilter(appendPeriodsFilter(new URLSearchParams(), periodsFilter), ratingFilter),
    search,
  );
  return params.size > 0 ? `?${params}` : "";
}

/** 'from 2019 Q1 rated 1 mentioning "valet"': the filters in force, or "" with none. */
export function describeFilters(
  ratingFilter: RatingFilter | null,
  periodsFilter: PeriodsFilter | null,
  search: string | null = null,
): string {
  return [
    periodsFilter && describePeriodsFilter(periodsFilter),
    ratingFilter && describeRatingFilter(ratingFilter),
    search && describeSearch(search),
  ]
    .filter(Boolean)
    .join(" ");
}

/** The start of the period after `start` (an ISO period start, as the API writes them). */
function nextStart(start: string, interval: TrendInterval): number {
  const date = new Date(start);
  const months = { month: 1, quarter: 3, year: 12 }[interval];
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1);
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * A run of consecutive periods in words, as short as it reads clearly: whole years by their year
 * ("2020", "2019–2020"), and the year said once when the run stays in it ("2020 Q1–Q3", "Jan–Mar 2020").
 */
function runName(first: string, last: string, interval: TrendInterval): string {
  const from = new Date(first), to = new Date(last);
  const [y1, m1, y2, m2] = [from.getUTCFullYear(), from.getUTCMonth(), to.getUTCFullYear(), to.getUTCMonth()];
  const years = y1 === y2 ? `${y1}` : `${y1}–${y2}`;
  const wholeYears = interval === "year" || (m1 === 0 && m2 === (interval === "quarter" ? 9 : 11));
  if (wholeYears) return years;
  if (interval === "quarter") {
    const [q1, q2] = [Math.floor(m1 / 3) + 1, Math.floor(m2 / 3) + 1];
    if (y1 !== y2) return `${y1} Q${q1}–${y2} Q${q2}`;
    return q1 === q2 ? `${y1} Q${q1}` : `${y1} Q${q1}–Q${q2}`;
  }
  if (y1 !== y2) return `${MONTH_NAMES[m1]} ${y1}–${MONTH_NAMES[m2]} ${y2}`;
  return m1 === m2 ? `${MONTH_NAMES[m1]} ${y1}` : `${MONTH_NAMES[m1]}–${MONTH_NAMES[m2]} ${y1}`;
}

/**
 * The chosen periods in words, each run of consecutive periods as a range: "from 2019 Q1",
 * "from 2018 Q1–2019 Q4", "from 2020 and 2021 Q2", "from 2012–2013 and 2019–2020", or
 * "from 5 ranges, 2012 to 2021".
 */
export function describePeriodsFilter(filter: PeriodsFilter): string {
  const starts = [...filter.starts].sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
  const runs: [string, string][] = [];
  for (const start of starts) {
    const run = runs.at(-1);
    if (run && nextStart(run[1], filter.interval) === new Date(start).getTime()) run[1] = start;
    else runs.push([start, start]);
  }
  const names = runs.map(([first, last]) => runName(first, last, filter.interval));
  if (names.length === 1) return `from ${names[0]}`;
  if (names.length <= 3) return `from ${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  const name = (start: string) => periodName(start, filter.interval);
  return `from ${names.length} ranges, ${name(runs[0][0])} to ${name(runs.at(-1)![1])}`;
}
