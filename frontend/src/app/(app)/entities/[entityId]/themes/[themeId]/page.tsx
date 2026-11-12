"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ExportButton } from "@/components/ExportButton";
import { capitalize, FilterChip } from "@/components/FilterBar";
import { RatingsCard } from "@/components/RatingsCard";
import { SentimentBar, SentimentBreakdown } from "@/components/SentimentBar";
import { periodName, TrendChart, type TrendMode } from "@/components/TrendChart";
import { BackLink, buttonSecondary, card, ErrorNote, INTERVALS, Loading, Page, Toggle } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatDate, formatNumber, formatPercent, SENTIMENTS, sentimentStyle } from "@/lib/format";
import { useLayout } from "@/lib/layout";
import { useEntity, useThemes } from "@/lib/queries";
import { describePeriodsFilter, filterQuery } from "@/lib/periodFilter";
import { appendRatingFilter, describeRatingFilter, ratingQuery, readRatingFilter } from "@/lib/ratingFilter";
import type {
  RatingFilter,
  RatingSummary,
  Sentiment,
  SentimentCounts,
  ThemeReview,
  ThemeSummary,
  ThemeTrend,
  TrendInterval,
} from "@/lib/types";

const PAGE_SIZE = 20;
const MODES: { key: TrendMode; label: string }[] = [
  { key: "count", label: "Reviews" },
  { key: "share", label: "% of reviews" },
  { key: "mix", label: "Sentiment mix" },
];
const MODE_HINTS: Record<TrendMode, string> = {
  count: "The theme's reviews in each period.",
  share: "The share of each period's reviews that raise the theme, so a busy period doesn't look like a surge.",
  mix: "How the theme's reviews in each period felt, each bar adding up to 100%.",
};

