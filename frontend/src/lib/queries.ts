"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import { appendPeriodsFilter, appendSearch, type PeriodsFilter } from "@/lib/periodFilter";
import { appendRatingFilter } from "@/lib/ratingFilter";
import type {
  EntityDetail,
  RatingFilter,
  RatingScale,
  RatingSummary,
  ReviewFacets,
  ReviewPeriods,
  RunDetail,
  ThemeChanges,
  ThemeList,
  ThemeRatings,
  TrendInterval,
} from "@/lib/types";

const POLL_MS = 3000;

export const isActive = (run: RunDetail | null | undefined) =>
  run?.status === "queued" || run?.status === "running";

/** The entity with its latest analysis run. Polls while a run is queued or running. */
export function useEntity(entityId: string) {
  return useQuery({
    queryKey: ["entity", entityId],
    queryFn: () => api<EntityDetail>(`/entities/${entityId}`),
    refetchInterval: (query) => (isActive(query.state.data?.latest_run) ? POLL_MS : false),
  });
}

/**
 * The entity's themes and its listed candidates (emerging topics), in one call. With a rating or
 * period filter or a search, every count covers only the reviews they select.
 */
export function useThemes(
  entityId: string,
  ratingFilter: RatingFilter | null = null,
  periodsFilter: PeriodsFilter | null = null,
  search: string | null = null,
) {
  const params = appendSearch(
    appendRatingFilter(
      appendPeriodsFilter(new URLSearchParams({ include_candidates: "true" }), periodsFilter),
      ratingFilter,
    ),
    search,
  );
  return useQuery({
    queryKey: ["themes", entityId, ratingFilter, periodsFilter, search],
    // The recent window (behind "Most recent", the recent counts, and quiet themes) is the API's default.
    queryFn: () => api<ThemeList>(`/entities/${entityId}/themes?${params}`),
    // Keep showing the last list while a new filter loads, instead of a loading flash.
    placeholderData: keepPreviousData,
  });
}

/** The entity's classified reviews per period on one interval: the reviews-over-time chart. */
export function useReviewPeriods(entityId: string, interval: TrendInterval) {
  return useQuery({
    queryKey: ["review-periods", entityId, interval],
    queryFn: () => api<ReviewPeriods>(`/entities/${entityId}/themes/periods?interval=${interval}`),
    placeholderData: keepPreviousData,
  });
}

/**
 * How each theme changed from one period (compareTo, by default the one before) to another (period).
 * With no period, the API picks the latest period with enough reviews.
 */
export function useThemeChanges(
  entityId: string,
  interval: TrendInterval,
  period: string | null,
  compareTo: string | null,
) {
  const params = new URLSearchParams({ interval });
  if (period) params.set("period", period);
  if (compareTo) params.set("compare_to", compareTo);
  return useQuery({
    queryKey: ["changes", entityId, interval, period, compareTo],
    queryFn: () => api<ThemeChanges>(`/entities/${entityId}/themes/changes?${params}`),
    placeholderData: keepPreviousData,
  });
}

/** Each theme's average rating against the reviews that don't raise it, on one scale. Waits for the scale. */
export function useThemeRatings(entityId: string, scale: RatingScale | undefined) {
  return useQuery({
    queryKey: ["theme-ratings", entityId, scale],
    queryFn: () =>
      api<ThemeRatings>(
        `/entities/${entityId}/themes/ratings?${new URLSearchParams({ rating_scale: JSON.stringify(scale) })}`,
      ),
    enabled: scale !== undefined,
  });
}

/** The entity's ratings: count, average and distribution per rating scale. Changes only on upload. */
export function useRatings(entityId: string) {
  return useQuery({
    queryKey: ["ratings", entityId],
    queryFn: () => api<RatingSummary>(`/entities/${entityId}/ratings`),
  });
}

/** When a run that was being watched finishes, the results it produced replace the cached ones. */
export function useRefreshWhenRunEnds(entityId: string, run: RunDetail | null | undefined) {
  const queryClient = useQueryClient();
  const wasActive = useRef(false);
  useEffect(() => {
    const active = isActive(run);
    if (wasActive.current && !active) {
      for (const key of ["themes", "theme-reviews", "trend", "changes", "theme-ratings", "review-periods", "review-facets", "reviews"]) {
        queryClient.invalidateQueries({ queryKey: [key, entityId] });
      }
      queryClient.invalidateQueries({ queryKey: ["entities"] });
    }
    wasActive.current = active;
  }, [entityId, run, queryClient]);
}

/** The All reviews page's counts by theme, sentiment and rating, for its filters (an API query string). */
export function useReviewFacets(entityId: string, query: string) {
  return useQuery({
    queryKey: ["review-facets", entityId, query],
    queryFn: () => api<ReviewFacets>(`/entities/${entityId}/reviews/facets?${query}`),
    placeholderData: keepPreviousData,
  });
}
