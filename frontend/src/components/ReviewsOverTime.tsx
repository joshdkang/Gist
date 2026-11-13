"use client";

import { AxisEnd, PeriodAxis, periodName } from "@/components/TrendChart";
import { card, INTERVALS, Toggle } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import { describePeriodsFilter, type PeriodsFilter } from "@/lib/periodFilter";
import type { ReviewPeriods, TrendInterval } from "@/lib/types";

const CHART_HEIGHT = 120;
// Months and quarters keep a readable width; a long history scrolls sideways instead of shrinking.
const MIN_BAR_WIDTH: Record<TrendInterval, string> = { month: "12px", quarter: "16px", year: "0px" };

/**
 * The entity's reviews per period, as bars that choose the period filter, the way a theme's trend
 * chooses the periods of its reviews. The filter (in the page's URL) narrows the theme list to the
 * reviews from the chosen periods. Every period from the first review to the last has a slot, so a
 * gap shows as a gap.
 */
export function ReviewsOverTime({
  data,
  interval,
  onIntervalChange,
  filter,
  onFilterChange,
}: {
  data: ReviewPeriods;
  interval: TrendInterval;
  onIntervalChange: (interval: TrendInterval) => void;
  filter: PeriodsFilter | null;
  onFilterChange: (filter: PeriodsFilter | null) => void;
}) {
  // The data may still be the previous interval's while the new one loads.
  const shown = data.interval;
  const selected = filter && filter.interval === shown ? filter.starts : [];
  const reviews = new Map(data.periods.map((period) => [period.period_start, period.reviews]));
  const slots = everyPeriod(data.periods.map((period) => period.period_start), shown);
  const max = Math.max(1, ...data.periods.map((period) => period.reviews));
  const barStyle = { flex: `1 0 ${MIN_BAR_WIDTH[shown]}`, maxWidth: "48px" };

  function toggle(start: string) {
    const starts = selected.includes(start) ? selected.filter((item) => item !== start) : [...selected, start];
    onFilterChange(starts.length > 0 ? { interval: shown, starts } : null);
  }

  const bars = slots.map((start) => {
    const count = reviews.get(start) ?? 0;
    return { start, count, tip: `${periodName(start, shown)}: ${formatNumber(count)} reviews` };
  });

  return (
    <section className={`${card} flex flex-col gap-3 px-5 py-4`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        {/* Takes the room beside the interval switch, which then wraps under it only when it must. */}
        <div className="min-w-[220px] flex-1">
          <h2 className="text-lg font-semibold">Reviews over time</h2>
          <p className="text-sm text-muted">Select bars to show only the reviews from those periods.</p>
        </div>
        <Toggle label="Interval" options={INTERVALS} value={interval} onChange={onIntervalChange} />
      </div>
      <div
        role="group"
        aria-label={`Reviews per ${shown}, from ${periodName(slots[0], shown)} to ${periodName(slots.at(-1)!, shown)}, up to ${formatNumber(max)}. Select bars to filter the themes.`}
        className="flex gap-2.5"
      >
        <div
          className="flex min-w-8 flex-col justify-between text-right text-xs text-muted"
          style={{ height: CHART_HEIGHT + 8 }}
        >
          <span>{formatNumber(max)}</span>
          <span>0</span>
        </div>
        {/* Right-to-left scrolling opens a long history at its latest period; the content stays left-to-right. */}
        <div className="min-w-0 flex-1 overflow-x-auto" style={{ direction: "rtl" }}>
          <div style={{ direction: "ltr", width: "max-content", minWidth: "100%" }}>
            <div className="flex items-end gap-[3px] border-b border-line-strong pt-2" style={{ height: CHART_HEIGHT + 8 }}>
              {bars.map((bar) => {
                const isSelected = selected.includes(bar.start);
                const dimmed = selected.length > 0 && !isSelected;
                return (
                  <button
                    key={bar.start}
                    type="button"
                    title={bar.tip}
                    aria-label={bar.tip}
                    aria-pressed={isSelected}
                    disabled={bar.count === 0}
                    onClick={() => toggle(bar.start)}
                    className={`flex h-full flex-col justify-end rounded-t-sm enabled:cursor-pointer enabled:hover:bg-subtle ${
                      dimmed ? "opacity-35 hover:opacity-70" : ""
                    }`}
                    style={barStyle}
                  >
                    <span
                      className="block rounded-t-sm bg-link"
                      style={{ height: bar.count ? Math.max(1, Math.round((bar.count / max) * CHART_HEIGHT)) : 0 }}
                    />
                  </button>
                );
              })}
              <AxisEnd interval={shown} />
            </div>
            <PeriodAxis starts={slots} interval={shown} selected={selected} barStyle={barStyle} />
          </div>
        </div>
      </div>
      {filter && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="font-semibold">
            Showing reviews {describePeriodsFilter(filter)} (
            {formatNumber(filter.starts.reduce((sum, start) => sum + (reviews.get(start) ?? 0), 0))} reviews)
          </span>
          <button type="button" onClick={() => onFilterChange(null)} className="text-link hover:underline">
            Show all periods
          </button>
        </div>
      )}
    </section>
  );
}

/** Every period start from the first to the last, including empty ones, as date_trunc writes them. */
function everyPeriod(starts: string[], interval: TrendInterval): string[] {
  if (starts.length === 0) return [];
  const step = { month: 1, quarter: 3, year: 12 }[interval];
  const first = new Date(starts[0]);
  const last = new Date(starts.at(-1)!);
  const slots: string[] = [];
  // Keep the API's own spelling for periods it returned, so a slot matches its count.
  const given = new Map(starts.map((start) => [new Date(start).getTime(), start]));
  for (let at = first; at <= last; at = new Date(Date.UTC(at.getUTCFullYear(), at.getUTCMonth() + step, 1))) {
    slots.push(given.get(at.getTime()) ?? at.toISOString());
  }
  return slots;
}