function ReviewCard({ review, row = false }: { review: ThemeReview; row?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const style = review.sentiment ? sentimentStyle(review.sentiment) : null;
  const long = review.text.length > 600;
  return (
    <article
      className={`flex flex-col gap-2 ${row ? "border-b border-subtle px-5 py-4" : "rounded-[10px] border border-line px-4 py-3.5"}`}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px]">
        {style && (
          <span className={`flex items-center gap-1.5 font-semibold ${style.text}`}>
            <span className={`size-2.5 rounded-full ${style.bar}`} aria-hidden="true" />
            {style.label}
          </span>
        )}
        {review.rating !== null && <span>Rating {Number(review.rating)}</span>}
        {review.review_date && <span className="text-muted">{formatDate(review.review_date)}</span>}
      </div>
      {review.title && <p className="font-semibold">{review.title}</p>}
      <p className={`whitespace-pre-line ${expanded ? "" : "line-clamp-6"}`}>{review.text}</p>
      {long && (
        <button type="button" onClick={() => setExpanded(!expanded)} className="self-start text-sm text-link hover:underline">
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </article>
  );
}

/** Trend periods chosen on the chart, with their reviews counted by sentiment. */
interface PeriodFilter {
  interval: TrendInterval;
  starts: string[];
  counts: SentimentCounts;
}

function Reviews({
  entityId,
  theme,
  periods,
  onClearPeriods,
  ratingFilter,
  onClearRating,
  desktop,
}: {
  entityId: string;
  theme: ThemeSummary;
  periods: PeriodFilter | null;
  onClearPeriods: () => void;
  ratingFilter: RatingFilter | null;
  onClearRating: () => void;
  desktop: boolean;
}) {
  const [filter, setFilter] = useState<Sentiment | "all">("all");
  const params = appendRatingFilter(new URLSearchParams({ limit: String(PAGE_SIZE) }), ratingFilter);
  if (filter !== "all") params.set("sentiment", filter);
  if (periods) {
    params.set("interval", periods.interval);
    for (const start of periods.starts) params.append("period", start);
  }
  const reviews = useInfiniteQuery({
    queryKey: ["theme-reviews", entityId, theme.id, filter, periods?.interval, periods?.starts, ratingFilter],
    queryFn: ({ pageParam }) =>
      api<ThemeReview[]>(`/entities/${entityId}/themes/${theme.id}/reviews?${params}&offset=${pageParam}`),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.length < PAGE_SIZE ? undefined : pages.length * PAGE_SIZE),
  });
  const shown = reviews.data?.pages.flat() ?? [];
  // The counts behind the chips: the chosen periods' bars, or the whole theme.
  const counts = periods?.counts ?? theme.sentiment;
  const total = filter === "all" ? counts.total : counts[filter];
  const filters: { key: Sentiment | "all"; label: string; count: number }[] = [
    { key: "all", label: "All", count: counts.total },
    ...SENTIMENTS.map(({ key, label }) => ({ key, label, count: counts[key] })),
  ];
  // The export takes the list's filters, without paging.
  const exportParams = new URLSearchParams(params);
  exportParams.delete("limit");
  exportParams.set("theme_id", String(theme.id));
  const periodNames = periods
    ? [...periods.starts].sort().map((start) => periodName(start, periods.interval))
    : [];
  const chips = (
    <div role="group" aria-label="Filter by sentiment" className="flex flex-wrap gap-1.5">
      {filters.map((option) => (
        <button
          key={option.key}
          type="button"
          aria-pressed={filter === option.key}
          onClick={() => setFilter(option.key)}
          className={`rounded-full border px-3 py-1.5 text-sm ${
            filter === option.key ? "border-primary bg-primary text-on-primary" : "border-line-strong bg-surface"
          }`}
        >
          {option.label} <span className="opacity-80">{formatNumber(option.count)}</span>
        </button>
      ))}
    </div>
  );
  const exportButton = total > 0 && (
    <ExportButton
      path={`/entities/${entityId}/exports/reviews.csv?${exportParams}`}
      label={`Export ${formatNumber(total)} ${total === 1 ? "review" : "reviews"}`}
    />
  );
  const footer = shown.length > 0 && (
    <div className={`flex flex-wrap items-center justify-between gap-3 ${desktop ? "px-5 py-3.5" : ""}`}>
      <span className="text-sm text-muted">
        Showing {formatNumber(shown.length)} of {formatNumber(total)}, newest first
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
  );

  if (desktop) {
    const anyFilter = Boolean(periods || ratingFilter || filter !== "all");
    const what = filter === "all" ? "reviews" : `${filter} reviews`;
    return (
      <section className={`${card} min-w-0`}>
        {/* While any filter is on (periods, ratings or a sentiment), the heading and its chips stay at the top
            of the window as the reviews scroll, so the filter stays in view (as the entity page's filter bar does). */}
        <div
          className={`flex flex-col gap-3 border-b border-line px-5 py-4 ${
            anyFilter ? "sticky top-0 z-10 rounded-t-xl bg-surface" : ""
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">Reviews</h2>
              <p className="text-sm text-muted">
                {formatNumber(total)} {total === 1 ? what.replace(/s$/, "") : what}, newest first
              </p>
            </div>
            {exportButton}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {chips}
            {(periods || ratingFilter) && <span className="mx-1 h-5 w-px bg-line" aria-hidden="true" />}
            {periods && (
              <FilterChip
                label={capitalize(describePeriodsFilter({ interval: periods.interval, starts: periods.starts }))}
                onRemove={onClearPeriods}
              />
            )}
            {ratingFilter && <FilterChip label={capitalize(describeRatingFilter(ratingFilter))} onRemove={onClearRating} />}
            {anyFilter && (
              <button
                type="button"
                onClick={() => {
                  setFilter("all");
                  onClearPeriods();
                  onClearRating();
                }}
                className="px-1 py-1 text-sm text-link hover:underline"
              >
                Clear all
              </button>
            )}
          </div>
        </div>
        <ErrorNote error={reviews.error} />
        {reviews.isPending && <div className="px-5"><Loading label="Loading reviews…" /></div>}
        {reviews.isSuccess && shown.length === 0 && <p className="px-5 py-4 text-muted">No reviews with this sentiment.</p>}
        {shown.map((review) => (
          <ReviewCard key={review.id} review={review} row />
        ))}
        {footer}
      </section>
    );
  }

  return (
    <section className={`${card} flex flex-col gap-4 px-5 py-4`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-semibold">Reviews</h2>
          {exportButton}
        </div>
        {chips}
      </div>
      {periods && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-subtle px-3 py-2 text-sm">
          <span>
            From {periodNames.length > 4 ? `${periodNames.length} periods` : periodNames.join(", ")}
          </span>
          <button type="button" onClick={onClearPeriods} className="text-link hover:underline">
            Show all periods
          </button>
        </div>
      )}
      <ErrorNote error={reviews.error} />
      {reviews.isPending && <Loading label="Loading reviews…" />}
      {reviews.isSuccess && shown.length === 0 && <p className="text-muted">No reviews with this sentiment.</p>}
      {shown.map((review) => (
        <ReviewCard key={review.id} review={review} />
      ))}
      {footer}
    </section>
  );
}

function Trend({
  trend,
  interval,
  onIntervalChange,
  selected,
  onToggle,
  desktop,
}: {
  desktop: boolean;
  trend: ReturnType<typeof useTrend>;
  interval: TrendInterval;
  onIntervalChange: (interval: TrendInterval) => void;
  selected: string[];
  onToggle: (periodStart: string) => void;
}) {
  const [mode, setMode] = useState<TrendMode>("count");
  return (
    <section className={`${card} flex flex-col gap-4 px-5 py-4`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className={desktop ? "min-w-[260px] flex-1" : "max-w-md"}>
          <h2 className={desktop ? "text-lg font-semibold" : "font-semibold"}>Reviews over time</h2>
          <p className={desktop ? "text-sm text-muted" : "text-[13px] text-muted"}>
            {MODE_HINTS[mode]} Select bars to list their reviews{desktop ? " below" : ""}.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Toggle label="Show" options={MODES} value={mode} onChange={setMode} />
          <Toggle label="Interval" options={INTERVALS} value={interval} onChange={onIntervalChange} />
        </div>
      </div>
      <ErrorNote error={trend.error} />
      {trend.isPending && <Loading label="Loading trend…" />}
      {trend.data && (
        <TrendChart trend={trend.data} interval={interval} mode={mode} selected={selected} onToggle={onToggle} />
      )}
    </section>
  );
}

function useTrend(
  entityId: string,
  themeId: number | undefined,
  interval: TrendInterval,
  ratingFilter: RatingFilter | null,
) {
  const params = appendRatingFilter(new URLSearchParams({ interval }), ratingFilter);
  return useQuery({
    queryKey: ["trend", entityId, themeId, interval, ratingFilter],
    queryFn: () => api<ThemeTrend>(`/entities/${entityId}/themes/${themeId}/trend?${params}`),
    placeholderData: (previous) => previous,
    enabled: themeId !== undefined,
  });
}

/** The sentiment counts of the chosen periods, added up from their bars. */
function sumPeriods(trend: ThemeTrend, starts: string[]): SentimentCounts {
  const sum: SentimentCounts = { positive: 0, negative: 0, mixed: 0, neutral: 0, total: 0 };
  for (const point of trend.points) {
    if (!starts.includes(point.period_start)) continue;
    for (const key of Object.keys(sum) as (keyof SentimentCounts)[]) sum[key] += point.sentiment[key];
  }
  return sum;
}

export default function ThemePage() {
  const { entityId, themeId } = useParams<{ entityId: string; themeId: string }>();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // A link can carry the entity page's rating filter, and the period comparison's interval and
  // periods (`interval`, repeated `period`), so the page opens on what was being looked at.
  const ratingFilter = useMemo(() => readRatingFilter(searchParams), [searchParams]);
  const entity = useEntity(entityId);
  const themes = useThemes(entityId, ratingFilter);
  const desktop = useLayout() === "desktop";
  // A merged candidate's id resolves to the theme it merged into; the trend says which one that is.
  const resolved = useQuery({
    queryKey: ["trend", entityId, Number(themeId), "year"],
    queryFn: () => api<ThemeTrend>(`/entities/${entityId}/themes/${themeId}/trend?interval=year`),
  });

  const all = [...(themes.data?.themes ?? []), ...(themes.data?.candidates ?? [])];
  const theme = all.find((item) => item.id === (resolved.data?.theme_id ?? Number(themeId)));
  const [interval, setTrendInterval] = useState<TrendInterval>(() => {
    const asked = searchParams.get("interval");
    return INTERVALS.find((option) => option.key === asked)?.key ?? "year";
  });
  const [selectedPeriods, setSelectedPeriods] = useState<string[]>(() => searchParams.getAll("period"));
  const trend = useTrend(entityId, theme?.id, interval, ratingFilter);
  // The trend shown may be the previous interval's while the new one loads; only filter on a match.
  const trendMatches = trend.data?.interval === interval && !trend.isPlaceholderData;
  // A period with no reviews (a comparison with an empty period) has no bar, so it isn't a choice.
  const chosen = trend.data
    ? selectedPeriods.filter((start) => trend.data.points.some((point) => point.period_start === start))
    : [];
  const periods: PeriodFilter | null =
    chosen.length > 0 && trend.data && trendMatches
      ? { interval, starts: chosen, counts: sumPeriods(trend.data, chosen) }
      : null;

  // The theme's own ratings: their bars choose the filter, which lives in the URL like the entity page's.
  const themeRatings = useQuery({
    queryKey: ["theme-rating-summary", entityId, theme?.id],
    queryFn: () => api<RatingSummary>(`/entities/${entityId}/themes/${theme?.id}/ratings`),
    enabled: theme !== undefined,
  });

  function changeRatingFilter(filter: RatingFilter | null) {
    router.replace(`${pathname}${ratingQuery(filter)}`, { scroll: false });
  }

  function clearRatingFilter() {
    changeRatingFilter(null);
  }

  function changeInterval(next: TrendInterval) {
    setTrendInterval(next);
    setSelectedPeriods([]);  // a year's start is not a month's; the choice doesn't carry over
  }

  function togglePeriod(start: string) {
    setSelectedPeriods((current) =>
      current.includes(start) ? current.filter((item) => item !== start) : [...current, start],
    );
  }
  const total = themes.data?.classified_reviews ?? 0;
  const ratingsCard = themeRatings.data && themeRatings.data.scales.length > 0 && (
    <RatingsCard
      ratings={themeRatings.data}
      filter={ratingFilter}
      onFilterChange={changeRatingFilter}
      title="Ratings of these reviews"
      hints={{
        idle: "Select a rating to see only those reviews.",
        active: "The counts, the trend and the reviews cover only the selected ratings.",
      }}
    />
  );
  // Back to the entity page with this page's filters: its ratings and the periods chosen on the trend.
  const backHref = `/entities/${entityId}${filterQuery(
    ratingFilter,
    selectedPeriods.length > 0 ? { interval, starts: selectedPeriods } : null,
  )}`;
  const error = entity.error ?? resolved.error ?? themes.error;

  if (error instanceof ApiError && error.status === 404) {
    return (
      <Page>
        <BackLink href={`/entities/${entityId}`}>Back</BackLink>
        <p className="mt-4">This theme doesn&apos;t exist, or you don&apos;t have access to it.</p>
      </Page>
    );
  }
  if (error) return <Page><ErrorNote error={error} /></Page>;
  if (!entity.data || !themes.data || !resolved.data) return <Page><Loading /></Page>;
  if (!theme && ratingFilter) {
    return (
      <Page>
        <BackLink href={backHref}>{entity.data.name}</BackLink>
        <p className="mt-4">
          No reviews {describeRatingFilter(ratingFilter)} raise this theme.{" "}
          <button type="button" onClick={clearRatingFilter} className="text-link hover:underline">
            Show all ratings
          </button>
        </p>
      </Page>
    );
  }
  if (!theme) {
    return (
      <Page>
        <BackLink href={backHref}>{entity.data.name}</BackLink>
        <p className="mt-4 text-muted">
          This topic has too few reviews to be listed yet. It appears once 10 reviews raise it.
        </p>
      </Page>
    );
  }

  const reviewsList = (
    <Reviews
      entityId={entityId}
      theme={theme}
      periods={periods}
      onClearPeriods={() => setSelectedPeriods([])}
      ratingFilter={ratingFilter}
      onClearRating={clearRatingFilter}
      desktop={desktop}
    />
  );

  return (
    <Page>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-1.5">
          <BackLink href={backHref}>{entity.data.name}</BackLink>
          {theme.kind === "candidate" && (
            <span className="self-start rounded-full bg-subtle px-2.5 py-0.5 text-xs font-semibold text-muted">
              Emerging topic
            </span>
          )}
          <h1 className="font-display text-[30px] leading-tight font-semibold">{theme.name}</h1>
          <p className="max-w-[760px] text-muted">{theme.description}</p>
          <div className="mt-1.5 flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <span>
              <strong>{formatNumber(theme.review_count)}</strong> reviews
            </span>
            <span>
              <strong>{formatPercent(theme.review_count, total)}</strong> of{" "}
              {ratingFilter ? `reviews ${describeRatingFilter(ratingFilter)}` : "classified reviews"}
            </span>
            <span>
              <strong>{formatNumber(theme.recent_count)}</strong> in the last {themes.data.recent_days} days
            </span>
          </div>
        </div>
        {/* Mobile: ratings, sentiment, trend, reviews, stacked. Desktop: sentiment and the trend across
            the page, then the ratings in a column that stays in view beside the reviews. */}
        {!desktop && ratingsCard}
        <section className={`${card} flex flex-col gap-3 px-5 py-4`}>
          <h2 className="font-semibold">Sentiment</h2>
          <SentimentBar counts={theme.sentiment} height="h-3.5" />
          <SentimentBreakdown counts={theme.sentiment} />
        </section>
        <Trend
          desktop={desktop}
          trend={trend}
          interval={interval}
          onIntervalChange={changeInterval}
          selected={selectedPeriods}
          onToggle={togglePeriod}
        />
        {desktop && ratingsCard ? (
          <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
            <div className="lg:sticky lg:top-4">{ratingsCard}</div>
            {reviewsList}
          </div>
        ) : (
          reviewsList
        )}
      </div>
    </Page>
  );
}
