"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";

import { SentimentBar, SentimentBreakdown } from "@/components/SentimentBar";
import { TrendChart } from "@/components/TrendChart";
import { BackLink, buttonSecondary, card, ErrorNote, Loading, Page } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatDate, formatNumber, formatPercent, SENTIMENTS, sentimentStyle } from "@/lib/format";
import { useEntity, useThemes } from "@/lib/queries";
import type { Sentiment, ThemeReview, ThemeSummary, ThemeTrend, TrendInterval } from "@/lib/types";

const PAGE_SIZE = 20;
const INTERVALS: { key: TrendInterval; label: string }[] = [
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "year", label: "Year" },
];

function Toggle<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { key: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex overflow-hidden rounded-lg border border-line-strong">
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          aria-pressed={value === option.key}
          onClick={() => onChange(option.key)}
          className={`px-3.5 py-2 text-sm ${value === option.key ? "bg-primary text-on-primary" : "bg-surface text-fg"}`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function ReviewCard({ review }: { review: ThemeReview }) {
  const [expanded, setExpanded] = useState(false);
  const style = review.sentiment ? sentimentStyle(review.sentiment) : null;
  const long = review.text.length > 600;
  return (
    <article className="flex flex-col gap-2 rounded-[10px] border border-line px-4 py-3.5">
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

function Reviews({ entityId, theme }: { entityId: string; theme: ThemeSummary }) {
  const [filter, setFilter] = useState<Sentiment | "all">("all");
  const reviews = useInfiniteQuery({
    queryKey: ["theme-reviews", entityId, theme.id, filter],
    queryFn: ({ pageParam }) =>
      api<ThemeReview[]>(
        `/entities/${entityId}/themes/${theme.id}/reviews?limit=${PAGE_SIZE}&offset=${pageParam}` +
          (filter === "all" ? "" : `&sentiment=${filter}`),
      ),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.length < PAGE_SIZE ? undefined : pages.length * PAGE_SIZE),
  });
  const shown = reviews.data?.pages.flat() ?? [];
  const total = filter === "all" ? theme.sentiment.total : theme.sentiment[filter];
  const filters: { key: Sentiment | "all"; label: string; count: number }[] = [
    { key: "all", label: "All", count: theme.sentiment.total },
    ...SENTIMENTS.map(({ key, label }) => ({ key, label, count: theme.sentiment[key] })),
  ];

  return (
    <section className={`${card} flex flex-col gap-4 px-5 py-4`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">Reviews</h2>
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
      </div>
      <ErrorNote error={reviews.error} />
      {reviews.isPending && <Loading label="Loading reviews…" />}
      {reviews.isSuccess && shown.length === 0 && <p className="text-muted">No reviews with this sentiment.</p>}
      {shown.map((review) => (
        <ReviewCard key={review.id} review={review} />
      ))}
      {shown.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3">
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
      )}
    </section>
  );
}

function Trend({ entityId, themeId }: { entityId: string; themeId: number }) {
  const [interval, setTrendInterval] = useState<TrendInterval>("year");
  const trend = useQuery({
    queryKey: ["trend", entityId, themeId, interval],
    queryFn: () => api<ThemeTrend>(`/entities/${entityId}/themes/${themeId}/trend?interval=${interval}`),
    placeholderData: (previous) => previous,
  });
  return (
    <section className={`${card} flex flex-col gap-4 px-5 py-4`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Reviews over time</h2>
          <p className="text-[13px] text-muted">Periods with no reviews are left out.</p>
        </div>
        <Toggle label="Interval" options={INTERVALS} value={interval} onChange={setTrendInterval} />
      </div>
      <ErrorNote error={trend.error} />
      {trend.isPending && <Loading label="Loading trend…" />}
      {trend.data && <TrendChart trend={trend.data} interval={interval} />}
    </section>
  );
}

export default function ThemePage() {
  const { entityId, themeId } = useParams<{ entityId: string; themeId: string }>();
  const entity = useEntity(entityId);
  const themes = useThemes(entityId);
  // A merged candidate's id resolves to the theme it merged into; the trend says which one that is.
  const resolved = useQuery({
    queryKey: ["trend", entityId, Number(themeId), "year"],
    queryFn: () => api<ThemeTrend>(`/entities/${entityId}/themes/${themeId}/trend?interval=year`),
  });

  const all = [...(themes.data?.themes ?? []), ...(themes.data?.candidates ?? [])];
  const theme = all.find((item) => item.id === (resolved.data?.theme_id ?? Number(themeId)));
  const total = themes.data?.classified_reviews ?? 0;
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
  if (!theme) {
    return (
      <Page>
        <BackLink href={`/entities/${entityId}`}>{entity.data.name}</BackLink>
        <p className="mt-4 text-muted">
          This topic has too few reviews to be listed yet. It appears once 10 reviews raise it.
        </p>
      </Page>
    );
  }

  return (
    <Page>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-1.5">
          <BackLink href={`/entities/${entityId}`}>{entity.data.name}</BackLink>
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
              <strong>{formatPercent(theme.review_count, total)}</strong> of classified reviews
            </span>
            <span>
              <strong>{formatNumber(theme.recent_count)}</strong> in the last {themes.data.recent_days} days
            </span>
          </div>
        </div>
        <section className={`${card} flex flex-col gap-3 px-5 py-4`}>
          <h2 className="font-semibold">Sentiment</h2>
          <SentimentBar counts={theme.sentiment} height="h-3.5" />
          <SentimentBreakdown counts={theme.sentiment} />
        </section>
        <Trend entityId={entityId} themeId={theme.id} />
        <Reviews entityId={entityId} theme={theme} />
      </div>
    </Page>
  );
}
