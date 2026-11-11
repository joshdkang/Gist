"use client";

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import type {
  EntityDetail,
  RatingFilter,
  RatingSummary,
  RunDetail,
  ThemeChanges,
  ThemeList,
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
 * The entity's themes and its listed candidates (emerging topics), in one call. With a rating
 * filter, every count covers only the reviews in the chosen rating buckets.
 */
export function useThemes(entityId: string, ratingFilter: RatingFilter | null = null) {
  const params = new URLSearchParams({ include_candidates: "true" });
  if (ratingFilter) {
    params.set("rating_scale", JSON.stringify(ratingFilter.scale));
    for (const label of ratingFilter.labels) params.append("rating", label);
  }
  return useQuery({
    queryKey: ["themes", entityId, ratingFilter],
    // The recent window (behind "Most recent", the recent counts, and quiet themes) is the API's default.
    queryFn: () => api<ThemeList>(`/entities/${entityId}/themes?${params}`),
    // Keep showing the last list while a new filter loads, instead of a loading flash.
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
      for (const key of ["themes", "theme-reviews", "trend", "changes"]) {
        queryClient.invalidateQueries({ queryKey: [key, entityId] });
      }
      queryClient.invalidateQueries({ queryKey: ["entities"] });
    }
    wasActive.current = active;
  }, [entityId, run, queryClient]);
}
