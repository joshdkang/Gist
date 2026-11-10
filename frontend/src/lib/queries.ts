"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import type { EntityDetail, RatingSummary, RunDetail, ThemeList } from "@/lib/types";

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

/** The entity's themes and its listed candidates (emerging topics), in one call. */
export function useThemes(entityId: string) {
  return useQuery({
    queryKey: ["themes", entityId],
    // The recent window (behind "Most recent", the recent counts, and quiet themes) is the API's default.
    queryFn: () => api<ThemeList>(`/entities/${entityId}/themes?include_candidates=true`),
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
      for (const key of ["themes", "theme-reviews", "trend"]) {
        queryClient.invalidateQueries({ queryKey: [key, entityId] });
      }
      queryClient.invalidateQueries({ queryKey: ["entities"] });
    }
    wasActive.current = active;
  }, [entityId, run, queryClient]);
}
