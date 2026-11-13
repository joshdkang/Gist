"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ExportButton } from "@/components/ExportButton";
import { capitalize, FilterChip } from "@/components/FilterBar";
import { ReviewFilterPanel } from "@/components/ReviewFilterPanel";
import { Highlighted, searchTerms } from "@/components/SearchBox";
import { BackLink, buttonSecondary, card, ErrorNote, inputBase, Loading, Page, PageTitle } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatDate, formatNumber, sentimentStyle } from "@/lib/format";
import { useLayout } from "@/lib/layout";
import {
  appendPeriodsFilter,
  appendSearch,
  describePeriodsFilter,
  describeSearch,
  filterQuery,
  type PeriodsFilter,
  readPeriodsFilter,
  readSearch,
} from "@/lib/periodFilter";
import { useEntity, useReviewFacets } from "@/lib/queries";
import { appendRatingFilter, describeRatingFilter, readRatingFilter } from "@/lib/ratingFilter";
import {
  appendThemeChoices,
  type Match,
  readMatch,
  readSort,
  readThemeChoices,
  type ReviewSort,
  type ThemeChoices,
} from "@/lib/reviewFilters";
import type { RatingFilter, ReviewListItem, ReviewTag, TrendInterval } from "@/lib/types";

const PAGE_SIZE = 20;
const SORTS: { key: ReviewSort; label: string }[] = [
  { key: "newest", label: "Newest first" },
  { key: "oldest", label: "Oldest first" },
  { key: "highest", label: "Highest rated" },
  { key: "lowest", label: "Lowest rated" },
];

/** Every filter on the page, as the URL holds it. */
interface Filters {
  themes: ThemeChoices;
  match: Match;
  ratingFilter: RatingFilter | null;
  periodsFilter: PeriodsFilter | null;
  search: string | null;
}

/** The filters as query parameters: the API's, which the page's URL uses too. */
function filterParams({ themes, match, ratingFilter, periodsFilter, search }: Filters): URLSearchParams {
  return appendThemeChoices(
    appendSearch(appendRatingFilter(appendPeriodsFilter(new URLSearchParams(), periodsFilter), ratingFilter), search),
    themes,
    match,
  );
}

function Tag({ tag, chosen, onToggle }: { tag: ReviewTag; chosen: boolean; onToggle: () => void }) {
  const name = tag.kind === "general" ? "No specific topic" : tag.name;
  const style = tag.sentiment ? sentimentStyle(tag.sentiment) : null;
  const tip = chosen ? `Remove filter: ${name}` : `Filter by ${name} (${style ? style.label.toLowerCase() : "no sentiment"} here)`;
  return (
    <button
      type="button"
      onClick={onToggle}
      title={tip}
      aria-label={tip}
      aria-pressed={chosen}
      className={`inline-flex h-[26px] items-center gap-1.5 rounded-full border px-2.5 text-[13px] ${
        chosen ? "border-info-line bg-info-bg" : "border-line bg-surface hover:border-line-strong"
      }`}
    >
      <span className={`size-2 rounded-full ${style ? style.bar : "bg-line-strong"}`} aria-hidden="true" />
      {name}
    </button>
  );
}

