"use client";

import Link from "next/link";
import { useState } from "react";

import { cell, numberCell, PlainHeading, SortHeading, sortRows, useSorting } from "@/components/SortableTable";
import { periodName } from "@/components/TrendChart";
import { card } from "@/components/ui";
import { formatChange, formatNumber, formatShare, negativeShare } from "@/lib/format";
import type { ThemeChange, ThemeChanges } from "@/lib/types";

/**
 * The column the comparison is sorted by. A change column sorts by the size of the change, largest
 * first, whichever way it went: a fall matters as much as a rise.
 */
type SortKey = "name" | "share" | "negative";

interface Row {
  change: ThemeChange;
  shareDelta: number;
  negativeBefore: number | null;
  negativeNow: number | null;
  /** Null when the theme has no reviews in one of the periods, so it has no negative share there. */
  negativeDelta: number | null;
}

const VALUE: Record<SortKey, (row: Row) => number | string> = {
  name: (row) => row.change.name,
  share: (row) => Math.abs(row.shareDelta),
  negative: (row) => (row.negativeDelta === null ? -1 : Math.abs(row.negativeDelta)),
};

/** A bar from the centre line: right for a rise, left for a fall, its length relative to the largest. */
function ChangeBar({ delta, largest, tone }: { delta: number; largest: number; tone: string }) {
  const width = largest ? `${(Math.abs(delta) / largest) * 50}%` : "0";
  return (
    <span className="relative h-2 min-w-0 flex-1 rounded-full bg-subtle" aria-hidden="true">
      <span className="absolute top-[-2px] left-1/2 h-3 w-px bg-line-strong" />
      <span
        className={`absolute top-0 h-2 rounded-full ${tone}`}
        style={delta >= 0 ? { left: "50%", width } : { right: "50%", width }}
      />
    </span>
  );
}

function ChangeCell({
  delta,
  largest,
  significant,
  /** A rise in the negative share is bad, so it takes the negative colour; mentions are neither. */
  sentiment,
  className = "",
}: {
  delta: number | null;
  largest: number;
  significant: boolean;
  sentiment: boolean;
  className?: string;
}) {
  if (delta === null) return <td className={`${cell} ${className}`} />;
  const tone = !significant ? "bg-line-strong" : !sentiment ? "bg-fg" : delta > 0 ? "bg-neg" : "bg-pos";
  const text = !significant
    ? "text-muted"
    : !sentiment
      ? "font-semibold"
      : delta > 0
        ? "font-semibold text-neg-fg"
        : "font-semibold text-pos-fg";
  return (
    <td className={`${cell} ${className}`}>
      <span className="flex items-center gap-3 pt-1">
        <ChangeBar delta={delta} largest={largest} tone={tone} />
        <span className={`w-16 text-right tabular-nums whitespace-nowrap ${text}`}>{formatChange(delta)}</span>
      </span>
    </td>
  );
}

const shareOf = (share: number | null) => (share === null ? <span className="text-[13px] text-muted">no reviews</span> : formatShare(share));

const badge = "ml-2 rounded-full bg-subtle px-2 py-0.5 text-[11px] font-semibold text-muted";

/**
 * The desktop layout's period comparison: one table with both changes side by side, the share of the
 * period's reviews that mention each theme and the share of its own reviews that are negative, each
 * period with its counts, and each change as a bar from the centre line. By default it lists only the
 * themes with a significant change, the largest change in mentions first; the rest are behind a
 * checkbox and shown faded. A theme opens on its page with both periods chosen in its trend.
 */
