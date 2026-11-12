"use client";

import { useState } from "react";

import { periodName } from "@/components/TrendChart";
import { INTERVALS, inputBase, Toggle } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import type { ThemeChanges, TrendInterval } from "@/lib/types";

/** The two periods to compare. A null period lets the API pick (the latest with enough reviews, and the one before). */
export interface PeriodChoice {
  interval: TrendInterval;
  period: string | null;
  compareTo: string | null;
}

/** The chosen periods, with the rules that keep them consistent when one of them changes. */
export function usePeriodChoice(initial: Partial<PeriodChoice> = {}) {
  const [choice, setChoice] = useState<PeriodChoice>({
    interval: initial.interval ?? "quarter",
    period: initial.period ?? null,
    compareTo: initial.compareTo ?? null,
  });
  return {
    ...choice,
    // A quarter's start is not a month's; let the API pick both periods again.
    setInterval: (interval: TrendInterval) => setChoice({ interval, period: null, compareTo: null }),
    // A period can't be compared with itself.
    setPeriod: (period: string) =>
      setChoice((current) => ({ ...current, period, compareTo: current.compareTo === period ? null : current.compareTo })),
    setCompareTo: (compareTo: string) => setChoice((current) => ({ ...current, compareTo })),
  };
}

/**
 * The query that asks for these periods: the API's own parameters, so a link can carry them. A
 * `prefix` keeps them apart from a page's other parameters (the printable summary's period filter
 * uses `interval` and `period` too).
 */
export function periodQuery({ interval, period, compareTo }: PeriodChoice, prefix = ""): URLSearchParams {
  const params = new URLSearchParams({ [`${prefix}interval`]: interval });
  if (period) params.set(`${prefix}period`, period);
  if (compareTo) params.set(`${prefix}compare_to`, compareTo);
  return params;
}

/** The choice in a page's URL, as `periodQuery` writes it with the same prefix. */
export function readPeriodChoice(
  params: { get(name: string): string | null },
  prefix = "",
): Partial<PeriodChoice> {
  const interval = params.get(`${prefix}interval`);
  return {
    interval: INTERVALS.some((option) => option.key === interval) ? (interval as TrendInterval) : undefined,
    period: params.get(`${prefix}period`),
    compareTo: params.get(`${prefix}compare_to`),
  };
}

/** The prefix of the printable summary's comparison periods. */
export const SUMMARY_CHANGES_PREFIX = "changes_";

/**
 * The interval and the two periods compared, as controls in a flex row. `data` is the comparison the
 * API returned, which says which periods it picked and which have reviews.
 */
export function PeriodPicker({
  choice,
  data,
}: {
  choice: ReturnType<typeof usePeriodChoice>;
  data: ThemeChanges | undefined;
}) {
  // The periods to compare with: every other period with reviews, newest first, plus the default
  // (the period before) when it has none, so the selector can show what is being compared.
  const compareOptions = data
    ? [
        ...data.periods.filter((option) => option.period_start !== data.period_start),
        ...(data.previous_start && !data.periods.some((option) => option.period_start === data.previous_start)
          ? [{ period_start: data.previous_start, reviews: 0 }]
          : []),
      ].sort((a, b) => b.period_start.localeCompare(a.period_start))
    : [];

  return (
    <>
      <div className="flex flex-col gap-1.5">
        <span className="text-sm font-medium">Interval</span>
        <Toggle label="Interval" options={INTERVALS} value={choice.interval} onChange={choice.setInterval} />
      </div>
      {data && data.periods.length > 0 && (
        <>
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Period</span>
            <select
              className={`${inputBase} pr-8`}
              value={data.period_start ?? ""}
              onChange={(event) => choice.setPeriod(event.target.value)}
            >
              {[...data.periods].reverse().map((option) => (
                <option key={option.period_start} value={option.period_start}>
                  {periodName(option.period_start, data.interval)} · {formatNumber(option.reviews)} reviews
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Compared with</span>
            <select
              className={`${inputBase} pr-8`}
              value={data.previous_start ?? ""}
              onChange={(event) => choice.setCompareTo(event.target.value)}
            >
              {compareOptions.map((option) => (
                <option key={option.period_start} value={option.period_start} disabled={option.reviews === 0}>
                  {periodName(option.period_start, data.interval)} ·{" "}
                  {option.reviews ? `${formatNumber(option.reviews)} reviews` : "no reviews"}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
    </>
  );
}