function ReviewItem({
  review,
  terms,
  themes,
  onToggleTheme,
}: {
  review: ReviewListItem;
  terms: string[];
  themes: ThemeChoices;
  onToggleTheme: (themeId: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const long = review.text.length > 600;
  return (
    <article className="flex flex-col gap-2 border-b border-subtle px-5 py-4">
      <p className="flex flex-wrap gap-x-3 text-[13px] text-muted">
        {review.rating !== null && <span className="font-semibold text-fg">Rating {Number(review.rating)}</span>}
        {review.review_date && <span>{formatDate(review.review_date)}</span>}
      </p>
      {review.title && (
        <p className="font-semibold">
          <Highlighted text={review.title} terms={terms} />
        </p>
      )}
      <p className={`whitespace-pre-line ${expanded ? "" : "line-clamp-6"}`}>
        <Highlighted text={review.text} terms={terms} />
      </p>
      {long && (
        <button type="button" onClick={() => setExpanded(!expanded)} className="self-start text-sm text-link hover:underline">
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
      {/* A tag is a toggle: it adds its theme to the filters (any sentiment), or removes it. */}
      <div className="mt-0.5 flex flex-wrap gap-1.5">
        {review.themes.map((tag) => (
          <Tag key={tag.id} tag={tag} chosen={themes.has(tag.id)} onToggle={() => onToggleTheme(tag.id)} />
        ))}
      </div>
    </article>
  );
}

/**
 * Every classified review of an entity with the themes it raises, filtered by theme (each with the
 * sentiments of its mentions, combined by any or all), rating, period and search, and sorted by date or
 * rating. The filters live in the URL in the API's own parameters, so links carry them both ways. On
 * desktop the filters are a column that stays in view; on mobile they open from a Filters button.
 */
export default function AllReviewsPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const desktop = useLayout() === "desktop";
  const [showFilters, setShowFilters] = useState(false);
  const entity = useEntity(entityId);

  const filters: Filters = useMemo(
    () => ({
      themes: readThemeChoices(searchParams),
      match: readMatch(searchParams),
      ratingFilter: readRatingFilter(searchParams),
      periodsFilter: readPeriodsFilter(searchParams),
      search: readSearch(searchParams),
    }),
    [searchParams],
  );
  const sort = readSort(searchParams);
  const query = filterParams(filters).toString();
  // The period list's interval: the chosen periods', or the user's pick while none is chosen.
  const [chartInterval, setChartInterval] = useState<TrendInterval>("year");
  const periodInterval = filters.periodsFilter?.interval ?? chartInterval;
  const facets = useReviewFacets(entityId, `${query}${query ? "&" : ""}period_interval=${periodInterval}`);
  const reviews = useInfiniteQuery({
    queryKey: ["reviews", entityId, query, sort],
    queryFn: ({ pageParam }) =>
      api<ReviewListItem[]>(
        `/entities/${entityId}/reviews?${query}${query ? "&" : ""}sort=${sort}&limit=${PAGE_SIZE}&offset=${pageParam}`,
      ),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.length < PAGE_SIZE ? undefined : pages.length * PAGE_SIZE),
  });

  function go(next: Partial<Filters>, nextSort: ReviewSort = sort) {
    const params = filterParams({ ...filters, ...next });
    if (nextSort !== "newest") params.set("sort", nextSort);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  function toggleTheme(themeId: number) {
    const themes = new Map(filters.themes);
    if (themes.has(themeId)) themes.delete(themeId);
    else themes.set(themeId, []);
    go({ themes });
  }

  if (entity.error instanceof ApiError && entity.error.status === 404) {
    return (
      <Page>
        <BackLink href="/">Entities</BackLink>
        <p className="mt-4">This entity doesn&apos;t exist, or you don&apos;t have access to it.</p>
      </Page>
    );
  }

  const shown = reviews.data?.pages.flat() ?? [];
  const total = facets.data?.total ?? null;
  const terms = searchTerms(filters.search);
  const nameOf = new Map((facets.data?.themes ?? []).map((facet) => [facet.id, facet.kind === "general" ? "No specific topic" : facet.name]));
  const shared = filterQuery(filters.ratingFilter, filters.periodsFilter, filters.search);
  const chips = [
    ...[...filters.themes].map(([themeId, sentiments]) => {
      const name = nameOf.get(themeId) ?? "Theme";
      const label = sentiments.length
        ? `${name}: ${sentiments.map((sentiment) => sentimentStyle(sentiment).label).join(" or ")}`
        : name;
      const themes = new Map(filters.themes);
      themes.delete(themeId);
      return { key: `theme-${themeId}`, label, remove: () => go({ themes }) };
    }),
    ...(filters.ratingFilter
      ? [{ key: "rating", label: capitalize(describeRatingFilter(filters.ratingFilter)), remove: () => go({ ratingFilter: null }) }]
      : []),
    ...(filters.periodsFilter
      ? [{ key: "period", label: capitalize(describePeriodsFilter(filters.periodsFilter)), remove: () => go({ periodsFilter: null }) }]
      : []),
    ...(filters.search ? [{ key: "search", label: capitalize(describeSearch(filters.search)), remove: () => go({ search: null }) }] : []),
  ];
  const clearAll = () => go({ themes: new Map(), ratingFilter: null, periodsFilter: null, search: null });
  const joined = filters.themes.size > 1 ? ` (${filters.match} of the ticked themes)` : "";

  const panel = facets.data && (
    <ReviewFilterPanel
      facets={facets.data}
      themes={filters.themes}
      match={filters.match}
      ratingFilter={filters.ratingFilter}
      search={filters.search}
      periodsFilter={filters.periodsFilter}
      periodInterval={periodInterval}
      onPeriodsFilter={(periodsFilter) => go({ periodsFilter })}
      onPeriodInterval={setChartInterval}
      themeHref={(themeId) => `/entities/${entityId}/themes/${themeId}${shared}`}
      onThemes={(themes) => go({ themes })}
      onMatch={(match) => go({ match })}
      onRatingFilter={(ratingFilter) => go({ ratingFilter })}
      onSearch={(search) => go({ search })}
    />
  );

  return (
    <Page>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-1.5">
          <BackLink href={`/entities/${entityId}${shared}`}>{entity.data?.name ?? "Entity"}</BackLink>
          <PageTitle>All reviews</PageTitle>
          <p className="max-w-3xl text-muted">
            Every review{entity.data ? ` of ${entity.data.name}` : ""} with the themes it raises. Filter by theme, sentiment
            or rating, or search the text. Select a review&apos;s theme to filter by it.
          </p>
        </div>
        <ErrorNote error={facets.error ?? reviews.error} />

        <div className={desktop ? "grid items-start gap-6 lg:grid-cols-[300px_minmax(0,1fr)]" : "flex flex-col gap-4"}>
          {desktop ? (
            <aside className={`${card} sticky top-4 max-h-[calc(100vh-2rem)] overflow-y-auto`}>
              {panel ?? <Loading label="Counting…" />}
            </aside>
          ) : (
            <div className="flex flex-col gap-3">
              <button
                type="button"
                aria-expanded={showFilters}
                onClick={() => setShowFilters(!showFilters)}
                className={`${buttonSecondary} self-start`}
              >
                {showFilters ? "Hide filters" : `Filters${chips.length ? ` (${chips.length})` : ""}`}
              </button>
              {showFilters && <div className={card}>{panel ?? <Loading label="Counting…" />}</div>}
            </div>
          )}

          <section className={`${card} min-w-0`}>
            <div
              className={`flex flex-col gap-2.5 border-b border-line px-5 py-4 ${
                chips.length ? "sticky top-0 z-10 rounded-t-xl bg-surface" : ""
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">
                  {total === null ? "Counting the reviews…" : `${formatNumber(total)} ${total === 1 ? "review" : "reviews"}${joined}`}
                </h2>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-2 text-sm">
                    Sort
                    <select
                      value={sort}
                      onChange={(event) => go({}, event.target.value as ReviewSort)}
                      className={`${inputBase} h-9 pr-8 text-sm`}
                    >
                      {SORTS.map((option) => (
                        <option key={option.key} value={option.key}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {total !== null && total > 0 && (
                    <ExportButton
                      path={`/entities/${entityId}/exports/reviews.csv${query ? `?${query}` : ""}`}
                      label={`Export ${formatNumber(total)} ${total === 1 ? "review" : "reviews"}`}
                    />
                  )}
                </div>
              </div>
              {chips.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  {chips.map((chip) => (
                    <FilterChip key={chip.key} label={chip.label} onRemove={chip.remove} />
                  ))}
                  <button type="button" onClick={clearAll} className="px-1 py-1 text-sm text-link hover:underline">
                    Clear all
                  </button>
                </div>
              )}
            </div>
            {reviews.isPending && <div className="px-5"><Loading label="Loading reviews…" /></div>}
            {reviews.isSuccess && shown.length === 0 && <p className="px-5 py-4 text-muted">No reviews match these filters.</p>}
            {shown.map((review) => (
              <ReviewItem key={review.id} review={review} terms={terms} themes={filters.themes} onToggleTheme={toggleTheme} />
            ))}
            {shown.length > 0 && (
              <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5">
                <span className="text-sm text-muted">
                  Showing {formatNumber(shown.length)}
                  {total !== null ? ` of ${formatNumber(total)}` : ""}
                </span>
                {reviews.hasNextPage && (
                  <button
                    type="button"
                    onClick={() => reviews.fetchNextPage()}
                    disabled={reviews.isFetchingNextPage}
                    className={`${buttonSecondary} h-10`}
                  >
                    {reviews.isFetchingNextPage ? "Loading…" : "Load more"}
                  </button>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </Page>
  );
}
