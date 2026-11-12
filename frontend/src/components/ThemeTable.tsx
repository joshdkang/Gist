"use client";

import Link from "next/link";
import { SentimentBar } from "@/components/SentimentBar";
import { cell, numberCell, PlainHeading, SortHeading, sortRows, useSorting } from "@/components/SortableTable";
import { formatNumber, formatPercent } from "@/lib/format";
import type { ThemeSummary } from "@/lib/types";

/**
 * The column a table is sorted by. There is no share column to sort: a theme's share is its reviews
 * over the same total for every theme, so it sorts exactly like reviews and sits under the count.
 */
type SortKey = "name" | "reviews" | "positive" | "negative" | "recent";

const share = (part: number, whole: number) => (whole ? part / whole : 0);

const VALUE: Record<SortKey, (theme: ThemeSummary) => number | string> = {
  name: (theme) => theme.name,
  reviews: (theme) => theme.review_count,
  positive: (theme) => share(theme.sentiment.positive, theme.sentiment.total),
  negative: (theme) => share(theme.sentiment.negative, theme.sentiment.total),
  recent: (theme) => theme.recent_count,
};

/**
 * The theme list as a table, for the desktop layout: one row per theme with its description, reviews
 * and share, sentiment, and recent mentions. A column heading sorts by that column (again to reverse).
 * Quiet themes stay in the table, dimmed and marked, so a sort covers every theme.
 */
export function ThemeTable({
  entityId,
  themes,
  query,
  recentDays,
}: {
  entityId: string;
  themes: ThemeSummary[];
  /** Keeps the page's filters on the theme's page ("" for none). */
  query: string;
  recentDays: number;
}) {
  // Ties fall back to the larger theme, so the order is stable and sensible.
  const sorting = useSorting<SortKey>("reviews", "desc", ["name"]);
  const sorted = sortRows(themes, VALUE, sorting, (a, b) => b.review_count - a.review_count);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[880px] border-collapse text-[15px]">
        <thead>
          <tr>
            <SortHeading {...sorting} column="name" label="Theme" className="pl-5 text-left" />
            <SortHeading {...sorting} column="reviews" label="Reviews" className="w-[120px] text-right" />
            <PlainHeading label="Sentiment" className="w-[220px] text-left" />
            <SortHeading {...sorting} column="positive" label="Positive" className="w-[64px] text-right" />
            <SortHeading {...sorting} column="negative" label="Negative" className="w-[68px] text-right" />
            <SortHeading {...sorting} column="recent" label={`Last ${recentDays} days`} className="w-[84px] pr-5 text-right" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((theme) => (
            <tr key={theme.id} className={`hover:bg-bg ${theme.quiet ? "opacity-60" : ""}`}>
              <td className={`${cell} max-w-0 pr-6 pl-5`}>
                <Link href={`/entities/${entityId}/themes/${theme.id}${query}`} className="font-semibold hover:underline">
                  {theme.name}
                </Link>
                {theme.quiet && (
                  <span className="ml-2 rounded-full bg-subtle px-2 py-0.5 text-[11px] font-semibold text-muted">Quiet</span>
                )}
                <p title={theme.description} className="mt-0.5 line-clamp-2 text-sm leading-snug text-muted">
                  {theme.description}
                </p>
              </td>
              <td className={numberCell}>
                <span className="block font-semibold">{formatNumber(theme.review_count)}</span>
                <span className="mt-0.5 block text-[13px] text-muted">{formatPercent(theme.share, 1)} of reviews</span>
              </td>
              <td className={`${cell} pt-[18px]`}>
                <SentimentBar counts={theme.sentiment} />
              </td>
              <td className={`${numberCell} text-pos-fg`}>{formatPercent(theme.sentiment.positive, theme.sentiment.total)}</td>
              <td className={`${numberCell} text-neg-fg`}>{formatPercent(theme.sentiment.negative, theme.sentiment.total)}</td>
              <td className={`${numberCell} pr-5`}>{formatNumber(theme.recent_count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