export function ChangesTable({ entityId, data }: { entityId: string; data: ThemeChanges }) {
  const [showAll, setShowAll] = useState(false);
  const sorting = useSorting<SortKey>("share", "desc", ["name"]);
  const before = data.previous_start ? periodName(data.previous_start, data.interval) : "";
  const now = data.period_start ? periodName(data.period_start, data.interval) : "";

  const rows: Row[] = data.themes.map((change) => {
    const negativeBefore = negativeShare(change.previous);
    const negativeNow = negativeShare(change.current);
    return {
      change,
      shareDelta: change.current_share - change.previous_share,
      negativeBefore,
      negativeNow,
      negativeDelta: negativeBefore === null || negativeNow === null ? null : negativeNow - negativeBefore,
    };
  });
  const significant = (row: Row) => row.change.share_change_clear || row.change.negative_change_clear;
  const clear = rows.filter(significant);
  const shown = sortRows(showAll ? rows : clear, VALUE, sorting, (a, b) => a.change.name.localeCompare(b.change.name));
  // Bars are scaled to the rows on screen: a hidden theme's huge swing on a handful of reviews (one
  // review of three going negative) would otherwise shrink every visible bar to a sliver.
  const largestShare = Math.max(0, ...shown.map((row) => Math.abs(row.shareDelta)));
  const largestNegative = Math.max(0, ...shown.map((row) => Math.abs(row.negativeDelta ?? 0)));
  const others = rows.length - clear.length;

  // The theme's page opens on this interval with both periods chosen in its trend.
  const periodParams = new URLSearchParams({ interval: data.interval });
  for (const start of [data.previous_start, data.period_start]) if (start) periodParams.append("period", start);
  const group = "border-t border-l border-line px-3 pt-2 pb-0.5 text-left text-[13px] font-semibold";

  return (
    <section className={`${card} overflow-hidden`}>
      <div className="flex flex-wrap items-end justify-between gap-3 px-5 pt-4 pb-3">
        <div>
          <h2 className="text-lg font-semibold">
            {now} compared with {before}
          </h2>
          <p className="text-sm text-muted">
            {formatNumber(data.current_reviews)} reviews in {now} against {formatNumber(data.previous_reviews)} in {before}.{" "}
            {showAll
              ? `All ${rows.length} themes; ${clear.length} with a significant change.`
              : `${clear.length} ${clear.length === 1 ? "theme changed" : "themes changed"} significantly.`}
          </p>
        </div>
        {others > 0 && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={showAll} onChange={(event) => setShowAll(event.target.checked)} className="size-4" />
            Show the {others} {others === 1 ? "theme" : "themes"} with no significant change
          </label>
        )}
      </div>
      {shown.length === 0 ? (
        <p className="border-t border-line px-5 py-4 text-sm">No theme&apos;s mentions or negative share changed significantly.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px] border-collapse text-[15px]">
            <thead>
              <tr>
                <SortHeading {...sorting} column="name" label="Theme" rowSpan={2} className="pl-5 text-left align-bottom" />
                <th scope="colgroup" colSpan={3} className={group}>
                  Share of the period&apos;s reviews that mention the theme
                </th>
                <th scope="colgroup" colSpan={3} className={`${group} pr-5`}>
                  Share of the theme&apos;s reviews that are negative
                </th>
              </tr>
              <tr>
                <PlainHeading label={before} className="w-[96px] border-l text-right" />
                <PlainHeading label={now} className="w-[96px] text-right" />
                <SortHeading {...sorting} column="share" label="Change" className="w-[210px] text-right" />
                <PlainHeading label={before} className="w-[96px] border-l text-right" />
                <PlainHeading label={now} className="w-[96px] text-right" />
                <SortHeading {...sorting} column="negative" label="Change" className="w-[210px] pr-5 text-right" />
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => {
                const { change } = row;
                return (
                  <tr key={change.id} className={`hover:bg-bg ${significant(row) ? "" : "opacity-60"}`}>
                    <td className={`${cell} pl-5`}>
                      <Link href={`/entities/${entityId}/themes/${change.id}?${periodParams}`} className="font-semibold hover:underline">
                        {change.name}
                      </Link>
                      {change.kind === "candidate" && <span className={badge}>Emerging</span>}
                      {change.previous.total === 0 && <span className={badge}>New</span>}
                      {change.current.total === 0 && <span className={badge}>Not raised</span>}
                    </td>
                    <td className={`${numberCell} border-l border-l-subtle`}>
                      <span className="block">{formatShare(change.previous_share)}</span>
                      <span className="mt-0.5 block text-[13px] text-muted">{formatNumber(change.previous.total)} reviews</span>
                    </td>
                    <td className={numberCell}>
                      <span className="block font-semibold">{formatShare(change.current_share)}</span>
                      <span className="mt-0.5 block text-[13px] text-muted">{formatNumber(change.current.total)} reviews</span>
                    </td>
                    <ChangeCell delta={row.shareDelta} largest={largestShare} significant={change.share_change_clear} sentiment={false} />
                    <td className={`${numberCell} border-l border-l-subtle`}>{shareOf(row.negativeBefore)}</td>
                    <td className={`${numberCell} font-semibold`}>{shareOf(row.negativeNow)}</td>
                    <ChangeCell
                      delta={row.negativeDelta}
                      largest={largestNegative}
                      significant={change.negative_change_clear}
                      sentiment
                      className="pr-5"
                    />
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="border-t border-line px-5 py-3 text-[13px] text-muted">
        Select a theme to open its page with both periods chosen in its trend. Changes in grey are not significant.
      </p>
    </section>
  );
}
