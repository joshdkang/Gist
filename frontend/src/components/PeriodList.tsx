"use client";

import { useState } from "react";

import { periodName } from "@/components/TrendChart";
import { INTERVALS } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { PeriodsFilter } from "@/lib/periodFilter";
import type { TrendInterval } from "@/lib/types";

// Years shown before "Show earlier years": more when each is one row.
const YEARS_SHOWN: Record<TrendInterval, number> = { year: 8, quarter: 3, month: 3 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A period start as the API reads it, from a UTC year and month (0-based). */
const startOf = (year: number, month: number) => new Date(Date.UTC(year, month, 1)).toISOString();
const timeOf = (start: string) => new Date(start).getTime();

/**
 * The period filter as a checklist, newest year first, with each period's reviews under the page's
 * other filters. A Month, Quarter or Year switch sets what is listed: years as rows, or each year as a
 * heading over a compact grid of its quarters (one row) or months (three rows of four). Any mix of
 * periods can be ticked; a year's checkbox ticks all its quarters or months. Older years wait behind
 * "Show earlier years". Switching the interval clears the choice, since a quarter's start is not a month's.
 */
export function PeriodList({
  periods,
  interval,
  onIntervalChange,
  filter,
  onFilter,
}: {
  /** Reviews per period on `interval` under the page's other filters (periods without any left out). */
  periods: { period_start: string; reviews: number }[];
  interval: TrendInterval;
  onIntervalChange: (interval: TrendInterval) => void;
  filter: PeriodsFilter | null;
  onFilter: (filter: PeriodsFilter | null) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const counts = new Map(periods.map((period) => [timeOf(period.period_start), period.reviews]));
  const chosen = new Set(filter && filter.interval === interval ? filter.starts.map(timeOf) : []);
  const years = [...new Set([...counts.keys(), ...chosen].map((time) => new Date(time).getUTCFullYear()))].sort((a, b) => b - a);
  const shown = showAll ? years : years.slice(0, YEARS_SHOWN[interval]);
  const hidden = years.length - shown.length;

  function choose(times: Set<number>) {
    const starts = [...times].sort((a, b) => a - b).map((time) => new Date(time).toISOString());
    onFilter(starts.length ? { interval, starts } : null);
  }

  function toggle(times: number[], on: boolean) {
    const next = new Set(chosen);
    for (const time of times) {
      if (on) next.delete(time);
      else next.add(time);
    }
    choose(next);
  }

  /** A year's quarters or months, every one of them, so the grid reads Q1 to Q4 or Jan to Dec. */
  function partsOf(year: number): { start: string; label: string }[] {
    if (interval === "quarter") return [0, 3, 6, 9].map((month, index) => ({ start: startOf(year, month), label: `Q${index + 1}` }));
    return MONTHS.map((label, month) => ({ start: startOf(year, month), label }));
  }

  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between gap-2 px-4 pt-3 pb-1.5">
        <h2 className="text-sm font-semibold">Period</h2>
        <div role="group" aria-label="Interval" className="flex overflow-hidden rounded-md border border-line-strong">
          {INTERVALS.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={interval === option.key}
              onClick={() => {
                if (option.key === interval) return;
                onIntervalChange(option.key);
                if (filter) onFilter(null);
              }}
              className={`px-2 py-0.5 text-xs ${interval === option.key ? "bg-primary text-on-primary" : "bg-surface text-fg"}`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <div className="px-2 pb-2">
        {years.length === 0 && <p className="px-2 py-1 text-sm text-muted">No dated reviews match the other filters.</p>}
        {shown.map((year) => {
          if (interval === "year") {
            const time = timeOf(startOf(year, 0));
            const count = counts.get(time) ?? 0;
            const on = chosen.has(time);
            return (
              <label
                key={year}
                className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-subtle ${count === 0 && !on ? "opacity-45" : ""}`}
              >
                <input type="checkbox" checked={on} onChange={() => toggle([time], on)} className="size-4 shrink-0" />
                <span className="flex-1">{year}</span>
                <span className="text-muted tabular-nums">{formatNumber(count)}</span>
              </label>
            );
          }
          const parts = partsOf(year).map((part) => ({ ...part, time: timeOf(part.start) }));
          // The year's checkbox stands for its periods that have reviews, or are ticked.
          const present = parts.filter((part) => (counts.get(part.time) ?? 0) > 0 || chosen.has(part.time)).map((part) => part.time);
          const allOn = present.length > 0 && present.every((time) => chosen.has(time));
          const total = parts.reduce((sum, part) => sum + (counts.get(part.time) ?? 0), 0);
          return (
            <div key={year}>
              <label
                className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm font-semibold hover:bg-subtle ${total === 0 && !allOn ? "opacity-45" : ""}`}
              >
                <input type="checkbox" checked={allOn} onChange={() => toggle(present, allOn)} className="size-4 shrink-0" />
                <span className="flex-1">{year}</span>
                <span className="font-normal text-muted tabular-nums">{formatNumber(total)}</span>
              </label>
              <div
                role="group"
                aria-label={`${interval === "quarter" ? "Quarters" : "Months"} of ${year}`}
                className="grid grid-cols-4 gap-1 pt-0.5 pr-2 pb-2 pl-8"
              >
                {parts.map((part) => {
                  const count = counts.get(part.time) ?? 0;
                  const on = chosen.has(part.time);
                  const name = `${periodName(part.start, interval)}: ${formatNumber(count)} reviews`;
                  return (
                    <button
                      key={part.time}
                      type="button"
                      aria-pressed={on}
                      aria-label={name}
                      title={name}
                      disabled={count === 0 && !on}
                      onClick={() => toggle([part.time], on)}
                      className={`flex flex-col items-center rounded-md border py-0.5 text-xs leading-tight disabled:cursor-default disabled:opacity-40 ${
                        on ? "border-info-line bg-info-bg" : "border-line bg-surface enabled:hover:border-line-strong"
                      }`}
                    >
                      <span>{part.label}</span>
                      <span className="text-[11px] text-muted tabular-nums">{count ? formatNumber(count) : "–"}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
        {hidden > 0 || showAll ? (
          <button type="button" onClick={() => setShowAll(!showAll)} className="mx-2 mt-1 text-[13px] text-link hover:underline">
            {showAll ? "Show fewer years" : `Show ${hidden} earlier ${hidden === 1 ? "year" : "years"}`}
          </button>
        ) : null}
      </div>
    </div>
  );
}
