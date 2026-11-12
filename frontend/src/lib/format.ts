import type { Sentiment } from "@/lib/types";

export const formatNumber = (value: number) => value.toLocaleString("en-US");

export function formatPercent(part: number, whole: number): string {
  if (!whole) return "0%";
  const percent = (part * 100) / whole;
  return percent > 0 && percent < 1 ? "<1%" : `${Math.round(percent)}%`;
}

/** A share as a whole percentage: "6%", "<1%" for a share above zero that rounds to 0. */
export function formatShare(value: number): string {
  const rounded = Math.round(value * 100);
  return value > 0 && rounded === 0 ? "<1%" : `${rounded}%`;
}

/** The difference between two shares in percentage points, signed: "+6 pts", "−3 pts", "0 pts". */
export function formatChange(change: number): string {
  const rounded = Math.round(change * 100);
  if (rounded === 0) return change === 0 ? "0 pts" : `${change > 0 ? "+" : "−"}<1 pt`;
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)} ${Math.abs(rounded) === 1 ? "pt" : "pts"}`;
}

/** The share of a theme's reviews that are negative, or null when it has none in the period. */
export const negativeShare = (counts: { negative: number; total: number }) =>
  counts.total ? counts.negative / counts.total : null;

/** Review dates are stored in UTC; show them as the calendar date they were given. */
export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

/** Tailwind classes per sentiment, in the order bars stack: positive, mixed, neutral, negative. */
export const SENTIMENTS: { key: Sentiment; label: string; bar: string; text: string }[] = [
  { key: "positive", label: "Positive", bar: "bg-pos", text: "text-pos-fg" },
  { key: "mixed", label: "Mixed", bar: "bg-mixed", text: "text-mixed-fg" },
  { key: "neutral", label: "Neutral", bar: "bg-neutral", text: "text-neutral-fg" },
  { key: "negative", label: "Negative", bar: "bg-neg", text: "text-neg-fg" },
];

export const sentimentStyle = (key: Sentiment) => SENTIMENTS.find((sentiment) => sentiment.key === key)!;
