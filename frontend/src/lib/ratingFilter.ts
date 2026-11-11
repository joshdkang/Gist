import type { RatingFilter, RatingScale } from "@/lib/types";

/**
 * A rating filter in a URL: the scale as JSON in `rating_scale` and each chosen bucket in a
 * repeated `rating`. The API takes the same parameters, so page URLs and API calls share one form,
 * and the filter survives opening a theme, the back link, and a reload.
 */
export function appendRatingFilter(params: URLSearchParams, filter: RatingFilter | null): URLSearchParams {
  if (filter) {
    params.set("rating_scale", JSON.stringify(filter.scale));
    for (const label of filter.labels) params.append("rating", label);
  }
  return params;
}

/** The filter in a page's URL, or null when there is none or it can't be read. */
export function readRatingFilter(params: { get(name: string): string | null; getAll(name: string): string[] }) {
  const labels = params.getAll("rating");
  const raw = params.get("rating_scale");
  if (labels.length === 0 || !raw) return null;
  try {
    const scale = JSON.parse(raw) as RatingScale;
    return scale && typeof scale.type === "string" ? ({ scale, labels } satisfies RatingFilter) : null;
  } catch {
    return null;
  }
}

/** "?rating_scale=…&rating=1" for a link that keeps the filter, or "" without one. */
export function ratingQuery(filter: RatingFilter | null): string {
  const params = appendRatingFilter(new URLSearchParams(), filter);
  return params.size > 0 ? `?${params}` : "";
}

/** "rated 5 or 4": the chosen buckets, highest first as the ratings card lists them. */
export function describeRatingFilter(filter: RatingFilter): string {
  const labels = filter.labels.join(" or ");
  return filter.scale.type === "binary" ? labels.toLowerCase() : `rated ${labels}`;
}
