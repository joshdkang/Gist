"use client";

import Link from "next/link";

import { ExportButton, smallButtonClass } from "@/components/ExportButton";
import { SearchBox } from "@/components/SearchBox";
import { formatNumber } from "@/lib/format";
import { describePeriodsFilter, describeSearch, type PeriodsFilter } from "@/lib/periodFilter";
import { describeRatingFilter } from "@/lib/ratingFilter";
import type { RatingFilter } from "@/lib/types";

export const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** One active filter, as a chip that removes it. */
export function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <button
      type="button"
      onClick={onRemove}
      aria-label={`Remove filter: ${label}`}
      className="inline-flex h-8 items-center gap-1.5 rounded-full border border-info-line bg-info-bg pr-2 pl-3 text-sm font-medium hover:border-link"
    >
      {label}
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </button>
  );
}

/**
 * The desktop layout's filter bar, above the results: how many reviews the results cover, the active
 * filters as chips that remove them, a search of the review text, the view switch and the exports. While a filter is on it stays at
 * the top of the window, so the filter stays in view over a long list; otherwise it scrolls away.
 */
export function FilterBar({
  entityId,
  totalReviews,
  shownReviews,
  ratingFilter,
  periodsFilter,
  onClearRating,
  onClearPeriods,
  onClearAll,
  search,
  onSearch,
  query,
  view,
}: {
  entityId: string;
  totalReviews: number;
  /** The reviews the filters select (all of them without a filter); null while the count loads. */
  shownReviews: number | null;
  ratingFilter: RatingFilter | null;
  periodsFilter: PeriodsFilter | null;
  onClearRating: () => void;
  onClearPeriods: () => void;
  /** Every filter at once: one URL change, since each of the others keeps the rest as they were. */
  onClearAll: () => void;
  /** The search in force, and how to change it (null clears it). */
  search: string | null;
  onSearch: (search: string | null) => void;
  /** Every filter as a query string, for the exports. */
  query: string;
  /** The view switch (Themes or Ratings by theme), when there is one. */
  view?: React.ReactNode;
}) {
  const filtered = Boolean(ratingFilter || periodsFilter || search);
  return (
    <div
      className={`-mx-4 px-4 py-2.5 sm:-mx-6 sm:px-6 lg:-mx-10 lg:px-10 ${
        filtered ? "sticky top-0 z-10 border-b border-line bg-bg/95 backdrop-blur-sm" : ""
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-h-9 flex-wrap items-center gap-2.5">
          {filtered ? (
            <>
              <span className="font-semibold">
                {shownReviews === null
                  ? "Counting the reviews…"
                  : `Showing ${formatNumber(shownReviews)} of ${formatNumber(totalReviews)} reviews`}
              </span>
              {periodsFilter && <FilterChip label={capitalize(describePeriodsFilter(periodsFilter))} onRemove={onClearPeriods} />}
              {ratingFilter && <FilterChip label={capitalize(describeRatingFilter(ratingFilter))} onRemove={onClearRating} />}
              {search && <FilterChip label={capitalize(describeSearch(search))} onRemove={() => onSearch(null)} />}
              <button
                type="button"
                onClick={onClearAll}
                className="px-1 py-1 text-sm text-link hover:underline"
              >
                Clear all
              </button>
            </>
          ) : (
            <>
              <span className="font-semibold">All {formatNumber(totalReviews)} reviews</span>
              <span className="text-sm text-muted">Select a rating or a period above, or search, to filter.</span>
            </>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SearchBox value={search} onSearch={onSearch} className="w-64" />
          {view}
          <Link href={`/entities/${entityId}/reviews${query}`} className={smallButtonClass}>
            View reviews
          </Link>
          <ExportButton path={`/entities/${entityId}/exports/themes.csv${query}`} label="Export themes" />
          <ExportButton path={`/entities/${entityId}/exports/reviews.csv${query}`} label="Export reviews" />
        </div>
      </div>
    </div>
  );
}
