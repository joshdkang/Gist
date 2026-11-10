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

export interface ThemeTrend {
  theme_id: number;
  interval: string;
  points: { period_start: string; sentiment: SentimentCounts }[];
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
