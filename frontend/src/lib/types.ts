// Shapes returned by the API (src/schemas.py).

export type Sentiment = "positive" | "negative" | "mixed" | "neutral";
export type RunStatus = "queued" | "running" | "succeeded" | "failed";
export type StepKind = "discover" | "classify" | "confirm" | "consolidate" | "promote";
export type StepStatus = "running" | "succeeded" | "failed" | "skipped";
export type TrendInterval = "month" | "quarter" | "year";

export interface Run {
  id: number;
  type: string;
  status: RunStatus;
  reviews_processed: number;
  input_tokens: number;
  output_tokens: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface Step {
  id: number;
  kind: StepKind;
  status: StepStatus;
  model: string | null;
  outcome: string | null;
  reviews_processed: number;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunDetail extends Run {
  steps: Step[];
}

export interface Entity {
  id: number;
  name: string;
  description: string | null;
  metadata: Record<string, unknown>;
  is_public: boolean;
  is_owner: boolean;
  review_count: number;
  theme_count: number;
  created_at: string;
}

export interface EntityDetail extends Entity {
  import_count: number;
  latest_run: RunDetail | null;
}

export interface SentimentCounts {
  positive: number;
  negative: number;
  mixed: number;
  neutral: number;
  total: number;
}

export interface ThemeSummary {
  id: number;
  kind: "discovered" | "candidate";
  name: string;
  description: string;
  review_count: number;
  share: number;
  sentiment: SentimentCounts;
  recent_count: number;
  quiet: boolean;
}

export interface ThemeList {
  total_reviews: number;
  classified_reviews: number;
  as_of: string | null;
  recent_days: number;
  general: { review_count: number; share: number };
  themes: ThemeSummary[];
  candidates: ThemeSummary[] | null;
}

export interface ThemeReview {
  id: number;
  title: string | null;
  text: string;
  rating: number | null;
  review_date: string | null;
  sentiment: Sentiment | null;
}

export interface TrendPoint {
  period_start: string;
  /** The theme's reviews in the period, by sentiment. */
  sentiment: SentimentCounts;
  /** The entity's classified reviews in the period: the denominator for the theme's share. */
  period_reviews: number;
}

export interface ThemeTrend {
  theme_id: number;
  interval: string;
  /** Every period in which the entity has reviews, oldest first, including ones without this theme. */
  points: TrendPoint[];
}

export interface ThemeChange {
  id: number;
  kind: "discovered" | "candidate";
  name: string;
  description: string;
  previous: SentimentCounts;
  current: SentimentCounts;
  previous_share: number;
  current_share: number;
  /** Fisher's exact test p-value for the change in share; null when the previous period has no reviews. */
  share_change_p: number | null;
  /** The change in share is significant, corrected for testing every theme (5% false discovery rate). */
  share_change_clear: boolean;
  /** The p-value for the change in the negative share of the theme's reviews; null when a period has none. */
  negative_change_p: number | null;
  /** The change in the negative share is significant, corrected the same way. */
  negative_change_clear: boolean;
}

/** The entity's classified reviews per period, oldest first (periods without reviews left out). */
export interface ReviewPeriods {
  interval: TrendInterval;
  periods: { period_start: string; reviews: number }[];
}

export interface ThemeChanges {
  interval: TrendInterval;
  period_start: string | null;
  previous_start: string | null;
  current_reviews: number;
  previous_reviews: number;
  periods: { period_start: string; reviews: number }[];
  themes: ThemeChange[];
}

export interface ColumnMapping {
  text: string;
  title?: string;
  rating?: string;
  review_date?: string;
  external_id?: string;
  metadata_columns?: string[];
  date_format?: string;
}

export type RatingScale =
  | { type: "numeric"; min: number; max: number }
  | { type: "binary" }
  | { type: "labels"; labels: string[] };

export interface UploadResult {
  import: {
    id: number;
    filename: string | null;
    rows_received: number;
    rows_inserted: number;
    rows_skipped: number;
  };
  analysis: { run: Run | null; reused: boolean; message: string | null };
}

export interface RatingScaleSummary {
  scale: RatingScale;
  count: number;
  /** The number itself on a numeric scale, the share positive on binary, the mean label index on labels. */
  average: number;
  /** Highest rating first. */
  buckets: { label: string; count: number }[];
}

export interface ThemeRatingImpact {
  id: number;
  kind: "discovered" | "candidate";
  name: string;
  /** Rated reviews (on the scale) that raise the theme. */
  rated_reviews: number;
  average_with: number;
  /** The other rated reviews' average; null when every rated review raises the theme. */
  average_without: number | null;
  /** How far the theme's reviews move the overall average from what it would be if they were rated like the rest. */
  effect: number;
  p_value: number | null;
  /** Significant once corrected for testing every theme (5% false discovery rate). */
  significant: boolean;
}

export interface ThemeRatings {
  /** The scale compared on; null when no review has a rating. */
  scale: RatingScale | null;
  rated_reviews: number;
  average: number | null;
  /** Most negative effect first. */
  themes: ThemeRatingImpact[];
}

/** Rating buckets chosen on one scale, to count only those reviews. */
export interface RatingFilter {
  scale: RatingScale;
  labels: string[];
}

export interface RatingSummary {
  total_reviews: number;
  rated_reviews: number;
  scales: RatingScaleSummary[];
}

/** A theme a review raises, with the review's sentiment for it. */
export interface ReviewTag {
  id: number;
  name: string;
  kind: "discovered" | "candidate" | "general";
  sentiment: Sentiment | null;
}

/** A review on the All reviews page, with every theme it raises (themes, then emerging topics, then General). */
export interface ReviewListItem extends Omit<ThemeReview, "sentiment"> {
  themes: ReviewTag[];
}

export interface ThemeFacet {
  id: number;
  name: string;
  kind: "discovered" | "candidate" | "general";
  /** Reviews raising the theme under the filters: the other filters with "any", the current results with "all". */
  count: number;
  /** For a chosen theme, its mentions by sentiment under the other filters. */
  sentiments: SentimentCounts | null;
}

export interface ReviewFacets {
  total: number;
  themes: ThemeFacet[];
  ratings: RatingSummary;
  period_interval: TrendInterval;
  /** Reviews per period under every filter but the period, oldest first (periods without reviews left out). */
  periods: { period_start: string; reviews: number }[];
}
